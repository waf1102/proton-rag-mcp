"""Resumable migration from a frozen private export into an isolated Qdrant catalog."""

import argparse
from collections import defaultdict
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from .catalog import Catalog
from .config import Settings
from .embeddings import EmbeddingProfile, Ollama, validate_vector
from .index_state import IndexState
from .qdrant import QdrantIndex, ImportedChunk
from .migration_report import fingerprint, readonly, save_report


def clone_catalog(source, target):
    target = Path(target)
    if target.exists():
        raise ValueError("Target catalog already exists")
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with target.open("xb"):
        target.chmod(0o600)
    try:
        with readonly(source) as src, sqlite3.connect(target) as dst:
            src.backup(dst)
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def write_export(path, rows, *, profile):
    """Write the same integrity envelope as the one-time frozen Lance reader."""
    path = Path(path)
    digest, count = hashlib.sha256(), 0
    with path.open("w") as file:
        path.chmod(0o600)
        file.write(json.dumps({"type": "header", "format": 1, "profile": asdict(profile)}) + "\n")
        for row in rows:
            line = (
                json.dumps({"type": "chunk", **row}, separators=(",", ":"), ensure_ascii=False)
                + "\n"
            )
            digest.update(line.encode())
            file.write(line)
            count += 1
        file.write(
            json.dumps({"type": "footer", "rows": count, "sha256": digest.hexdigest()}) + "\n"
        )


class ExportRows:
    """Disk-backed rows; retain only byte offsets, never the vector corpus."""

    def __init__(self, path, offsets):
        self.path, self.offsets = Path(path), offsets

    def __len__(self):
        return len(self.offsets)

    def __getitem__(self, index):
        with self.path.open("rb") as file:
            file.seek(self.offsets[index])
            return json.loads(file.readline())

    def __iter__(self):
        with self.path.open("rb") as file:
            for offset in self.offsets:
                file.seek(offset)
                yield json.loads(file.readline())


def read_export(path):
    offsets, ids, digest, footer = [], set(), hashlib.sha256(), None
    with Path(path).open("rb") as file:
        header = json.loads(file.readline())
        if header.get("type") != "header" or header.get("format") != 1:
            raise ValueError("Unsupported migration export")
        while True:
            offset = file.tell()
            line = file.readline()
            if not line:
                break
            row = json.loads(line)
            if row.get("type") == "footer":
                footer = row
                if file.read().strip():
                    raise ValueError("Trailing export data")
                break
            if row.get("type") != "chunk" or row.get("id") in ids:
                raise ValueError("Invalid or duplicate exported chunk identity")
            ids.add(row["id"])
            digest.update(line)
            offsets.append(offset)
    if not footer or footer["rows"] != len(offsets) or footer["sha256"] != digest.hexdigest():
        raise ValueError("Incomplete or corrupt migration export")
    return header, ExportRows(path, offsets), footer


def audit(source_catalog, export_path, *, backend_database=None):
    header, rows, footer = read_export(export_path)
    with readonly(source_catalog) as db:
        keys = {r[0] for r in db.execute("SELECT key FROM messages")}
        aliases = dict(db.execute("SELECT key,message_key FROM message_aliases"))
        cached = dict(db.execute("SELECT key,text FROM message_text"))
        active = {
            r[0] for r in db.execute("SELECT DISTINCT message_key FROM documents WHERE active=1")
        }
        paths = {r[0]: json.loads(r[1]) for r in db.execute("SELECT key,paths FROM messages")}
    groups, alias_chunks, unknown = defaultdict(list), 0, 0
    profile = EmbeddingProfile(**header["profile"])
    for index, row in enumerate(rows):
        source = row.get("docSource")
        key = aliases.get(source, source)
        if key not in keys:
            unknown += 1
            continue
        alias_chunks += source != key
        validate_vector(row["vector"], profile.dimension)
        if not isinstance(row.get("text"), str) or not isinstance(row["id"], str):
            raise ValueError("Invalid chunk export")
        groups[key].append(index)
    if unknown:
        raise ValueError("Export has unexplained source identities")
    if active - set(cached):
        raise ValueError("Active messages are missing cached text")
    incomplete = set()
    if backend_database:
        # Compare source table row identities against AnythingLLM's durable document mapping.
        expected_by_path = defaultdict(set)
        with readonly(backend_database) as db:
            for path, vector_id in db.execute(
                "SELECT w.docpath,v.vectorId FROM workspace_documents w JOIN document_vectors v ON v.docId=w.docId"
            ):
                expected_by_path[path].add(vector_id)
        for key in keys:
            expected = set().union(*(expected_by_path.get(p, set()) for p in paths[key]))
            actual = {rows[i]["id"] for i in groups[key]}
            if expected and not expected.issubset(actual):
                incomplete.add(key)
    return {
        "header": header,
        "groups": dict(groups),
        "rows": rows,
        "cached": cached,
        "keys": keys,
        "active": active,
        "incomplete": incomplete,
        "export_sha256": footer["sha256"],
        "export_rows": len(rows),
        "alias_chunks": alias_chunks,
    }


