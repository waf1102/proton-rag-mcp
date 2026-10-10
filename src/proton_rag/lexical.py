"""Transactional, canonical SQLite full-text retrieval alongside the vector index."""

import argparse
import json
import os
import sqlite3
from .index_state import exclusive_state
from .config import Settings


def initialize(db):
    db.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS mail_fts USING fts5(key UNINDEXED,subject,sender,body)"
    )
    existing = db.execute("SELECT 1 FROM message_text LIMIT 1").fetchone()
    db.execute(
        "INSERT OR IGNORE INTO catalog_settings VALUES ('lexical_ready',?)",
        ("0" if existing else "1",),
    )
    # A replacement insert first removes the existing row; it does not depend on SQLite's
    # recursive_triggers setting to observe the implicit DELETE of INSERT OR REPLACE.
    for event, ref in [("INSERT", "new"), ("UPDATE", "new")]:
        db.execute(f"""CREATE TRIGGER IF NOT EXISTS lexical_text_{event.lower()} AFTER {event} ON message_text BEGIN
            DELETE FROM mail_fts WHERE rowid=(SELECT rowid FROM messages WHERE key={ref}.key);
            INSERT INTO mail_fts(rowid,key,subject,sender,body)
            SELECT rowid,key,coalesce(json_extract(metadata,'$.subject'),''),
            coalesce(json_extract(metadata,'$.sender'),''),{ref}.text FROM messages WHERE key={ref}.key;
        END""")
    db.execute("""CREATE TRIGGER IF NOT EXISTS lexical_text_delete AFTER DELETE ON message_text BEGIN
        DELETE FROM mail_fts WHERE rowid=(SELECT rowid FROM messages WHERE key=old.key);
    END""")
    db.execute("""CREATE TRIGGER IF NOT EXISTS lexical_message_delete AFTER DELETE ON messages BEGIN
        DELETE FROM mail_fts WHERE rowid=old.rowid;
        DELETE FROM message_dates WHERE key=old.key;
    END""")
    db.execute("""CREATE TRIGGER IF NOT EXISTS lexical_metadata AFTER UPDATE OF metadata ON messages BEGIN
        UPDATE mail_fts SET subject=coalesce(json_extract(new.metadata,'$.subject'),''),
        sender=coalesce(json_extract(new.metadata,'$.sender'),'') WHERE rowid=new.rowid;
    END""")


def status(catalog):
    with catalog.connect() as db:
        ready = db.execute(
            "SELECT value FROM catalog_settings WHERE name='lexical_ready'"
        ).fetchone()
        count = db.execute("SELECT COUNT(*) FROM mail_fts").fetchone()[0]
    return {"lexical_ready": bool(ready and ready[0] == "1"), "lexical_messages": count}


def backfill(catalog, batch_size=100, *, max_batches=None):
    """Resume by missing canonical row IDs; each batch and readiness publish are atomic."""
    batches = 0
    while max_batches is None or batches < max_batches:
        with catalog.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                """SELECT m.rowid,m.key,m.metadata,t.text FROM messages m
                JOIN message_text t ON t.key=m.key LEFT JOIN mail_fts f ON f.rowid=m.rowid
                WHERE f.rowid IS NULL ORDER BY m.rowid LIMIT ?""",
                (batch_size,),
            ).fetchall()
            if not rows:
                db.execute("INSERT INTO mail_fts(mail_fts) VALUES ('integrity-check')")
                count = db.execute("SELECT COUNT(*) FROM mail_fts").fetchone()[0]
                expected = db.execute(
                    "SELECT COUNT(*) FROM message_text t JOIN messages m ON m.key=t.key"
                ).fetchone()[0]
                if count != expected:
                    raise ValueError("Lexical coverage verification failed")
                db.execute("UPDATE catalog_settings SET value='1' WHERE name='lexical_ready'")
            for row in rows:
                meta = json.loads(row["metadata"])
                db.execute(
                    "INSERT INTO mail_fts(rowid,key,subject,sender,body) VALUES (?,?,?,?,?)",
                    (
                        row["rowid"],
                        row["key"],
                        meta.get("subject", ""),
                        meta.get("sender", ""),
                        row["text"],
                    ),
                )
        if not rows:
            return status(catalog)
        batches += 1
    return status(catalog)


def predicate(catalog, filters):
    clauses = [
        "m.phase='active'",
        "EXISTS(SELECT 1 FROM documents d WHERE d.message_key=m.key AND d.active=1)",
    ]
    args = []
    with catalog.connect() as db:
        bound = db.execute("SELECT 1 FROM sqlite_master WHERE name='index_messages'").fetchone()
    if bound:
        clauses.append(
            "EXISTS(SELECT 1 FROM index_messages im WHERE im.key=m.key AND im.phase='ready')"
        )
    for field, op in [("sent_after", ">="), ("sent_before", "<")]:
        if filters.get(field) is not None:
            clauses.append(f"md.sent_at {op} ?")
            args.append(filters[field])
    for field in ["sender", "subject"]:
        if filters.get(field) is not None:
            clauses.append(
                f"instr(casefold(coalesce(json_extract(m.metadata,'$.{field}'),'')),casefold(?))>0"
            )
            args.append(filters[field])
    if filters.get("folder") is not None:
        clauses.append(
            "EXISTS(SELECT 1 FROM documents d WHERE d.message_key=m.key AND d.active=1 AND d.folder=?)"
        )
        args.append(filters["folder"])
    return " AND ".join(clauses), args


def candidates(catalog, query, filters, *, exact=True, max_bytes=64 * 1024 * 1024):
    if exact and not status(catalog)["lexical_ready"]:
        raise ValueError("Exact search unavailable until lexical backfill completes")
    where, args = predicate(catalog, filters)
    join = " JOIN mail_fts ON mail_fts.rowid=m.rowid" if exact else ""
    if exact and query.strip():
        where += " AND mail_fts MATCH ?"
        args.append(query)
    sql = (
        "SELECT m.key FROM messages m LEFT JOIN message_dates md ON md.key=m.key"
        + join
        + " WHERE "
        + where
        + " ORDER BY m.key"
    )
    ids, size = [], 0
    try:
        with catalog.connect() as db:
            for row in db.execute(sql, args):
                size += len(row[0].encode())
                if size > max_bytes:
                    raise ValueError("Search snapshot exceeds memory limit; narrow the filters")
                ids.append(row[0])
    except sqlite3.OperationalError:
        raise ValueError(
            "Invalid exact query; use FTS5 terms, quoted phrases, AND, OR or NOT"
        ) from None
    return ids


def previews(catalog, keys, query):
    if not query.strip() or not keys:
        return {}
    placeholders = ",".join("?" for _ in keys)
    try:
        with catalog.connect() as db:
            return {
                r["key"]: r["preview"]
                for r in db.execute(
                    f"SELECT key,snippet(mail_fts,-1,'','','…',32) AS preview FROM mail_fts WHERE mail_fts MATCH ? AND key IN ({placeholders})",
                    [query, *keys],
                )
            }
    except sqlite3.OperationalError:
        return {}


def main():
    from .catalog import Catalog

    parser = argparse.ArgumentParser(
        description="Build exact search from cached text without re-embedding"
    )
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("batch-size must be positive")
    os.umask(0o077)
    settings = Settings.from_env()
    with exclusive_state(settings.state_dir):
        print(
            json.dumps(
                backfill(Catalog(settings.state_dir / "catalog.db", writer=True), args.batch_size)
            )
        )


if __name__ == "__main__":
    main()
