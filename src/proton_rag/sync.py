"""Folder-aware incremental indexing with durable upload intents."""

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from .extract import extract
from .mailbox import SnapshotError
from .config import Settings
from .anything import PendingUploadError


class Catalog:
    def __init__(self, path):
        self.path = path
        with self.connect() as db:
            columns = {r[1] for r in db.execute("PRAGMA table_info(documents)")}
            if columns and "folder" not in columns:
                raise ValueError(
                    "Legacy catalog: use a new RAG_STATE_DIR and workspace for mail indexing"
                )
            db.execute(
                "CREATE TABLE IF NOT EXISTS documents "
                "(key TEXT PRIMARY KEY, folder TEXT, validity TEXT, uid TEXT, digest TEXT, "
                "paths TEXT, active INTEGER, synthetic INTEGER, metadata TEXT, phase TEXT)"
            )
            db.execute("CREATE INDEX IF NOT EXISTS folder_uids ON documents(folder,validity,uid)")
            db.execute(
                "CREATE TABLE IF NOT EXISTS catalog_settings (name TEXT PRIMARY KEY, value TEXT)"
            )
            db.execute(
                "INSERT OR IGNORE INTO catalog_settings VALUES ('namespace',?)", (uuid.uuid4().hex,)
            )
            self.namespace = db.execute(
                "SELECT value FROM catalog_settings WHERE name='namespace'"
            ).fetchone()[0]
            db.execute(
                "CREATE TABLE IF NOT EXISTS message_text "
                "(key TEXT PRIMARY KEY, text TEXT, skipped TEXT, truncated INTEGER)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS folder_inventory "
                "(folder TEXT PRIMARY KEY, validity TEXT, total INTEGER, fingerprint TEXT, "
                "observed_at TEXT, successful INTEGER)"
            )

    def store_text(self, key, text, skipped, truncated):
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO message_text VALUES (?,?,?,?)",
                (key, text, json.dumps(skipped), None if truncated is None else int(truncated)),
            )

    def get_text(self, key):
        with self.connect() as db:
            row = db.execute("SELECT * FROM message_text WHERE key=?", (key,)).fetchone()
            return dict(row) if row else None

    def set_scope(self, folders):
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO catalog_settings VALUES ('folders',?)",
                (json.dumps(folders),),
            )

    def observe(self, inventory):
        fingerprint = hashlib.sha256(
            json.dumps(sorted(inventory.uids, key=int)).encode()
        ).hexdigest()
        with self.connect() as db:
            db.execute(
                "INSERT INTO folder_inventory VALUES (?,?,?,?,?,0) "
                "ON CONFLICT(folder) DO UPDATE SET validity=excluded.validity, "
                "total=excluded.total,fingerprint=excluded.fingerprint, "
                "observed_at=excluded.observed_at, successful=CASE WHEN "
                "folder_inventory.fingerprint=excluded.fingerprint AND "
                "folder_inventory.validity=excluded.validity THEN folder_inventory.successful "
                "ELSE 0 END",
                (
                    inventory.folder,
                    inventory.validity,
                    len(inventory.uids),
                    fingerprint,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def folder_finished(self, folder, successful):
        with self.connect() as db:
            db.execute(
                "UPDATE folder_inventory SET successful=? WHERE folder=?",
                (int(successful), folder),
            )

    def update_runtime(self, **values):
        with self.connect() as db:
            row = db.execute("SELECT value FROM catalog_settings WHERE name='runtime'").fetchone()
            runtime = json.loads(row[0]) if row else {}
            runtime.update(values, updated_at=datetime.now(timezone.utc).isoformat())
            db.execute(
                "INSERT OR REPLACE INTO catalog_settings VALUES ('runtime',?)",
                (json.dumps(runtime),),
            )

    def coverage(self):
        with self.connect() as db:
            scope = db.execute("SELECT value FROM catalog_settings WHERE name='folders'").fetchone()
            runtime = db.execute(
                "SELECT value FROM catalog_settings WHERE name='runtime'"
            ).fetchone()
            inventories = {
                r["folder"]: dict(r) for r in db.execute("SELECT * FROM folder_inventory")
            }
            rows = [dict(r) for r in db.execute("SELECT * FROM documents WHERE active=1")]
            cached_keys = {r[0] for r in db.execute("SELECT key FROM message_text")}
            pending_rows = [dict(r) for r in db.execute("SELECT * FROM documents WHERE active=0")]
        folders = json.loads(scope[0]) if scope else list(inventories)

        def selected(row):
            inv = inventories.get(row["folder"])
            return row["folder"] in folders and (not inv or row["validity"] == inv["validity"])

        retained = len(rows)
        rows = [r for r in rows if selected(r)]
        cached = sum(r["key"] in cached_keys for r in rows)
        pending = sum(selected(r) for r in pending_rows)
        reports = []
        for folder in folders:
            inv = inventories.get(folder)
            count = sum(
                r["folder"] == folder and (not inv or r["validity"] == inv["validity"])
                for r in rows
            )
            reports.append(
                {
                    "folder": folder,
                    "expected": inv["total"] if inv else None,
                    "indexed": count,
                    "last_inventory_at": inv["observed_at"] if inv else None,
                    "complete": bool(inv and inv["successful"] and count == inv["total"]),
                }
            )
        dates = []
        for row in rows:
            try:
                dates.append(
                    parsedate_to_datetime(json.loads(row["metadata"]).get("date", ""))
                    .date()
                    .isoformat()
                )
            except (ValueError, TypeError, OverflowError):
                pass
        known = bool(scope) and all(r["expected"] is not None for r in reports)
        return {
            "runtime": json.loads(runtime[0]) if runtime else {"state": "unknown"},
            "coverage_complete": known and all(r["complete"] for r in reports),
            "folder_entries_expected": sum(r["expected"] for r in reports) if known else None,
            "folder_entries_indexed": len(rows),
            "full_text_available": cached,
            "pending_entries": pending,
            "retained_entries_outside_scope": retained - len(rows),
            "folders": reports,
            "indexed_date_range": {"earliest": min(dates), "latest": max(dates)} if dates else None,
            "notice": "Coverage is the last observed inventory, not a guarantee of exhaustive search. "
            "Folder counts include copies. Missing results do not prove that mail does not exist; "
            "older mail may still be unindexed. Use read_mail to open search results.",
        }

    def bind_workspace(self, workspace):
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO catalog_settings VALUES ('workspace',?)", (workspace,)
            )
            saved = db.execute(
                "SELECT value FROM catalog_settings WHERE name='workspace'"
            ).fetchone()[0]
            if saved != workspace:
                raise ValueError(
                    "Catalog belongs to another workspace; use a separate state directory"
                )

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def rows(self, folder=None):
        with self.connect() as db:
            query = "SELECT * FROM documents"
            params = ()
            if folder is not None:
                query += " WHERE folder=?"
                params = (folder,)
            return {r["key"]: dict(r) for r in db.execute(query, params)}

    def message(self, key=None, identity=None):
        with self.connect() as db:
            if key is not None:
                row = db.execute("SELECT * FROM documents WHERE key=?", (key,)).fetchone()
            else:
                row = db.execute(
                    "SELECT * FROM documents WHERE folder=? AND validity=? AND uid=? AND digest=?",
                    identity,
                ).fetchone()
            return dict(row) if row else None

    def intent(self, key, folder, validity, uid, digest, synthetic, metadata):
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO documents VALUES (?,?,?,?,?, '[]',0,?,?,'prepared')",
                (key, folder, validity, uid, digest, int(synthetic), json.dumps(metadata)),
            )

    def dispatched(self, key):
        with self.connect() as db:
            db.execute("UPDATE documents SET phase='uploading' WHERE key=?", (key,))

    def activate(self, key, paths):
        with self.connect() as db:
            db.execute(
                "UPDATE documents SET paths=?,active=1,phase='active' WHERE key=?",
                (json.dumps(paths), key),
            )

    def retire(self, key):
        with self.connect() as db:
            db.execute("UPDATE documents SET active=0 WHERE key=?", (key,))

    def forget(self, key):
        with self.connect() as db:
            db.execute("DELETE FROM message_text WHERE key=?", (key,))
            db.execute("DELETE FROM documents WHERE key=?", (key,))