def verify_embeddings(export_path, embedder, samples=8):
    header, rows, footer = read_export(export_path)
    if EmbeddingProfile(**header["profile"]) != embedder.profile:
        raise ValueError("Export embedding profile mismatch")
    embedder.check_profile()
    if not rows:
        return {"compatible": True, "samples": 0, "export_sha256": footer["sha256"]}
    # Spread samples across the frozen table instead of trusting the first document.
    indices = sorted(
        {i * (len(rows) - 1) // max(samples - 1, 1) for i in range(min(samples, len(rows)))}
    )
    for index in indices:
        row = rows[index]
        try:
            vector = embedder.embed_documents([row["text"]])[0]
        except Exception:
            return {"compatible": False, "samples": len(indices), "export_sha256": footer["sha256"]}
        old = row["vector"]
        norm = (sum(x * x for x in vector) * sum(x * x for x in old)) ** 0.5
        cosine = sum(a * b for a, b in zip(vector, old, strict=True)) / norm
        if cosine < 0.99999:
            return {"compatible": False, "samples": len(indices), "export_sha256": footer["sha256"]}
    return {"compatible": True, "samples": len(indices), "export_sha256": footer["sha256"]}


def import_index(
    source_catalog,
    export_path,
    target_state,
    backend,
    resume=True,
    *,
    backend_database=None,
    reuse_verified=True,
):
    source_catalog, target_state = Path(source_catalog).resolve(), Path(target_state).resolve()
    if (
        source_catalog == backend.catalog.path.resolve()
        or target_state != backend.catalog.path.parent.resolve()
    ):
        raise ValueError("Migration requires a separate target catalog")
    data = audit(source_catalog, export_path, backend_database=backend_database)
    if EmbeddingProfile(**data["header"]["profile"]) != backend.state.profile:
        raise ValueError("Export embedding profile mismatch")
    source_hash = fingerprint(source_catalog)
    if source_hash != fingerprint(backend.catalog.path):
        raise ValueError("Target catalog content differs from frozen source")
    identity = json.dumps(
        {
            "export": data["export_sha256"],
            "catalog": source_hash,
            "profile": backend.state.profile.identity,
            "reuse": reuse_verified,
        },
        sort_keys=True,
    )
    with backend.catalog.connect() as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS migration_source (id INTEGER PRIMARY KEY CHECK(id=1), identity TEXT)"
        )
        saved = db.execute("SELECT identity FROM migration_source WHERE id=1").fetchone()
        if saved and (saved[0] != identity or not resume):
            raise ValueError("Migration source changed; use a fresh target")
        db.execute("INSERT OR IGNORE INTO migration_source VALUES (1,?)", (identity,))
    report = {
        "export_rows": data["export_rows"],
        "alias_chunks": data["alias_chunks"],
        "ready_messages": 0,
        "rebuilt_messages": 0,
        "skipped_messages": 0,
        "pending_records": 0,
        "imported_chunks": 0,
        "export_sha256": data["export_sha256"],
    }
    for key in sorted(data["keys"]):
        text = data["cached"].get(key)
        if text is None:
            report["pending_records"] += 1
            continue
        if backend.state.ready(key):
            backend.verify_message(key)
            report["skipped_messages"] += 1
        else:
            rows = [data["rows"][i] for i in data["groups"].get(key, [])]
            if reuse_verified and rows and key not in data["incomplete"]:
                backend.import_message(
                    key, text, [ImportedChunk(r["id"], r["text"], r["vector"]) for r in rows]
                )
                report["imported_chunks"] += len(rows)
            else:
                backend.ensure(key, text)
                report["rebuilt_messages"] += bool(text)
        report["ready_messages"] += 1
        if report["ready_messages"] % 100 == 0:
            save_report(target_state / "migration-progress.json", report)
            print(
                json.dumps(
                    {"event": "migration_progress", "ready_messages": report["ready_messages"]}
                ),
                flush=True,
            )
    save_report(target_state / "migration-progress.json", report)
    return report


