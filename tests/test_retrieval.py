"""Retrieval regressions: targeted hydration, bounded pages and complete exact searches."""

import json
import pytest
from proton_rag.catalog import Catalog
from proton_rag.config import Settings
from proton_rag.mcp_server import build, search
from proton_rag.mailbox import Inventory
from test_mail_reading import call


def seed(
    catalog, count=1, text="Flight AA 3366 to Burlington 🦊", date="Thu, 9 Aug 2018 12:00:00 +0000"
):
    keys = []
    for i in range(count):
        key = "proton-mail-" + format(i, "064x")
        catalog.intent(
            key,
            "INBOX",
            "1",
            str(i + 1),
            str(i),
            False,
            {"subject": f"Itinerary {i}", "sender": "Airline <air@example.test>", "date": date},
        )
        catalog.store_text(key, text, [], False)
        catalog.activate(key, [])
        keys.append(key)
    return keys


class Ranked:
    def __init__(self, keys, text="Flight AA 3366 to Burlington 🦊"):
        self.keys, self.text = keys, text

    async def search(self, query, limit, **kwargs):
        return [{"text": self.text, "metadata": {"docSource": key}} for key in self.keys[:limit]]


async def test_search_and_status_do_not_load_all_memberships(tmp_path, monkeypatch):
    catalog = Catalog(tmp_path / "catalog.db")
    keys = seed(catalog)
    catalog.set_scope(["INBOX"])
    catalog.observe(Inventory("INBOX", "1", ("1",)))
    catalog.folder_finished("INBOX", True)
    monkeypatch.setattr(catalog, "rows", lambda *a: pytest.fail("whole catalog scan"))
    hits = await search(catalog, Ranked(keys), "flight", 1, Settings())
    assert hits[0]["message_id"] == keys[0]
    status = catalog.coverage()
    assert status["coverage_complete"] is True
    assert status["unique_messages_indexed"] == 1
    assert status["indexed_date_range"] == {"earliest": "2018-08-09", "latest": "2018-08-09"}


async def test_internal_search_respects_requested_limit(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    keys = seed(catalog, 3)

    class Limited(Ranked):
        async def search(self, query, limit):
            assert limit == 2
            return await super().search(query, limit)

    assert len(await search(catalog, Limited(keys), "flight", 2, Settings())) == 2