def _ingest(catalog, backend, folder, validity, uid, raw, existing, synthetic, settings):
    digest = hashlib.sha256(raw).hexdigest()
    identity = json.dumps(
        [catalog.namespace, folder, validity, uid, digest], ensure_ascii=False
    ).encode()
    key = "proton-mail-" + hashlib.sha256(identity).hexdigest()
    if existing.get(key, {}).get("active"):
        return key, 0
    parsed = extract(raw, settings=settings) if settings else extract(raw)
    if set(parsed["skipped"]) & {"parse_failed", "parse_timeout_or_resource_limit"}:
        raise SnapshotError("Parser failed; preserve prior retrieval data")
    catalog.intent(key, folder, validity, uid, digest, synthetic, parsed.get("metadata", {}))
    catalog.store_text(key, parsed["text"], parsed["skipped"], parsed.get("text_truncated", False))
    ingest = (
        backend.recover
        if existing.get(key, {}).get("phase") == "uploading" and hasattr(backend, "recover")
        else backend.ensure
    )
    paths = (
        ingest(key, parsed["text"], before_upload=lambda: catalog.dispatched(key))
        if parsed["text"]
        else []
    )
    catalog.activate(key, paths)
    return key, len(parsed["skipped"])


def _remove(catalog, backend, rows, keep):
    for key, row in rows.items():
        if key in keep:
            continue
        catalog.retire(key)
        paths = json.loads(row["paths"])
        if hasattr(backend, "find"):
            paths = list(set(paths + backend.find(key)))
        backend.remove(paths)
        catalog.forget(key)