def verify_target(source_catalog, target_catalog, backend):
    if Path(source_catalog).resolve() == Path(target_catalog).resolve():
        raise ValueError("Source and target must differ")
    source_hash = fingerprint(source_catalog)
    if source_hash != fingerprint(target_catalog):
        raise ValueError("Migrated catalog fingerprint differs from source")
    with readonly(source_catalog) as db:
        messages = list(
            db.execute("SELECT m.key,t.text FROM messages m JOIN message_text t ON t.key=m.key")
        )
        active_without_text = db.execute(
            "SELECT COUNT(*) FROM documents d LEFT JOIN message_text t ON d.message_key=t.key WHERE d.active=1 AND t.key IS NULL"
        ).fetchone()[0]
    if active_without_text:
        raise ValueError("Active source text is unavailable")
    chunks = 0
    for key, text in messages:
        entry = backend.state.entry(key)
        if (
            not entry
            or entry["phase"] != "ready"
            or entry["text_hash"] != hashlib.sha256(text.encode()).hexdigest()
        ):
            raise ValueError("Target message is not fully ready")
        backend.verify_message(key)
        chunks += len(entry["points"])
    # Exact collection count catches unexplained points not represented by any manifest.
    total = backend.request("POST", "/points/count", {"exact": True})["count"]
    if total != chunks:
        raise ValueError("Target collection has unexplained points")
    return {
        "ready": True,
        "messages": len(messages),
        "chunks": chunks,
        "catalog_fingerprints": source_hash,
    }


def bind_target(source_catalog, target_catalog, backend):
    report = verify_target(source_catalog, target_catalog, backend)
    with backend.catalog.connect() as db:
        for row in db.execute("SELECT key FROM index_messages WHERE phase='ready'").fetchall():
            db.execute("UPDATE messages SET paths=? WHERE key=?", (json.dumps([row[0]]), row[0]))
        # Old duplicate cleanup paths only describe the old backend. Their recovery copy is frozen.
        db.execute("DELETE FROM duplicate_cleanup")
        db.execute(
            "INSERT OR REPLACE INTO catalog_settings VALUES ('workspace',?)", (backend.workspace,)
        )
        db.execute("UPDATE index_binding SET published=1 WHERE id=1")
    return report


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["audit", "import", "verify", "bind", "verify-embeddings"]
    )
    parser.add_argument("--source-catalog", type=Path, required=True)
    parser.add_argument("--export", type=Path, required=True)
    parser.add_argument("--backend-db", type=Path)
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Re-embed from cached text instead of importing vectors",
    )
    parser.add_argument(
        "--reuse-verification",
        type=Path,
        help="Report from verify-embeddings for this exact export",
    )
    args = parser.parse_args()
    try:
        settings = Settings.from_env()
        if args.command == "audit":
            data = audit(args.source_catalog, args.export, backend_database=args.backend_db)
            print(
                json.dumps({k: data[k] for k in ["export_rows", "alias_chunks", "export_sha256"]})
            )
            return
        if args.source_catalog.resolve() == (settings.state_dir / "catalog.db").resolve():
            raise ValueError("Use a separate RAG_STATE_DIR")
        header, _, footer = read_export(args.export)
        profile = EmbeddingProfile(**header["profile"])
        settings.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = settings.state_dir / "catalog.db"
        if not target.exists():
            clone_catalog(args.source_catalog, target)
        catalog = Catalog(target)
        state = IndexState(catalog, settings.workspace, profile, allow_existing=True)
        embedder = Ollama(settings.ollama_url, profile, settings.embedding_timeout)
        if args.command == "verify-embeddings":
            report = verify_embeddings(args.export, embedder)
            save_report(settings.state_dir / "embedding-verification.json", report)
        else:
            backend = QdrantIndex(
                settings, catalog, state, embedder, key=os.environ.get("QDRANT_API_KEY", "")
            )
            backend.bootstrap()
            if args.command == "import":
                reuse = False
                if args.reuse_verification and not args.rebuild:
                    checked = json.loads(args.reuse_verification.read_text())
                    reuse = (
                        checked.get("compatible") is True
                        and checked.get("export_sha256") == footer["sha256"]
                    )
                    if not reuse:
                        raise ValueError("Embedding reuse verification did not pass")
                report = import_index(
                    args.source_catalog,
                    args.export,
                    settings.state_dir,
                    backend,
                    backend_database=args.backend_db,
                    reuse_verified=reuse,
                )
            elif args.command == "verify":
                report = verify_target(args.source_catalog, target, backend)
            else:
                report = bind_target(args.source_catalog, target, backend)
        save_report(settings.state_dir / ("migration-" + args.command + ".json"), report)
        print(json.dumps(report))
    except Exception as error:
        # No library exception message can leak mail, vectors, paths, or credentials.
        raise SystemExit("Migration stopped: " + type(error).__name__) from None


if __name__ == "__main__":
    main()
