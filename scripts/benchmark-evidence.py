"""Measure local evidence extraction against a frozen catalog, without model/client timing.

Usage: .venv/bin/python scripts/benchmark-evidence.py --catalog /private/snapshot.db \
    --pattern 'invoice|total|paid' --repeats 3 --all-pages
The catalog is opened read-only; no mailbox text or identifiers are printed.
"""

import argparse
import json
from pathlib import Path
import sqlite3
import statistics
import time

from proton_rag.catalog import Catalog
from proton_rag.evidence import Evidence
from proton_rag.retrieval import filters, wire_size


class ReadOnlyCatalog(Catalog):
    def __init__(self, path):
        self.path = Path(path).resolve()
        if not self.path.is_file():
            raise ValueError("Catalog snapshot does not exist")

    def connect(self):
        db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=1)
        db.row_factory = sqlite3.Row
        db.create_function("casefold", 1, lambda s: str(s).casefold(), deterministic=True)
        return db


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--pattern", required=True)
    parser.add_argument("--sent-after")
    parser.add_argument("--sent-before")
    parser.add_argument("--sender")
    parser.add_argument("--subject")
    parser.add_argument("--folder")
    parser.add_argument("--context", type=int, default=2)
    parser.add_argument("--max-bytes", type=int, default=16000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--all-pages", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.repeats <= 100:
        parser.error("repeats must be between 1 and 100")
    scanner = Evidence(ReadOnlyCatalog(args.catalog))
    selected = filters(args.sent_after, args.sent_before, args.sender, args.subject, args.folder)
    records = []
    for attempt in range(args.repeats):
        start = time.perf_counter()
        page = scanner.query(
            pattern=args.pattern, selected=selected, context=args.context, max_bytes=args.max_bytes
        )
        first_seconds = time.perf_counter() - start
        calls, payload, shown = 0, 0, set()
        while True:
            calls += 1
            size = wire_size(page)
            assert size <= args.max_bytes
            payload += size
            shown.update(s["message_id"] for s in page["sources"])
            if not args.all_pages or not page["has_more"]:
                break
            page = scanner.query(**page["next_call"]["arguments"])
        record = {
            "attempt": attempt + 1,
            "first_page_seconds": round(first_seconds, 4),
            "seconds": round(time.perf_counter() - start, 4),
            "calls": calls,
            "payload_bytes": payload,
            "displayed_unique_messages": len(shown),
            "matched_messages": page["matched_messages"],
            "searched_messages": page["searched_messages"],
            "unavailable_messages": page["unavailable_messages"],
            "has_more": page["has_more"],
        }
        records.append(record)
        print(json.dumps(record), flush=True)
    print(
        json.dumps(
            {
                "local_tool_only": True,
                "median_seconds": statistics.median(r["seconds"] for r in records),
            }
        )
    )


if __name__ == "__main__":
    main()
