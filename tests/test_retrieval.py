"""Retrieval regressions: targeted hydration, bounded pages and complete exact searches."""

import json
import pytest
from proton_rag.catalog import Catalog
from proton_rag.config import Settings
from proton_rag.mcp_server import build, search
from proton_rag.mailbox import Inventory
from proton_rag.retrieval import BUDGET, wire_size
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


async def test_hybrid_keeps_winning_passage_instead_of_header_keyword(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    passage = "We cannot book your journey this month. Please contact your scheduler for new dates."
    keys = seed(catalog, text="subject: Trip request\n\n" + "Layout \n" * 1000 + passage)
    server = build(catalog, Ranked(keys, text=passage))
    result = await call(server, "search_mail", {"query": "trip postponed"})
    assert passage in result["sources"][0]["text"]


async def test_larger_hybrid_response_contains_evidence_and_preserves_budget_on_resume(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    passage = "日本語 🦊 " + "Useful evidence. " * 70
    keys = seed(catalog, 12, text=passage)
    server = build(catalog, Ranked(keys, text=passage))
    result = await call(
        server, "search_mail", {"query": "meaning", "limit": 12, "max_bytes": 16000}
    )
    assert len(result["sources"]) >= 6
    assert len(result["sources"][0]["text"]) > 300
    assert wire_size(result) <= 16000
    recovered = [h["message_id"] for h in result["sources"]]
    while result["has_more"]:
        result = await call(server, "search_mail", result["next_call"]["arguments"])
        assert wire_size(result) <= 16000
        recovered.extend(h["message_id"] for h in result["sources"])
    assert recovered == keys


async def test_correspondence_scope_filters_before_ranking_and_discloses_exclusions(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    keys = seed(catalog, 3)
    with catalog.connect() as db:
        for key, subject in zip(
            keys, ["Newsletter", " Re: Receipt adjustment", "Original request"]
        ):
            md = json.loads(
                db.execute("SELECT metadata FROM messages WHERE key=?", (key,)).fetchone()[0]
            )
            md["subject"] = subject
            db.execute("UPDATE messages SET metadata=? WHERE key=?", (json.dumps(md), key))
        db.execute("UPDATE documents SET folder='Sent' WHERE message_key=?", (keys[2],))

    class Filtered(Ranked):
        async def search(self, query, limit, **kwargs):
            return [
                {"text": "Useful reply", "metadata": {"docSource": key}}
                for key in kwargs["message_ids"]
            ]

    result = await call(
        build(catalog, Filtered(keys)),
        "search_mail",
        {"query": "a corrected receipt", "correspondence_only": True},
    )
    assert {h["message_id"] for h in result["sources"]} == set(keys[1:])
    assert (
        result["retrieval_scope"]
        == "Reply/forward subjects or Sent membership; other mail excluded."
    )


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
        assert wire_size(page) <= BUDGET
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
        assert wire_size(page) <= BUDGET
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


async def test_pages_stay_below_gemini_inline_threshold(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    keys = seed(catalog, 10, text="Flight 3366 " + "x" * 10000)
    server = build(catalog, Ranked(keys))
    # The observed Gemini harness offloads a 4173-byte tool result, but keeps 3855 inline.
    # Leave framing headroom beneath its approximately 4 KB inline boundary.
    for tool, args in [
        ("search_mail", {"query": "flight", "limit": 10}),
        (
            "read_mail_batch",
            {"requests": [{"message_id": key, "length": 2000} for key in keys[:3]]},
        ),
    ]:
        result = await server.call_tool(tool, args)
        content = (
            result.content
            if hasattr(result, "content")
            else result[0]
            if isinstance(result, tuple)
            else result
        )
        assert len(content[0].text.encode()) < 4000


def test_filtered_candidates_stay_within_linear_sql_work(tmp_path, monkeypatch):
    """Catch correlated scans of every active location for every candidate."""
    from proton_rag import lexical
    from proton_rag.retrieval import filters

    catalog = Catalog(tmp_path / "catalog.db")
    keys = seed(catalog, 400)
    original_connect = catalog.connect
    calls = [0]

    def bounded_connect():
        db = original_connect()

        def progress():
            calls[0] += 1
            return int(calls[0] > 1000)

        db.set_progress_handler(progress, 100)
        return db

    monkeypatch.setattr(catalog, "connect", bounded_connect)
    assert lexical.candidates(catalog, "", filters("2018-01-01", "2019-01-01"), exact=False) == keys


async def test_search_continuation_needs_only_returned_next_call(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    keys = seed(catalog, 5)
    server = build(catalog, Ranked(keys))
    first = await call(
        server, "search_mail", {"query": "3366", "mode": "exact", "limit": 1, "folder": "INBOX"}
    )
    found = [h["message_id"] for h in first["sources"]]
    page = first
    while page["has_more"]:
        assert page["next_call"]["tool"] == "search_mail"
        assert page["next_call"]["arguments"] == {"cursor": page["next_cursor"]}
        assert page["remaining_count"] == len(keys) - len(found)
        page = await call(server, "search_mail", page["next_call"]["arguments"])
        found.extend(h["message_id"] for h in page["sources"])
    assert found == keys
    assert page["next_call"] is None and page["remaining_count"] == 0
    with pytest.raises(Exception, match="cursor|Cursor"):
        await server.call_tool("search_mail", {"cursor": first["next_cursor"], "folder": "Archive"})


async def test_batch_accepts_simple_ids_and_continuation_call(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    text = "Trip itinerary 🦊\n" * 200
    keys = seed(catalog, text=text)
    server = build(catalog, object())
    page = await call(server, "read_mail_batch", {"message_ids": keys})
    recovered = ""
    while True:
        body = page["messages"][0]
        recovered += body["text"]
        if body["next_offset"] is None:
            assert page["next_call"] is None
            break
        assert page["next_call"]["tool"] == "read_mail_batch"
        page = await call(server, "read_mail_batch", page["next_call"]["arguments"])
    assert recovered == text
    with pytest.raises(Exception, match="either|both|Choose"):
        await server.call_tool(
            "read_mail_batch", {"message_ids": keys, "requests": [{"message_id": keys[0]}]}
        )


async def test_cursor_only_retains_folder_predicate_after_membership_removed(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    keys = seed(catalog, 3)
    catalog.intent("archived-copy", "Archive", "1", "99", "1", False, {})
    catalog.activate("archived-copy", [])
    server = build(catalog, Ranked(keys))
    page = await call(
        server, "search_mail", {"query": "3366", "mode": "exact", "limit": 1, "folder": "INBOX"}
    )
    catalog.forget(keys[1])
    page = await call(server, "search_mail", {"cursor": page["next_cursor"]})
    assert [h["message_id"] for h in page["sources"]] == [keys[2]]


@pytest.mark.parametrize("count", [1, 3, 5])
async def test_batch_next_calls_preserve_every_character_inside_wire_budget(tmp_path, count):
    from proton_rag.retrieval import BUDGET, wire_size

    catalog = Catalog(tmp_path / "catalog.db")
    text = "日本語\n 🦊 AB" * 110
    keys = seed(catalog, count, text=text)
    server = build(catalog, object())
    page = await call(server, "read_mail_batch", {"message_ids": keys})
    recovered = {key: "" for key in keys}
    rounds = 0
    while True:
        assert wire_size(page) <= BUDGET
        for body in page["messages"]:
            assert "error" not in body
            recovered[body["message_id"]] += body["text"]
        if page["next_call"] is None:
            break
        rounds += 1
        assert rounds < 150
        page = await call(server, "read_mail_batch", page["next_call"]["arguments"])
    assert all(value == text for value in recovered.values())


async def test_compact_wire_preserves_structured_data_and_fits_more_evidence(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    keys = seed(catalog, 8, text="flight " + "evidence " * 50)
    server = build(catalog, Ranked(keys))
    result = await server.call_tool("search_mail", {"query": "flight", "limit": 8})
    if hasattr(result, "content"):
        contents, structured = result.content, result.structuredContent
    elif isinstance(result, tuple):
        contents, structured = result
    else:
        contents, structured = result, None
    page = json.loads(contents[0].text)
    assert len(contents[0].text.encode()) < 3900
    assert len(page["sources"]) >= 5
    assert page == structured
    assert [h["message_id"] for h in page["sources"]] == keys[: len(page["sources"])]


async def test_batch_cursor_errors_retain_original_request_index(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    keys = seed(catalog, 2, text="x" * 10000)
    catalog.store_text(keys[0], "short", [], False)
    server = build(catalog, object())
    page = await call(server, "read_mail_batch", {"message_ids": keys})
    assert page["messages"][0]["next_offset"] is None
    assert page["messages"][1]["next_offset"] is not None
    catalog.retire(keys[1])
    page = await call(server, "read_mail_batch", page["next_call"]["arguments"])
    assert page["messages"][0]["error"]
    assert page["messages"][0]["request_index"] == 1


async def test_exact_ranks_direct_evidence_before_incidental_long_mail(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    keys = seed(catalog, 2, text="Unrelated newsletter " * 1000 + " flight")
    catalog.store_text(keys[1], "Flight itinerary: flight AA 3366 departs Chicago.", [], False)
    server = build(catalog, Ranked(keys))
    result = await call(server, "search_mail", {"query": "flight", "mode": "exact", "limit": 1})
    assert result["sources"][0]["message_id"] == keys[1]
    assert result["match_count"] == 2
    result = await call(server, "search_mail", result["next_call"]["arguments"])
    assert result["sources"][0]["message_id"] == keys[0]
    assert result["has_more"] is False


async def test_larger_search_budget_fits_unicode_passage_instead_of_metadata_error(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    text = "日本語 " * 500
    keys = seed(catalog, text=text)
    server = build(catalog, Ranked(keys, text=text))
    result = await call(server, "search_mail", {"query": "meaning", "max_bytes": 4000})
    assert result["sources"][0]["text"]
    assert result["sources"][0]["passage_clipped"] is True
    assert wire_size(result) <= 4000


async def test_hybrid_alternatives_share_filters_and_interleave_unique_evidence(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    keys = seed(catalog, 4)
    requested = []

    class Variants:
        async def search(self, query, limit, **kwargs):
            requested.append((query, kwargs))
            selected = {
                "receipt corrected": keys[:2],
                "updated invoice": keys[1:3],
                "charges revised": keys[3:],
            }[query]
            return [{"text": query, "metadata": {"docSource": k}} for k in selected]

    result = await call(
        build(catalog, Variants()),
        "search_mail",
        {
            "query": "receipt corrected",
            "alternate_queries": ["updated invoice", "charges revised"],
            "subject": "Itinerary",
            "max_bytes": 16000,
        },
    )
    assert {q for q, _ in requested} == {"receipt corrected", "updated invoice", "charges revised"}
    assert all(set(kw["message_ids"]) == set(keys) for _, kw in requested)
    assert [h["message_id"] for h in result["sources"]] == [keys[0], keys[1], keys[3], keys[2]]
    assert result["sources"][1]["text"] == "updated invoice"
    assert result["exhaustive"] is False


async def test_exact_rejects_semantic_alternatives(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    seed(catalog)
    with pytest.raises(Exception, match="hybrid"):
        await call(
            build(catalog, object()),
            "search_mail",
            {"query": "receipt", "mode": "exact", "alternate_queries": ["invoice"]},
        )


async def test_hybrid_alternatives_cursor_preserves_queries_and_rejects_changes(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    keys = seed(catalog, 10)
    server = build(catalog, Ranked(keys, text="日本語 " * 400))
    result = await call(
        server,
        "search_mail",
        {"query": "receipt", "alternate_queries": ["invoice"], "max_bytes": 4000},
    )
    assert result["has_more"]
    with pytest.raises(Exception, match="changed"):
        await call(
            server, "search_mail", {"cursor": result["next_cursor"], "alternate_queries": []}
        )
    following = await call(server, "search_mail", result["next_call"]["arguments"])
    assert wire_size(following) <= 4000
    assert following["sources"][0]["message_id"] == keys[1]
