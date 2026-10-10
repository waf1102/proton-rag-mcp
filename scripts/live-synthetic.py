"""Exercise real local APIs without IMAP or private data; no paid calls."""

import asyncio
import json
import os
import time
from pathlib import Path
from proton_rag.runtime import load_index
from proton_rag.mailbox import Snapshot
from proton_rag.sync import Catalog, synchronize
from proton_rag.config import Settings


async def run():
    settings = Settings.from_env()
    if settings.workspace != "proton-fixtures":
        raise ValueError("This fixture test requires QDRANT_COLLECTION=proton-fixtures")
    state = Path(os.environ["RAG_STATE_DIR"])
    state.mkdir(exist_ok=True, parents=True)
    catalog = Catalog(state / "catalog.db")
    assert all(r["synthetic"] for r in catalog.rows().values()), "Refuse mixed/live catalog"
    backend = load_index(settings, catalog, writer=True)
    raw = b"Subject: Synthetic cobalt delivery\r\n\r\nThe cobalt shipment arrives Tuesday at warehouse violet shelf 12."
    raw2 = b"Subject: Synthetic zinc delivery\r\n\r\nThe zinc shipment arrives Friday at warehouse orange shelf 4."
    snapshot = Snapshot("1", {"7": raw, "8": raw2})
    start = time.monotonic()
    synchronize(catalog, backend, snapshot, synthetic=True)
    ingest = time.monotonic() - start
    paths_before = {k: r["paths"] for k, r in catalog.rows().items()}
    synchronize(Catalog(state / "catalog.db"), backend, snapshot, synthetic=True)
    assert paths_before == {k: r["paths"] for k, r in catalog.rows().items()}
    start = time.monotonic()
    hits = await backend.search("When does cobalt arrive?", 3)
    assert any("Tuesday" in h["text"] for h in hits)
    query = time.monotonic() - start
    old = next(k for k, r in catalog.rows().items() if r["uid"] == "8")
    synchronize(catalog, backend, Snapshot("1", {"7": raw}), synthetic=True)
    assert not backend.find(old)
    hits = await backend.search("When does zinc arrive?", 5)
    assert all(h["metadata"]["docSource"] != old for h in hits)
    # Restore a fixture file to hand over to the owned daemon.
    fixture = state / "fixtures"
    fixture.mkdir(exist_ok=True)
    (fixture / "7.eml").write_bytes(raw)
    print(
        json.dumps(
            {
                "ingest_seconds": round(ingest, 3),
                "query_seconds": round(query, 3),
                "retry_restart_idempotent": True,
                "deletion_verified": True,
                "remaining_synthetic_messages": len(catalog.rows()),
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(run())
