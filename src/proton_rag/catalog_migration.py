"""Atomic catalog normalization; obsolete remote copies use a durable cleanup queue."""

import json
from collections import defaultdict


def create_locations(db):
    db.execute(
        "CREATE TABLE documents (key TEXT PRIMARY KEY, folder TEXT, validity TEXT, "
        "uid TEXT, digest TEXT, message_key TEXT NOT NULL REFERENCES messages(key), "
        "active INTEGER, synthetic INTEGER)"
    )
    db.execute("CREATE INDEX folder_uids ON documents(folder,validity,uid)")
    db.execute("CREATE INDEX message_locations ON documents(message_key)")


def migrate(db):
    rows = [dict(r) for r in db.execute("SELECT * FROM documents")]
    texts = {r["key"]: dict(r) for r in db.execute("SELECT * FROM message_text")}
    groups = defaultdict(list)
    for row in rows:
        groups[(row["digest"], row["synthetic"])].append(row)
    db.execute("DROP INDEX IF EXISTS folder_uids")
    db.execute("ALTER TABLE documents RENAME TO legacy_documents")
    create_locations(db)
    db.execute("DELETE FROM message_text")
    for copies in groups.values():
        # Retain an already embedded document; never re-embed a migration.
        copies.sort(
            key=lambda r: (
                not r["active"],
                r["phase"] != "uploading",
                not bool(json.loads(r["paths"])),
                r["key"],
            )
        )
        chosen = copies[0]
        canonical = chosen["key"]
        phase = chosen["phase"]
        if not any(r["active"] for r in copies) and phase == "active":
            phase = "prepared"  # Legacy delete may have succeeded before its response was lost.
        db.execute(
            "INSERT INTO messages VALUES (?,?,?,?,?,?)",
            (
                canonical,
                chosen["digest"],
                chosen["paths"],
                chosen["synthetic"],
                chosen["metadata"],
                phase,
            ),
        )
        cached = [texts[r["key"]] for r in copies if r["key"] in texts]
        if cached:
            content = max(cached, key=lambda t: len(t["text"]))
            db.execute(
                "INSERT INTO message_text VALUES (?,?,?,?)",
                (canonical, content["text"], content["skipped"], content["truncated"]),
            )
        protected = set(json.loads(chosen["paths"]))
        for row in copies:
            db.execute(
                "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?)",
                (
                    row["key"],
                    row["folder"],
                    row["validity"],
                    row["uid"],
                    row["digest"],
                    canonical,
                    row["active"],
                    row["synthetic"],
                ),
            )
            db.execute("INSERT INTO message_aliases VALUES (?,?)", (row["key"], canonical))
            if row["key"] != canonical:
                paths = [p for p in json.loads(row["paths"]) if p not in protected]
                if paths or row["phase"] == "uploading":
                    db.execute(
                        "INSERT INTO duplicate_cleanup VALUES (?,?,?,?)",
                        (row["key"], canonical, json.dumps(paths), row["phase"]),
                    )
    db.execute("DROP TABLE legacy_documents")
