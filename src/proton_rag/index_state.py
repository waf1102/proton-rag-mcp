"""SQLite commit records for cross-store index writes and immutable bindings."""

from dataclasses import asdict
import hashlib
import json
from .embeddings import EmbeddingProfile


def text_hash(text):
    return hashlib.sha256(text.encode()).hexdigest()


class IndexState:
    def __init__(self, catalog, collection, profile, *, allow_existing=False):
        self.catalog, self.collection, self.profile = catalog, collection, profile
        self.binding = {
            "backend": "qdrant",
            "namespace": catalog.namespace,
            "collection": collection,
            "profile": asdict(profile),
        }
        with catalog.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS index_binding (id INTEGER PRIMARY KEY CHECK(id=1), binding TEXT, published INTEGER)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS index_messages (key TEXT PRIMARY KEY, text_hash TEXT, points TEXT, phase TEXT, revision TEXT)"
            )
            saved = db.execute("SELECT binding,published FROM index_binding WHERE id=1").fetchone()
            if saved:
                if json.loads(saved["binding"]) != self.binding:
                    raise ValueError(
                        "Index binding differs from catalog; migrate into a separate target"
                    )
            else:
                nonempty = db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
                if nonempty and not allow_existing:
                    raise ValueError(
                        "Existing catalog requires verified index migration and binding"
                    )
                db.execute(
                    "INSERT INTO index_binding VALUES (1,?,?)",
                    (json.dumps(self.binding, sort_keys=True), int(not nonempty)),
                )

    @staticmethod
    def saved(catalog):
        with catalog.connect() as db:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='index_binding'").fetchone()
            if not exists:
                return None
            row = db.execute("SELECT binding,published FROM index_binding WHERE id=1").fetchone()
            return (
                {"binding": json.loads(row["binding"]), "published": bool(row["published"])}
                if row
                else None
            )

    def entry(self, key):
        with self.catalog.connect() as db:
            row = db.execute("SELECT * FROM index_messages WHERE key=?", (key,)).fetchone()
            if not row:
                return None
            value = dict(row)
            value["points"] = json.loads(value["points"])
            return value

    def ready(self, key):
        value = self.entry(key)
        return bool(value and value["phase"] == "ready")

    def prepare(self, key, text, points):
        digest = text_hash(text)
        revision = text_hash(json.dumps(points, sort_keys=True))
        old = self.entry(key)
        if old and (old["text_hash"] != digest or old["revision"] != revision):
            raise ValueError("Index manifest changed; use a separate target collection")
        with self.catalog.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO index_messages VALUES (?,?,?,'pending',?)",
                (key, digest, json.dumps(points, sort_keys=True), revision),
            )
        return revision

    def finish(self, key):
        with self.catalog.connect() as db:
            db.execute("UPDATE index_messages SET phase='ready' WHERE key=?", (key,))

    def forget(self, key):
        with self.catalog.connect() as db:
            db.execute("DELETE FROM index_messages WHERE key=?", (key,))

    def status(self):
        with self.catalog.connect() as db:
            ready = db.execute(
                "SELECT COUNT(*) FROM index_messages WHERE phase='ready'"
            ).fetchone()[0]
            pending = db.execute(
                "SELECT COUNT(*) FROM index_messages WHERE phase!='ready'"
            ).fetchone()[0]
        return {
            "index_backend": "qdrant",
            "index_ready_messages": ready,
            "index_pending_messages": pending,
            "embedding_profile": self.profile.identity,
        }

    def publish(self):
        with self.catalog.connect() as db:
            db.execute("UPDATE index_binding SET published=1 WHERE id=1")

    @classmethod
    def from_saved(cls, catalog):
        saved = cls.saved(catalog)
        if not saved or not saved["published"]:
            raise ValueError("Catalog has no verified, published Qdrant binding")
        binding = saved["binding"]
        return cls(catalog, binding["collection"], EmbeddingProfile(**binding["profile"]))
