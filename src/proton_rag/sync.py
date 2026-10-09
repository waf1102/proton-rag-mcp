"""Folder-aware incremental indexing with durable upload intents."""

import hashlib
import json
import sqlite3
import uuid
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


def synchronize_folder(catalog, backend, mailbox, folder, settings=None, stop=None, progress=None):
    settings = settings or Settings()
    _dataset(catalog, False)
    if hasattr(backend, "workspace"):
        catalog.bind_workspace(backend.workspace)
    inventory = mailbox.inventory(folder)
    existing = catalog.rows(folder)
    active = {
        r["uid"]: key
        for key, r in existing.items()
        if r["validity"] == inventory.validity and r["active"]
    }
    keep, skipped, fetched, failed = set(), 0, 0, 0
    ordered = tuple(sorted(inventory.uids, key=int, reverse=True))
    for offset in range(0, len(ordered), settings.batch_size):
        if stop and stop.is_set():
            raise SnapshotError("Synchronization interrupted")
        for uid in ordered[offset : offset + settings.batch_size]:
            if stop and stop.is_set():
                raise SnapshotError("Synchronization interrupted")
            if uid in active:
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
            except (SnapshotError, PendingUploadError):
                failed += 1
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
    if not failed:
        _remove(catalog, backend, existing, keep)
    return {
        "messages": len(inventory.uids),
        "fetched": fetched,
        "skipped_parts": skipped,
        "failed_messages": failed,
    }
