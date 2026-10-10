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


async def test_compact_rank_42_is_reachable_without_truncation(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    keys = seed(catalog, 50, text="Prelude " * 1000 + "Flight AA 3366 to Burlington 🦊")
    server = build(
        catalog, Ranked(keys, text="Prelude " * 1000 + "Flight AA 3366 to Burlington 🦊")
    )
    args = {"query": "3366", "limit": 50}
    found = []
    page = await call(server, "search_mail", args)
    while True:
        assert len(json.dumps(page, indent=2).encode()) <= 8192
        for hit in page["sources"]:
            assert len(hit["text"]) <= 300
            assert "locations" not in hit
            assert "3366" in hit["text"]
        found.extend(h["message_id"] for h in page["sources"])
        if not page["has_more"]:
            break
        page = await call(server, "search_mail", {**args, "cursor": page["next_cursor"]})
    assert found == keys
    assert keys[41] in found
    assert page["exhaustive"] is False


async def test_exact_pages_more_than_50_and_numeric_precision(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    keys = seed(catalog, 63)
    other = seed(catalog, 1, text="Unrelated 13366 and 33660")  # replace cached first text
    server = build(catalog, Ranked(keys))
    args = {"query": "3366", "mode": "exact", "limit": 50}
    page = await call(server, "search_mail", args)
    found = []
    while True:
        assert page["exhaustive"] is True
        assert page["match_count"] == 62
        found.extend(h["message_id"] for h in page["sources"])
        if not page["has_more"]:
            break
        page = await call(server, "search_mail", {**args, "cursor": page["next_cursor"]})
    assert set(found) == set(keys) - set(other)
    assert len(found) == 62


async def test_exact_filters_dates_unicode_memberships_and_empty_query(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    keys = seed(catalog, 1, date="Thu, 9 Aug 2018 23:30:00 -0400")
    catalog.intent("copy", "Folders/旅行", "2", "2", "0", False, {})
    catalog.activate("copy", [])
    server = build(catalog, Ranked(keys))
    result = await call(
        server,
        "search_mail",
        {
            "query": "",
            "mode": "exact",
            "sent_after": "2018-08-10",
            "sent_before": "2018-08-11",
            "sender": "AIR@",
            "subject": "itinerary",
            "folder": "Folders/旅行",
        },
    )
    assert [h["message_id"] for h in result["sources"]] == keys
    assert set(result["sources"][0]["folders"]) == {"INBOX", "Folders/旅行"}
    assert result["match_count"] == 1
    assert (
        await call(
            server, "search_mail", {"query": "3366", "mode": "exact", "sent_before": "2018-08-10"}
        )
    )["sources"] == []
    with pytest.raises(Exception, match="date"):
        await server.call_tool("search_mail", {"query": "3366", "sent_after": "nonsense"})


async def test_cursor_query_binding_deletion_and_invalid_queries(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    keys = seed(catalog, 3)
    server = build(catalog, Ranked(keys))
    args = {"query": "3366", "mode": "exact", "limit": 1}
    page = await call(server, "search_mail", args)
    assert page["next_cursor"]
    with pytest.raises(Exception, match="cursor|Cursor"):
        await server.call_tool(
            "search_mail", {**args, "query": "flight", "cursor": page["next_cursor"]}
        )
    catalog.retire(keys[1])
    page = await call(server, "search_mail", {**args, "cursor": page["next_cursor"]})
    assert [h["message_id"] for h in page["sources"]] == [keys[2]]
    assert page["has_more"] is False
    with pytest.raises(Exception, match="query|Query"):
        await server.call_tool("search_mail", {"query": '"unfinished', "mode": "exact"})


async def test_batch_unicode_paging_and_individual_errors(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    keys = seed(catalog, 3, text="日本語🦊" * 3000)
    server = build(catalog, object())
    offsets = {key: 0 for key in keys}
    recovered = {key: "" for key in keys}
    while offsets:
        page = await call(
            server,
            "read_mail_batch",
            {
                "requests": [
                    {"message_id": key, "offset": offset, "length": 2000}
                    for key, offset in offsets.items()
                ]
            },
        )
        assert len(json.dumps(page, indent=2).encode()) <= 8192
        for result in page["messages"]:
            key = result["message_id"]
            assert result["offset"] == offsets[key]
            recovered[key] += result["text"]
            if result["next_offset"] is None:
                del offsets[key]
            else:
                assert result["next_offset"] > offsets[key]
                offsets[key] = result["next_offset"]
    assert all(text == "日本語🦊" * 3000 for text in recovered.values())
    page = await call(
        server,
        "read_mail_batch",
        {"requests": [{"message_id": keys[0]}, {"message_id": "unknown"}]},
    )
    assert "text" in page["messages"][0]
    assert "error" in page["messages"][1]
    with pytest.raises(Exception):
        await server.call_tool("read_mail_batch", {"requests": [{"message_id": keys[0]}] * 6})


def test_lexical_backfill_resumes_and_updates_transactionally(tmp_path):
    from proton_rag import lexical

    catalog = Catalog(tmp_path / "catalog.db")
    keys = seed(catalog, 3)
    # Emulate the pre-upgrade catalog without relying on the new write triggers.
    with catalog.connect() as db:
        db.execute("DELETE FROM mail_fts")
        db.execute("UPDATE catalog_settings SET value='0' WHERE name='lexical_ready'")
    assert lexical.backfill(catalog, 1, max_batches=1)["lexical_ready"] is False
    with pytest.raises(ValueError, match="backfill"):
        lexical.candidates(catalog, "3366", {})
    reopened = Catalog(catalog.path)
    assert lexical.backfill(reopened, 1)["lexical_ready"] is True
    assert lexical.candidates(reopened, '"AA 3366"', {}) == keys
    reopened.store_text(keys[0], "Different booking 7788", [], False)
    assert lexical.candidates(reopened, "3366", {}) == keys[1:]
    reopened.intent("alias", "Labels/旅行", "9", "9", "0", False, {})
    reopened.activate("alias", [])
    reopened.forget(keys[0])
    assert lexical.candidates(reopened, "7788", {}) == [keys[0]]
    reopened.forget("alias")
    assert lexical.candidates(reopened, "7788", {}) == []
    with reopened.connect() as db:
        db.execute("BEGIN")
        db.execute("DELETE FROM message_text WHERE key=?", (keys[1],))
        db.rollback()
    assert lexical.candidates(reopened, "3366", {}) == keys[1:]


def test_cursor_expiry_eviction_budget_and_tampering():
    from proton_rag.retrieval import Pages

    clock = [0]
    pages = Pages(clock=lambda: clock[0], ttl=10, max_sessions=1, max_bytes=10)
    first = pages.create("query", ["id1"], True)
    token = pages.token(first, 0)
    assert pages.resume(token, "query")[1] == 0
    with pytest.raises(ValueError, match="cursor"):
        pages.resume(token[:-4] + "AAAA", "query")
    clock[0] = 10
    with pytest.raises(ValueError, match="cursor"):
        pages.resume(token, "query")
    first = pages.create("query", ["id1"], True)
    token = pages.token(first, 0)
    pages.create("query2", ["id2"], True)
    with pytest.raises(ValueError, match="cursor"):
        pages.resume(token, "query")
    with pytest.raises(ValueError, match="memory"):
        pages.create("large", ["x" * 11], True)


async def test_exact_hides_pending_and_missing_header_dates(tmp_path):
    from test_qdrant import setup_index

    catalog, state, backend, _ = setup_index(tmp_path)
    keys = seed(catalog, 2, date="invalid")
    for key in keys:
        backend.ensure(key, "Flight AA 3366 to Burlington 🦊")
    with catalog.connect() as db:
        db.execute("UPDATE index_messages SET phase='pending' WHERE key=?", (keys[0],))
    server = build(catalog, backend)
    result = await call(server, "search_mail", {"query": "3366", "mode": "exact"})
    assert [h["message_id"] for h in result["sources"]] == [keys[1]]
    assert (
        await call(
            server, "search_mail", {"query": "3366", "mode": "exact", "sent_after": "2018-01-01"}
        )
    )["sources"] == []


async def test_hybrid_filter_applies_to_both_prefetches_before_ranking(tmp_path):
    import httpx
    from test_qdrant import setup_index

    catalog, state, backend, service = setup_index(tmp_path)
    keys = seed(catalog, 2)
    for key in keys:
        backend.ensure(key, "Flight AA 3366 to Burlington 🦊")
    actual = service.__call__

    def transport(request):
        if request.url.path.endswith("/points/query/groups"):
            data = json.loads(request.content)
            assert data["filter"]["must"][0]["match"]["any"] == [keys[1]]
            assert all(p["filter"] == data["filter"] for p in data["prefetch"])
        return actual(request)

    backend.client = httpx.Client(transport=httpx.MockTransport(transport))
    result = await call(
        build(catalog, backend), "search_mail", {"query": "flight", "subject": "Itinerary 1"}
    )
    assert [h["message_id"] for h in result["sources"]] == [keys[1]]


async def test_cursor_hides_message_removed_from_requested_folder(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    keys = seed(catalog, 3)
    catalog.intent("archive-copy", "Archive", "9", "9", "1", False, {})
    catalog.activate("archive-copy", [])
    server = build(catalog, Ranked(keys))
    args = {"query": "3366", "mode": "exact", "folder": "INBOX", "limit": 1}
    first = await call(server, "search_mail", args)
    catalog.forget(keys[1])
    second = await call(server, "search_mail", {**args, "cursor": first["next_cursor"]})
    assert [h["message_id"] for h in second["sources"]] == [keys[2]]
    assert second["match_count"] == 3
    assert second["has_more"] is False


async def test_hybrid_retains_ranked_evidence_without_cached_body(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    keys = seed(catalog)
    with catalog.connect() as db:
        db.execute("DELETE FROM message_text")
    result = await call(
        build(catalog, Ranked(keys, text="Flight AA 3366 to Burlington")),
        "search_mail",
        {"query": "3366"},
    )
    assert "3366" in result["sources"][0]["text"]
    assert len(result["sources"][0]["text"]) <= 300


def test_snapshot_memory_accounts_for_fallback_previews():
    from proton_rag.retrieval import Pages

    pages = Pages(max_bytes=10)
    with pytest.raises(ValueError, match="memory"):
        pages.create("query", ["key"], False, {"key": "x" * 8})
