"""Canonical mail content and independent folder memberships."""

import fcntl
from pathlib import Path
import json
import sqlite3
import uuid
import hashlib
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from .catalog_migration import create_locations, migrate


class Catalog:
    def __init__(self, path, *, writer=False):
        self.path = Path(path)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            columns = {r[1] for r in db.execute("PRAGMA table_info(documents)")}
            if columns and "folder" not in columns:
                raise ValueError(
                    "Legacy catalog: use a new RAG_STATE_DIR and workspace for mail indexing"
                )
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
                "CREATE TABLE IF NOT EXISTS messages "
                "(key TEXT PRIMARY KEY, digest TEXT, paths TEXT, synthetic INTEGER, "
                "metadata TEXT, phase TEXT, UNIQUE(digest,synthetic))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS message_aliases "
                "(key TEXT PRIMARY KEY, message_key TEXT REFERENCES messages(key))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS duplicate_cleanup "
                "(key TEXT PRIMARY KEY, message_key TEXT, paths TEXT, phase TEXT)"
            )
            db.execute("CREATE TABLE IF NOT EXISTS orphan_marks (key TEXT PRIMARY KEY)")
            if not columns:
                create_locations(db)
            elif "message_key" not in columns:
                if writer:
                    self._migrate(db)
                else:
                    with self.path.with_name("daemon.lock").open("a") as lock:
                        try:
                            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError:
                            raise ValueError(
                                "Stop the older indexer before migrating this catalog"
                            ) from None
                        self._migrate(db)
            db.execute(
                "CREATE TABLE IF NOT EXISTS folder_inventory "
                "(folder TEXT PRIMARY KEY, validity TEXT, total INTEGER, fingerprint TEXT, "
                "observed_at TEXT, successful INTEGER)"
            )

            db.execute(
                "CREATE TABLE IF NOT EXISTS message_dates (key TEXT PRIMARY KEY, day TEXT, sent_at REAL)"
            )
            db.execute("CREATE INDEX IF NOT EXISTS message_date_time ON message_dates(sent_at)")
            missing = db.execute(
                "SELECT key,metadata FROM messages WHERE key NOT IN (SELECT key FROM message_dates)"
            ).fetchall()
            db.executemany(
                "INSERT INTO message_dates VALUES (?,?,?)",
                [(r["key"], *self._date(json.loads(r["metadata"]))) for r in missing],
            )

    @staticmethod
    def _date(metadata):
        try:
            date = parsedate_to_datetime(metadata.get("date", ""))
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            return date.date().isoformat(), date.timestamp()
        except (ValueError, TypeError, OverflowError):
            return None, None

    def _migrate(self, db):
        backup = self.path.with_name(self.path.stem + ".pre-dedup.db")
        if not backup.exists():
            try:
                with backup.open("xb"):
                    backup.chmod(0o600)
                with self.connect() as source, sqlite3.connect(backup) as target:
                    source.backup(target)
            except BaseException:
                backup.unlink(missing_ok=True)
                raise
        migrate(db)

    def canonical_key(self, key):
        with self.connect() as db:
            row = db.execute(
                "SELECT message_key FROM message_aliases WHERE key=?", (key,)
            ).fetchone()
            return row[0] if row else key

    def content(self, digest, synthetic=False):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM messages WHERE digest=? AND synthetic=?", (digest, int(synthetic))
            ).fetchone()
            return dict(row) if row else None

    def store_text(self, key, text, skipped, truncated):
        key = self.canonical_key(key)
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO message_text VALUES (?,?,?,?)",
                (key, text, json.dumps(skipped), None if truncated is None else int(truncated)),
            )

    def get_text(self, key):
        key = self.canonical_key(key)
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
            folders = json.loads(scope[0]) if scope else list(inventories)
            if not folders and not scope:
                folders = [
                    r[0]
                    for r in db.execute("SELECT DISTINCT folder FROM documents ORDER BY folder")
                ]
            selected = """WITH selected AS (
                SELECT d.* FROM documents d LEFT JOIN folder_inventory i ON i.folder=d.folder
                WHERE d.folder IN (SELECT value FROM json_each(?))
                AND (i.folder IS NULL OR d.validity=i.validity)) """
            params = (json.dumps(folders),)
            counts = db.execute(
                selected
                + """SELECT COUNT(*) AS entries,
                COUNT(DISTINCT d.message_key) AS unique_count,
                COUNT(t.key) AS cached,COUNT(DISTINCT t.key) AS unique_cached,
                MIN(md.day) AS earliest,MAX(md.day) AS latest
                FROM selected d LEFT JOIN message_text t ON t.key=d.message_key
                LEFT JOIN message_dates md ON md.key=d.message_key WHERE d.active=1""",
                params,
            ).fetchone()
            folder_counts = {
                r["folder"]: r["count"]
                for r in db.execute(
                    selected
                    + "SELECT folder,COUNT(*) AS count FROM selected WHERE active=1 GROUP BY folder",
                    params,
                )
            }
            pending = db.execute(
                selected + "SELECT COUNT(*) FROM selected WHERE active=0", params
            ).fetchone()[0]
            retained = db.execute("SELECT COUNT(*) FROM documents WHERE active=1").fetchone()[0]
            unresolved = db.execute(
                "SELECT COUNT(*) FROM messages m WHERE m.phase='uploading' "
                "AND NOT EXISTS (SELECT 1 FROM documents d WHERE d.message_key=m.key)"
            ).fetchone()[0]
            cleanup_pending = db.execute("SELECT COUNT(*) FROM duplicate_cleanup").fetchone()[0]
            index_status = {}
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='index_binding'").fetchone():
                binding = db.execute("SELECT binding FROM index_binding WHERE id=1").fetchone()
                if binding:
                    from .embeddings import EmbeddingProfile

                    saved = json.loads(binding[0])
                    ready = db.execute(
                        "SELECT COUNT(*) FROM index_messages WHERE phase='ready'"
                    ).fetchone()[0]
                    pending_index = db.execute(
                        "SELECT COUNT(*) FROM index_messages WHERE phase!='ready'"
                    ).fetchone()[0]
                    index_status = {
                        "index_backend": saved["backend"],
                        "index_ready_messages": ready,
                        "index_pending_messages": pending_index,
                        "embedding_profile": EmbeddingProfile(**saved["profile"]).identity,
                    }
        unique, cached = counts["unique_count"], counts["cached"]
        reports = []
        for folder in folders:
            inv = inventories.get(folder)
            count = folder_counts.get(folder, 0)
            reports.append(
                {
                    "folder": folder,
                    "expected": inv["total"] if inv else None,
                    "indexed": count,
                    "last_inventory_at": inv["observed_at"] if inv else None,
                    "complete": bool(inv and inv["successful"] and count == inv["total"]),
                }
            )
        known = bool(scope) and all(r["expected"] is not None for r in reports)
        return {
            **index_status,
            "runtime": json.loads(runtime[0]) if runtime else {"state": "unknown"},
            "coverage_complete": known and all(r["complete"] for r in reports),
            "folder_entries_expected": sum(r["expected"] for r in reports) if known else None,
            "folder_entries_indexed": counts["entries"],
            "unique_messages_indexed": unique,
            "unique_full_text_available": counts["unique_cached"],
            "shared_folder_entries": counts["entries"] - unique,
            "pending_duplicate_cleanup": cleanup_pending,
            "unresolved_orphan_uploads": unresolved,
            "unique_messages_expected": unique
            if known and all(r["complete"] for r in reports)
            else None,
            "full_text_available": cached,
            "pending_entries": pending,
            "retained_entries_outside_scope": retained - counts["entries"],
            "folders": reports,
            "indexed_date_range": {"earliest": counts["earliest"], "latest": counts["latest"]}
            if counts["earliest"]
            else None,
            "notice": "Coverage is the last observed inventory, not a guarantee of exhaustive search. "
            "Folder entries count locations; unique messages count content hashes. "
            "The unique mailbox total is unknown until content is read. Missing results do not prove that mail does not exist; "
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

    @staticmethod
    def _joined():
        return (
            "SELECT d.*,m.paths,m.metadata,m.phase FROM documents d "
            "JOIN messages m ON d.message_key=m.key"
        )

    def rows(self, folder=None):
        with self.connect() as db:
            query = self._joined()
            params = ()
            if folder is not None:
                query += " WHERE d.folder=?"
                params = (folder,)
            return {r["key"]: dict(r) for r in db.execute(query, params)}

    def locations(self, keys):
        """Hydrate only selected IDs (including historical aliases)."""
        groups, aliases = {}, {}
        keys = list(set(keys))
        with self.connect() as db:
            for offset in range(0, len(keys), 400):
                batch = keys[offset : offset + 400]
                marks = ",".join("?" for _ in batch)
                mapped = {
                    r["key"]: r["message_key"]
                    for r in db.execute(
                        f"SELECT * FROM message_aliases WHERE key IN ({marks})", batch
                    )
                }
                aliases.update({key: mapped.get(key, key) for key in batch})
                canonical = list(set(aliases[key] for key in batch))
                placeholders = ",".join("?" for _ in canonical)
                for row in db.execute(
                    self._joined() + f" WHERE d.active=1 AND d.message_key IN ({placeholders})",
                    canonical,
                ):
                    groups.setdefault(row["message_key"], []).append(dict(row))
        return groups, aliases

    def active_keys(self, keys):
        return set(self.locations(keys)[0])

    def message(self, key=None, identity=None):
        with self.connect() as db:
            if key is not None:
                row = db.execute(self._joined() + " WHERE d.key=?", (key,)).fetchone()
                if row is None:
                    row = db.execute(
                        self._joined() + " WHERE d.message_key=? AND d.active=1 "
                        "ORDER BY d.folder,d.uid LIMIT 1",
                        (self.canonical_key(key),),
                    ).fetchone()
            else:
                row = db.execute(
                    self._joined()
                    + " WHERE d.folder=? AND d.validity=? AND d.uid=? AND d.digest=?",
                    identity,
                ).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["location_key"] = result["key"]
            result["key"] = result["message_key"]
            return result

    def intent(self, key, folder, validity, uid, digest, synthetic, metadata):
        with self.connect() as db:
            existing = db.execute(
                "SELECT key FROM messages WHERE digest=? AND synthetic=?", (digest, int(synthetic))
            ).fetchone()
            canonical = existing[0] if existing else key
            db.execute(
                "INSERT OR IGNORE INTO messages VALUES (?,?,'[]',?,?,'prepared')",
                (canonical, digest, int(synthetic), json.dumps(metadata)),
            )
            db.execute(
                "INSERT OR IGNORE INTO documents VALUES (?,?,?,?,?,?,0,?)",
                (key, folder, validity, uid, digest, canonical, int(synthetic)),
            )
            db.execute("INSERT OR IGNORE INTO message_aliases VALUES (?,?)", (key, canonical))
            db.execute("DELETE FROM orphan_marks WHERE key=?", (canonical,))
            db.execute(
                "INSERT OR IGNORE INTO message_dates VALUES (?,?,?)",
                (canonical, *self._date(metadata)),
            )
        return canonical

    def dispatched(self, key):
        with self.connect() as db:
            db.execute(
                "UPDATE messages SET phase='uploading' WHERE key=?", (self.canonical_key(key),)
            )

    def activate(self, key, paths):
        with self.connect() as db:
            db.execute(
                "UPDATE messages SET paths=?,phase='active' WHERE key=?",
                (json.dumps(paths), self.canonical_key(key)),
            )
            db.execute("UPDATE documents SET active=1 WHERE key=?", (key,))

    def retire(self, key):
        with self.connect() as db:
            db.execute("UPDATE documents SET active=0 WHERE key=?", (key,))

    def forget(self, key, *, retain_message=False):
        canonical = self.canonical_key(key)
        with self.connect() as db:
            db.execute("DELETE FROM documents WHERE key=?", (key,))
            references = db.execute(
                "SELECT 1 FROM documents WHERE message_key=?", (canonical,)
            ).fetchone()
            if not references and not retain_message:
                self._forget_content(db, canonical)

    @staticmethod
    def _forget_content(db, key):
        db.execute("DELETE FROM orphan_marks WHERE key=?", (key,))
        db.execute("DELETE FROM message_text WHERE key=?", (key,))
        db.execute("DELETE FROM message_aliases WHERE message_key=?", (key,))
        db.execute("DELETE FROM message_dates WHERE key=?", (key,))
        db.execute("DELETE FROM messages WHERE key=?", (key,))

    def orphan_messages(self):
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM messages m WHERE NOT EXISTS "
                    "(SELECT 1 FROM documents d WHERE d.message_key=m.key)"
                )
            ]

    def mark_deleting(self, key):
        with self.connect() as db:
            db.execute("UPDATE messages SET phase='deleting' WHERE key=?", (key,))

    def seen_orphan(self, key):
        with self.connect() as db:
            seen = db.execute("SELECT 1 FROM orphan_marks WHERE key=?", (key,)).fetchone()
            db.execute("INSERT OR IGNORE INTO orphan_marks VALUES (?)", (key,))
            return bool(seen)

    def forget_content(self, key):
        with self.connect() as db:
            if db.execute("SELECT 1 FROM documents WHERE message_key=?", (key,)).fetchone():
                raise ValueError("Cannot delete referenced message content")
            self._forget_content(db, key)

    def cleanup_queue(self, limit):
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT d.*,m.paths AS canonical_paths FROM duplicate_cleanup d "
                    "LEFT JOIN messages m ON d.message_key=m.key ORDER BY d.key LIMIT ?",
                    (limit,),
                )
            ]

    def cleanup_finished(self, keys):
        with self.connect() as db:
            db.executemany("DELETE FROM duplicate_cleanup WHERE key=?", [(key,) for key in keys])
