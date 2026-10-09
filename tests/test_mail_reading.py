"""Full-message retrieval must not stop at the search excerpt boundary."""

import json
import pytest
from proton_rag.sync import Catalog
from proton_rag.mcp_server import build
from proton_rag.mailbox import Inventory

KEY = "proton-mail-" + "a" * 64


def seed(catalog):
    catalog.intent(
        KEY,
        "INBOX",
        "1",
        "7",
        "abc",
        False,
        {"subject": "Itinerary", "date": "Mon, 2 Jan 2017 12:00:00 +0000"},
    )
    catalog.activate(KEY, ["custom-documents/itinerary.json"])


async def call(server, name, args):
    result = await server.call_tool(name, args)
    return (
        json.loads(result[0][0].text) if isinstance(result, tuple) else json.loads(result[0].text)
    )


async def test_read_full_text_past_search_excerpt_and_paginate(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    seed(catalog)
    text = "Flight itinerary\n" + "x" * 12000 + "\nConfirmation: ABC123; Total: $450"
    catalog.store_text(KEY, text, skipped=[], truncated=False)
    server = build(catalog, object())
    first = await call(server, "read_mail", {"message_id": KEY, "length": 10000})
    second = await call(
        server, "read_mail", {"message_id": KEY, "offset": first["next_offset"], "length": 10000}
    )
    assert first["text"] + second["text"] == text
    assert "ABC123" in second["text"]
    assert second["next_offset"] is None
    assert second["metadata"]["subject"] == "Itinerary"
    assert second["untrusted"] is True
    assert second["extraction"]["text_truncated"] is False
    assert second["citation"] == "imap:///INBOX/1/7#abc"


async def test_read_old_citation_and_reject_unknown_or_retired_message(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    seed(catalog)
    catalog.store_text(KEY, "body", skipped=["unsupported"], truncated=True)
    server = build(catalog, object())
    result = await call(server, "read_mail", {"message_id": "imap:///INBOX/1/7#abc"})
    assert result["text"] == "body"
    assert result["extraction"]["text_truncated"] is True
    assert result["extraction"]["skipped_parts"] == ["unsupported"]
    with pytest.raises(Exception):
        await server.call_tool("read_mail", {"message_id": "../../credentials"})
    catalog.retire(KEY)
    with pytest.raises(Exception):
        await server.call_tool("read_mail", {"message_id": KEY})


async def test_missing_body_is_reported_instead_of_returning_excerpt(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    seed(catalog)
    with pytest.raises(Exception, match="not available"):
        await build(catalog, object()).call_tool("read_mail", {"message_id": KEY})


def test_coverage_requires_every_selected_folder_and_complete_inventory(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    seed(catalog)
    assert catalog.coverage()["coverage_complete"] is False
    catalog.set_scope(["INBOX", "Archive"])
    catalog.observe(Inventory("INBOX", "1", ("7",)))
    catalog.observe(Inventory("Archive", "2", ("4",)))
    catalog.folder_finished("INBOX", successful=True)
    report = catalog.coverage()
    assert report["folder_entries_expected"] == 2
    assert report["folder_entries_indexed"] == 1
    assert report["coverage_complete"] is False
    assert report["indexed_date_range"] == {"earliest": "2017-01-02", "latest": "2017-01-02"}
    catalog.intent("other", "Archive", "2", "4", "xyz", False, {})
    catalog.activate("other", [])
    catalog.folder_finished("Archive", successful=True)
    assert catalog.coverage()["coverage_complete"] is True
    catalog.observe(Inventory("Archive", "2", ("4", "5")))
    assert catalog.coverage()["coverage_complete"] is False


def test_forgetting_message_removes_cached_body(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    seed(catalog)
    catalog.store_text(KEY, "secret", skipped=[], truncated=False)
    catalog.forget(KEY)
    assert catalog.get_text(KEY) is None


def test_coverage_excludes_retained_folders_and_old_uidvalidity(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    seed(catalog)
    catalog.store_text(KEY, "current", [], False)
    catalog.set_scope(["INBOX"])
    catalog.observe(Inventory("INBOX", "1", ("7",)))
    catalog.folder_finished("INBOX", True)
    catalog.intent(
        "retained", "Archive", "2", "4", "xyz", False, {"date": "Wed, 1 Jan 2014 12:00:00 +0000"}
    )
    catalog.activate("retained", [])
    catalog.intent(
        "old", "INBOX", "0", "8", "old", False, {"date": "Thu, 1 Jan 2015 12:00:00 +0000"}
    )
    catalog.activate("old", [])
    catalog.intent("pending", "Archive", "2", "5", "pending", False, {})
    report = catalog.coverage()
    assert report["coverage_complete"] is True
    assert report["folder_entries_expected"] == report["folder_entries_indexed"] == 1
    assert report["full_text_available"] == 1
    assert report["pending_entries"] == 0
    assert report["indexed_date_range"]["earliest"] == "2017-01-02"
    assert report["retained_entries_outside_scope"] == 2
    catalog.observe(Inventory("INBOX", "3", ("7",)))
    assert catalog.coverage()["coverage_complete"] is False
    assert catalog.coverage()["folder_entries_indexed"] == 0
