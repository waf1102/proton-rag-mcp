"""Compare rankings against private known-evidence labels using a read-only catalog.

Example: uv run python scripts/benchmark-ranking.py --catalog /private/snapshot.db \
    --cases /private/cases.json --repeats 3
Labels are a JSON array with query, expected_ids, optional correspondence_only,
filters, split and category. Keep labels and catalog outside Git. Results contain
aggregate metrics, never queries, message IDs or email bodies. This measures local
retrieval, not final-answer accuracy or model/client completion time.
"""

import argparse
import asyncio
from collections import defaultdict
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import statistics
import time

from proton_rag import lexical
from proton_rag.catalog import Catalog
from proton_rag.config import Settings
from proton_rag.index import KEY
from proton_rag.related import correspondence_keys
from proton_rag.retrieval import filters
from proton_rag.runtime import load_index


class ReadOnlyCatalog(Catalog):
    def __init__(self, path):
        self.path = Path(path).resolve(strict=True)
        with self.connect() as db:
            self.namespace = db.execute(
                "SELECT value FROM catalog_settings WHERE name='namespace'"
            ).fetchone()[0]

    def connect(self):
        db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=1)
        db.row_factory = sqlite3.Row
        db.create_function("casefold", 1, lambda s: str(s).casefold(), deterministic=True)
        return db


def load_cases(path):
    cases = json.loads(Path(path).read_text())
    if not isinstance(cases, list) or not 1 <= len(cases) <= 1000:
        raise ValueError("Labels must contain between 1 and 1000 cases")
    for case in cases:
        if (
            not isinstance(case, dict)
            or not isinstance(case.get("query"), str)
            or not 1 <= len(case["query"]) <= 4000
            or not isinstance(case.get("expected_ids"), list)
            or not case["expected_ids"]
            or any(not isinstance(k, str) or not KEY.fullmatch(k) for k in case["expected_ids"])
            or len(set(case["expected_ids"])) != len(case["expected_ids"])
            or not isinstance(case.get("correspondence_only", False), bool)
            or not isinstance(case.get("filters", {}), dict)
        ):
            raise ValueError("Each label needs a query and unique canonical expected_ids")
        for name in ("split", "category"):
            if not re.fullmatch(r"[a-z0-9_-]{1,40}", case.get(name, "all")):
                raise ValueError("Split/category must be short lowercase labels")
        if set(case.get("filters", {})) - {
            "sent_after",
            "sent_before",
            "sender",
            "subject",
            "folder",
        }:
            raise ValueError("Unknown label filter")
    return cases


def summarize(records):
    groups = defaultdict(list)
    for record in records:
        groups[(record["ranking"], record["split"], record["category"])].append(record)
    return [
        {
            "ranking": ranking,
            "split": split,
            "category": category,
            "trials": len(rows),
            "known_evidence_hit_rate": statistics.mean(r["hit"] for r in rows),
            "known_evidence_recall": statistics.mean(r["recall"] for r in rows),
            "mrr": statistics.mean(r["mrr"] for r in rows),
            "median_seconds": statistics.median(r["seconds"] for r in rows),
            "max_seconds": max(r["seconds"] for r in rows),
            "median_hit_bytes": statistics.median(r["hit_bytes"] for r in rows),
        }
        for (ranking, split, category), rows in sorted(groups.items())
    ]


async def run(args):
    cases = load_cases(args.cases)
    catalog = ReadOnlyCatalog(args.catalog)
    backend = load_index(Settings.from_env(), catalog)
    expected = {k for case in cases for k in case["expected_ids"]}
    if (
        catalog.active_keys(list(expected)) != expected
        or any(v != "ready" for v in backend.state.phases(list(expected)).values())
        or set(backend.state.phases(list(expected))) != expected
    ):
        raise ValueError("Known evidence must be active and committed in this snapshot/index")
    records = []
    try:
        for case in cases:
            selected = filters(**case.get("filters", {}))
            kwargs = {}
            if case.get("correspondence_only"):
                kwargs["message_ids"] = correspondence_keys(catalog, selected)
            elif any(v is not None for v in selected.values()):
                kwargs["message_ids"] = lexical.candidates(catalog, "", selected, exact=False)
            gold = set(case["expected_ids"])
            if "message_ids" in kwargs and not gold <= set(kwargs["message_ids"]):
                raise ValueError("Known evidence is excluded by label filters")
            for attempt in range(args.repeats):
                # Alternate order to reduce cache/order bias. Each measures embedding too.
                strategies = ("legacy", "ensemble") if attempt % 2 == 0 else ("ensemble", "legacy")
                for strategy in strategies:
                    backend.settings = replace(backend.settings, search_ranking=strategy)
                    start = time.perf_counter()
                    hits = await backend.search(case["query"], args.limit, **kwargs)
                    seconds = time.perf_counter() - start
                    ids = [h["metadata"]["docSource"] for h in hits]
                    ranks = [ids.index(k) + 1 for k in gold if k in ids]
                    records.append(
                        {
                            "ranking": strategy,
                            "split": case.get("split", "all"),
                            "category": case.get("category", "all"),
                            "hit": int(bool(ranks)),
                            "recall": len(ranks) / len(gold),
                            "mrr": 1 / min(ranks) if ranks else 0,
                            "seconds": seconds,
                            "hit_bytes": len(json.dumps(hits, ensure_ascii=False).encode()),
                        }
                    )
    finally:
        backend.client.close()
    print(
        json.dumps(
            {
                "local_retrieval_only": True,
                "cases": len(cases),
                "limit": args.limit,
                "repeats": args.repeats,
                "labels_sha256": hashlib.sha256(Path(args.cases).read_bytes()).hexdigest(),
                "metrics": summarize(records),
                "overall": summarize([{**r, "split": "all", "category": "all"} for r in records]),
            }
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--cases", required=True)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.limit <= 50 or not 1 <= args.repeats <= 10:
        parser.error("limit must be 1–50 and repeats 1–10")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