def _dataset(catalog, synthetic):
    if any(bool(row["synthetic"]) != synthetic for row in catalog.rows().values()):
        raise SnapshotError("Use separate catalogs for fixtures and real mail")


def synchronize(catalog, backend, snapshot, synthetic=False):
    """Small explicit snapshots for fixtures and import tests."""
    if not snapshot.complete:
        raise SnapshotError("Incomplete snapshot cannot synchronize")
    if not snapshot.validity.isdigit() or any(not uid.isdigit() for uid in snapshot.messages):
        raise SnapshotError("Invalid snapshot identities")
    _dataset(catalog, synthetic)
    if hasattr(backend, "workspace"):
        catalog.bind_workspace(backend.workspace)
    existing = catalog.rows(snapshot.folder)
    keep, skipped = set(), 0
    for uid, raw in snapshot.messages.items():
        key, count = _ingest(
            catalog,
            backend,
            snapshot.folder,
            snapshot.validity,
            uid,
            raw,
            existing,
            synthetic,
            None,
        )
        keep.add(key)
        skipped += count
    _remove(catalog, backend, existing, keep)
    return {"messages": len(keep), "skipped_parts": skipped}


def synchronize_folder(
    catalog,
    backend,
    mailbox,
    folder,
    settings=None,
    stop=None,
    progress=None,
    before_batch=None,
    on_error=None,
):
    settings = settings or Settings()
    _dataset(catalog, False)
    if hasattr(backend, "workspace"):
        catalog.bind_workspace(backend.workspace)
    inventory = mailbox.inventory(folder)
    catalog.observe(inventory)
    existing = catalog.rows(folder)
    active = {
        r["uid"]: key
        for key, r in existing.items()
        if r["validity"] == inventory.validity and r["active"]
    }
    keep, skipped, fetched, failed = set(), 0, 0, 0
    ordered = tuple(sorted(inventory.uids, key=int, reverse=True))
    for offset in range(0, len(ordered), settings.batch_size):
        if before_batch:
            before_batch()
        if stop and stop.is_set():
            raise SnapshotError("Synchronization interrupted")
        for uid in ordered[offset : offset + settings.batch_size]:
            if stop and stop.is_set():
                raise SnapshotError("Synchronization interrupted")
            if uid in active:
                key = active[uid]
                if catalog.get_text(key) is None:
                    raw = mailbox.fetch(uid)
                    if raw is None:
                        failed += 1
                        skipped += 1
                    else:
                        if hashlib.sha256(raw).hexdigest() != existing[key]["digest"]:
                            raise SnapshotError("Message content changed during text backfill")
                        parsed = extract(raw, settings=settings)
                        if set(parsed["skipped"]) & {
                            "parse_failed",
                            "parse_timeout_or_resource_limit",
                        }:
                            failed += 1
                        else:
                            catalog.store_text(
                                key,
                                parsed["text"],
                                parsed["skipped"],
                                parsed.get("text_truncated", False),
                            )
                keep.add(active[uid])
                continue
            raw = mailbox.fetch(uid)
            if raw is None:
                skipped += 1
                # Keep any earlier version while its body cannot be fetched.
                keep.update(
                    k
                    for k, r in existing.items()
                    if r["uid"] == uid and r["validity"] == inventory.validity
                )
                continue
            try:
                key, count = _ingest(
                    catalog,
                    backend,
                    folder,
                    inventory.validity,
                    uid,
                    raw,
                    existing,
                    False,
                    settings,
                )
            except (SnapshotError, PendingUploadError) as error:
                failed += 1
                if on_error:
                    on_error(error)
                continue
            keep.add(key)
            skipped += count
            fetched += 1
        if progress:
            progress(
                {
                    "processed": min(offset + settings.batch_size, len(ordered)),
                    "total": len(ordered),
                    "fetched": fetched,
                    "failed_messages": failed,
                }
            )
    mailbox.verify(inventory)
    if before_batch:
        before_batch()
    if not failed:
        _remove(catalog, backend, existing, keep)
    catalog.folder_finished(folder, successful=not failed)
    return {
        "messages": len(inventory.uids),
        "fetched": fetched,
        "skipped_parts": skipped,
        "failed_messages": failed,
    }
