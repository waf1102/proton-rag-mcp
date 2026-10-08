"""Durable intent journal plus stable document identities; single writer enforced by daemon."""

import hashlib
import json
import sqlite3
from .extract import extract
from .mailbox import SnapshotError


class Catalog:
    def __init__(self, path):
        self.path = path
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS documents "
                "(key TEXT PRIMARY KEY, validity TEXT, uid TEXT, digest TEXT, "
                "paths TEXT, active INTEGER, synthetic INTEGER)"
            )

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def rows(self):
        with self.connect() as db:
            return {r["key"]: dict(r) for r in db.execute("SELECT * FROM documents")}

    def intent(self, key, validity, uid, digest, synthetic):
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO documents VALUES (?,?,?,?,?,0,?)",
                (key, validity, uid, digest, "[]", int(synthetic)),
            )

    def activate(self, key, paths):
        with self.connect() as db:
            db.execute(
                "UPDATE documents SET paths=?,active=1 WHERE key=?", (json.dumps(paths), key)
            )

    def retire(self, key):
        with self.connect() as db:
            db.execute("UPDATE documents SET active=0 WHERE key=?", (key,))

    def forget(self, key):
        with self.connect() as db:
            db.execute("DELETE FROM documents WHERE key=?", (key,))


def synchronize(catalog, backend, snapshot, synthetic=False):
    if not snapshot.complete:
        raise SnapshotError("Incomplete snapshot cannot synchronize")
    if not snapshot.validity.isdigit() or any(not uid.isdigit() for uid in snapshot.messages):
        raise SnapshotError("Invalid snapshot identities")
    existing = catalog.rows()
    if any(bool(row["synthetic"]) != synthetic for row in existing.values()):
        raise SnapshotError("Refuse synthetic/private dataset mixing")
    keep = set()
    skipped = 0
    for uid, raw in snapshot.messages.items():
        digest = hashlib.sha256(raw).hexdigest()
        key = f"proton-test-{snapshot.validity}-{uid}-{digest}"
        keep.add(key)
        if existing.get(key, {}).get("active"):
            continue
        parsed = extract(raw)
        skipped += len(parsed["skipped"])
        if set(parsed["skipped"]) & {"parse_failed", "parse_timeout_or_resource_limit"}:
            raise SnapshotError("Parser failed; preserve prior retrieval data")
        # Journal only after extraction succeeds; parsing failures cannot dispatch uploads.
        catalog.intent(key, snapshot.validity, uid, digest, synthetic)
        ingest = (
            backend.recover if key in existing and hasattr(backend, "recover") else backend.ensure
        )
        paths = ingest(key, parsed["text"]) if parsed["text"] else []
        catalog.activate(key, paths)
    # Never run this after any failed addition, parse process crash or IMAP exception.
    for key, row in existing.items():
        if key in keep:
            continue
        catalog.retire(key)  # Hide before remote deletion; retry tombstone after crash.
        paths = json.loads(row["paths"])
        if hasattr(backend, "find"):
            paths = list(set(paths + backend.find(key)))
        backend.remove(paths)
        catalog.forget(key)
    return {"messages": len(keep), "skipped_parts": skipped}
