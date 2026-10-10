"""Content-free migration reports and immutable source fingerprints."""

import hashlib
import json
import sqlite3
from pathlib import Path

TABLES = (
    "messages",
    "documents",
    "message_text",
    "message_aliases",
    "folder_inventory",
    "orphan_marks",
    "duplicate_cleanup",
    "catalog_settings",
)


def readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=30)


def fingerprint(path):
    result = {}
    with readonly(path) as db:
        for table in TABLES:
            cols = [r[1] for r in db.execute(f"PRAGMA table_info({table})")]
            if table == "messages":
                cols.remove("paths")
            query = "SELECT " + ",".join(cols) + " FROM " + table
            if table == "catalog_settings":
                query += " WHERE name NOT IN ('runtime','workspace')"
            rows = sorted(tuple(r) for r in db.execute(query))
            result[table] = hashlib.sha256(
                json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode()
            ).hexdigest()
    return result


def save_report(path, report):
    path = Path(path)
    with path.open("w") as file:
        path.chmod(0o600)
        json.dump(report, file, indent=2, sort_keys=True)
        file.write("\n")
