"""Evidence retrieval: find deep facts in few calls without losing active-index boundaries."""

import json
import pytest
from proton_rag.catalog import Catalog
from proton_rag.mcp_server import build
from proton_rag.retrieval import wire_size
from test_mail_reading import call
from test_retrieval import seed


async def test_grep_finds_deep_facts_and_filters_retired_and_out_of_year(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    keys = seed(catalog, 3, text="Prelude " * 2000 + "Invoice INV-2048 total $187.45 due tomorrow.")
    catalog.retire(keys[1])
    with catalog.connect() as db:
        db.execute("UPDATE message_dates SET sent_at=0 WHERE key=?", (keys[2],))
    server = build(catalog, object())
    result = await call(
        server,
        "grep_mail",
        {"pattern": r"INV-\d+|total", "sent_after": "2018-01-01", "sent_before": "2019-01-01"},
    )
    assert result["matched_messages"] == 1
    assert result["has_more"] is False
    assert len(result["sources"]) == 1
    hit = result["sources"][0]
    assert "INV-2048 total $187.45" in " ".join(hit["passages"])
    assert hit["citation"].startswith("imap:///")
    assert result["coverage"]["unique_full_text_available"] >= 1
    full = await call(server, "read_mail", {"message_id": hit["citation"]})
    assert full["text"].startswith("Prelude ")


async def test_grep_pages_all_unicode_evidence_rechecks_active_membership(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    keys = seed(catalog, 60, text="Receipt رقم١٢٣ 🦊 paid 77.50. " + "details " * 100)
    server = build(catalog, object())
    result = await call(server, "grep_mail", {"pattern": "Receipt", "max_bytes": 4000})
    seen = []
    assert result["has_more"]
    first_citations = [x["citation"] for x in result["sources"]]
    last = await call(server, "read_mail", {"message_id": keys[-1]})
    assert last["citation"] not in first_citations
    catalog.retire(keys[-1])
    while True:
        assert wire_size(result) <= 4000
        seen.extend(x["citation"] for x in result["sources"])
        assert all("رقم١٢٣" in " ".join(x["passages"]) for x in result["sources"])
        if not result["has_more"]:
            break
        result = await call(server, "grep_mail", result["next_call"]["arguments"])
    assert len(seen) == len(set(seen)) == 59
    assert last["citation"] not in seen


async def test_batch_large_metadata_is_queued_instead_of_failing(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    keys = seed(catalog, 5, text="Useful receipt evidence. " * 40)
    with catalog.connect() as db:
        for key in keys:
            metadata = {
                "subject": "Long subject " * 40,
                "sender": "shop@example.test",
                "recipients": "Customer <customer@example.test>",
            }
            db.execute("UPDATE messages SET metadata=? WHERE key=?", (json.dumps(metadata), key))
    server = build(catalog, object())
    result = await call(server, "read_mail_batch", {"message_ids": keys})
    recovered = {i: "" for i in range(5)}
    pages = 0
    while True:
        pages += 1
        assert wire_size(result) <= 3900
        for value in result["messages"]:
            assert "error" not in value
            assert len(value["text"]) >= 100 or value["next_offset"] is None
            recovered[value["request_index"]] += value["text"]
        if result["next_call"] is None:
            break
        assert pages < 30
        result = await call(server, "read_mail_batch", result["next_call"]["arguments"])
    assert all(text == "Useful receipt evidence. " * 40 for text in recovered.values())


async def test_grep_nonblank_context_merges_windows_and_preserves_original(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    text = "intro\n\n  Build  failed\n\nerror E2048\n\n retry  tomorrow\nend\n"
    keys = seed(catalog, text=text)
    server = build(catalog, object())
    page = await call(server, "grep_mail", {"pattern": "failed|E2048", "context": 1})
    assert page["sources"][0]["passages"] == ["intro\nBuild failed\nerror E2048\nretry tomorrow"]
    assert catalog.get_text(keys[0])["text"] == text


async def test_grep_cross_year_conflicts_and_extraction_warnings(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    keys = seed(
        catalog,
        2,
        text="Reservation for Jan 2 2018\nFlight AA 123 standby\nUpdated AA 456 confirmed",
        date="Sun, 31 Dec 2017 12:00:00 +0000",
    )
    catalog.store_text(
        keys[1], "Receipt paid $19.95\nFlight AA 999 cancelled", ["image/png: unsupported"], True
    )
    server = build(catalog, object())
    page = await call(
        server, "grep_mail", {"pattern": "AA|paid", "message_ids": [keys[1]], "context": 2}
    )
    assert len(page["sources"]) == 1
    assert page["sources"][0]["extraction"]["text_truncated"] is True
    assert page["sources"][0]["extraction"]["skipped_parts"] == ["image/png: unsupported"]
    page = await call(server, "grep_mail", {"pattern": "AA", "context": 2})
    assert "standby" in " ".join(page["sources"][0]["passages"])
    assert "confirmed" in " ".join(page["sources"][0]["passages"])
    assert "2017" in page["sources"][0]["metadata"]["date"]


async def test_grep_single_large_passage_continues_without_losing_evidence(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    text = "Fact: " + "日本語🦊" * 2500
    seed(catalog, text=text)
    server = build(catalog, object())
    page = await call(server, "grep_mail", {"pattern": r"Fact:.*", "max_bytes": 4000, "context": 0})
    recovered = ""
    rounds = 0
    while True:
        assert wire_size(page) <= 4000
        recovered += "".join(page["sources"][0]["passages"])
        if not page["has_more"]:
            break
        assert page["next_call"]["tool"] == "grep_mail"
        assert list(page["next_call"]["arguments"]) == ["cursor"]
        rounds += 1
        assert rounds < 100
        page = await call(server, "grep_mail", page["next_call"]["arguments"])
    assert recovered == text


async def test_grep_rejects_malformed_and_expensive_patterns_without_completeness(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    seed(catalog, text="a" * 100000 + "!")
    server = build(catalog, object())
    for pattern, error in [("(", "pattern"), ("(a+)+$", "expensive|timeout|limit")]:
        with pytest.raises(Exception, match=error):
            await server.call_tool("grep_mail", {"pattern": pattern})


async def test_batch_metadata_that_cannot_fit_alone_has_explicit_fallback(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    keys = seed(catalog, 2, text="Useful technical evidence. " * 20)
    with catalog.connect() as db:
        db.execute(
            "UPDATE messages SET metadata=? WHERE key=?",
            (json.dumps({"subject": "x" * 10000}), keys[0]),
        )
    server = build(catalog, object())
    page = await call(server, "read_mail_batch", {"message_ids": keys})
    assert "read_mail" in page["messages"][0]["error"]
    assert page["messages"][1]["text"].startswith("Useful technical")
    assert page["messages"][1]["request_index"] == 1
    assert wire_size(page) <= 3900


def test_scanner_cursors_expire_and_arguments_cannot_change(tmp_path):
    from proton_rag.evidence import Evidence

    catalog = Catalog(tmp_path / "catalog.db")
    seed(catalog, 8, text="Receipt paid. " * 100)
    now = [0.0]
    scanner = Evidence(catalog, clock=lambda: now[0], ttl=1)
    page = scanner.query(pattern="Receipt", max_bytes=4000)
    cursor = page["next_call"]["arguments"]["cursor"]
    with pytest.raises(ValueError, match="cursor alone"):
        scanner.query(cursor=cursor, pattern="paid")
    now[0] = 2
    with pytest.raises(ValueError, match="expired"):
        scanner.query(cursor=cursor)


def test_scanner_filters_uncommitted_and_rechecks_commit_and_folder(tmp_path):
    from proton_rag.evidence import Evidence

    catalog = Catalog(tmp_path / "catalog.db")
    keys = seed(catalog, 8, text="Receipt evidence. " * 100)
    with catalog.connect() as db:
        db.execute("CREATE TABLE index_messages (key TEXT PRIMARY KEY, phase TEXT)")
        db.executemany("INSERT INTO index_messages VALUES (?,?)", [(key, "ready") for key in keys])
        db.execute("UPDATE index_messages SET phase='staging' WHERE key=?", (keys[0],))
    scanner = Evidence(catalog)
    page = scanner.query(pattern="Receipt", selected={"folder": "INBOX"}, max_bytes=4000)
    assert page["matched_messages"] == 7
    assert keys[0] not in [s["message_id"] for s in page["sources"]]
    with catalog.connect() as db:
        db.execute("UPDATE index_messages SET phase='staging' WHERE key=?", (keys[-1],))
    seen = [s["message_id"] for s in page["sources"]]
    while page["has_more"]:
        page = scanner.query(**page["next_call"]["arguments"])
        seen.extend(s["message_id"] for s in page["sources"])
    assert keys[-1] not in seen
    assert set(seen) == set(keys[1:-1])


def test_scanner_rejects_time_and_snapshot_memory_overruns(tmp_path):
    from proton_rag.evidence import Evidence

    catalog = Catalog(tmp_path / "catalog.db")
    seed(catalog, text="Technical error E2048\n" * 100)
    with pytest.raises(ValueError, match="time limit"):
        Evidence(catalog, scan_seconds=0).query(pattern="error")
    with pytest.raises(ValueError, match="memory limit"):
        Evidence(catalog, max_memory=4000).query(pattern="error")


async def test_grep_filter_casefold_citation_ids_and_missing_text(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    keys = seed(catalog, 2, text="Receipt paid $39.99")
    with catalog.connect() as db:
        db.execute("DELETE FROM message_text WHERE key=?", (keys[1],))
    server = build(catalog, object())
    page = await call(
        server,
        "grep_mail",
        {"pattern": "paid", "sender": "AIR@", "subject": "ITINERARY", "folder": "INBOX"},
    )
    assert page["searched_messages"] == 1
    assert page["unavailable_messages"] == 1
    citation_id = page["sources"][0]["citation"]
    page = await call(server, "grep_mail", {"pattern": r"\$39\.99", "message_ids": [citation_id]})
    assert page["sources"][0]["message_id"] == keys[0]
    assert page["search_complete"] is True
    with pytest.raises(Exception, match="not found"):
        await server.call_tool("grep_mail", {"pattern": "paid", "message_ids": ["unknown"]})


async def test_grep_repeated_passages_remain_distinct(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    seed(catalog, text="Flight AA 123 confirmed\nnoise\nnoise\nFlight AA 123 cancelled")
    page = await call(build(catalog, object()), "grep_mail", {"pattern": "AA 123", "context": 0})
    assert page["sources"][0]["passages"] == ["Flight AA 123 confirmed", "Flight AA 123 cancelled"]


async def test_grep_continuation_refreshes_citation_after_folder_copy_retired(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    keys = seed(catalog, 8, text="Receipt evidence. " * 100)
    catalog.intent("archive-copy", "Archive", "1", "99", "7", False, {})
    catalog.activate("archive-copy", [])
    server = build(catalog, object())
    page = await call(server, "grep_mail", {"pattern": "Receipt", "max_bytes": 4000})
    assert keys[-1] not in [s["message_id"] for s in page["sources"]]
    catalog.retire(keys[-1])
    while page["has_more"]:
        page = await call(server, "grep_mail", page["next_call"]["arguments"])
        for source in page["sources"]:
            full = await call(server, "read_mail", {"message_id": source["citation"]})
            assert full["message_id"] == source["message_id"]


async def test_batch_valid_metadata_is_deferred_when_errors_consume_space(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    keys = seed(catalog, text="x" * 1000)
    with catalog.connect() as db:
        db.execute(
            "UPDATE messages SET metadata=? WHERE key=?",
            (json.dumps({"subject": "x" * 2900}), keys[0]),
        )
    server = build(catalog, object())
    page = await call(
        server,
        "read_mail_batch",
        {"message_ids": [*keys, "unknown1", "unknown2", "unknown3", "unknown4"]},
    )
    assert all(v["request_index"] != 0 for v in page["messages"])
    assert page["next_call"]
    page = await call(server, "read_mail_batch", page["next_call"]["arguments"])
    assert page["messages"][0]["request_index"] == 0
    assert "error" not in page["messages"][0]
    assert len(page["messages"][0]["text"]) >= 100


async def test_grep_canonical_id_resolves_active_copy_after_original_retired(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    keys = seed(catalog, text="Receipt paid $37.50")
    catalog.intent("archive-copy", "Archive", "1", "99", "0", False, {})
    catalog.activate("archive-copy", [])
    catalog.retire(keys[0])
    server = build(catalog, object())
    page = await call(server, "grep_mail", {"pattern": "Receipt", "message_ids": keys})
    assert page["sources"][0]["message_id"] == keys[0]
    assert page["sources"][0]["citation"] == "imap:///Archive/1/99#0"


async def test_grep_nonblank_context_survives_large_blank_gaps(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    text = "Flight\n" + "   \r\n" * 200 + "Wed Oct 25\n" + "  \r\n" * 200 + "1833\n"
    seed(catalog, text=text)
    page = await call(build(catalog, object()), "grep_mail", {"pattern": "Flight", "context": 2})
    assert page["sources"][0]["passages"] == ["Flight\nWed Oct 25\n1833"]
    assert page["sources"][0]["context_clipped"] is False


async def test_grep_prioritizes_metadata_matches_over_incidental_body_mentions(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    keys = seed(
        catalog, 8, text="Long technical discussion with an incidental receipt mention. " * 100
    )
    catalog.store_text(keys[-1], "Receipt total $37.50 paid", [], False)
    with catalog.connect() as db:
        db.execute(
            "UPDATE messages SET metadata=? WHERE key=?",
            (json.dumps({"subject": "Receipt", "sender": "shop@example.test"}), keys[-1]),
        )
    build_server = build(catalog, object())
    page = await call(build_server, "grep_mail", {"pattern": "receipt", "max_bytes": 4000})
    assert page["sources"][0]["message_id"] == keys[-1]
    assert "total $37.50" in "".join(page["sources"][0]["passages"])
    seen = []
    while True:
        seen.extend(s["message_id"] for s in page["sources"])
        if not page["has_more"]:
            break
        page = await call(build_server, "grep_mail", page["next_call"]["arguments"])
    assert set(seen) == set(keys)


def test_scanner_does_not_claim_completion_after_final_header_regex_overrun(tmp_path, monkeypatch):
    from proton_rag import evidence

    catalog = Catalog(tmp_path / "catalog.db")
    seed(catalog, text="Receipt paid")
    now = [0.0]
    timeouts = []
    original = evidence.regex.compile

    class MeasuredRegex:
        def __init__(self, *args):
            self.real = original(*args)

        def finditer(self, text, **kwargs):
            for match in self.real.finditer(text, **kwargs):
                now[0] += 0.02
                yield match

        def search(self, text, **kwargs):
            timeouts.append(kwargs["timeout"])
            now[0] += 0.06
            return self.real.search(text, **kwargs)

    monkeypatch.setattr(evidence.regex, "compile", MeasuredRegex)
    scanner = evidence.Evidence(catalog, clock=lambda: now[0], scan_seconds=0.1, regex_seconds=0.1)
    with pytest.raises(ValueError, match="time limit|timeout"):
        scanner.query(pattern="Receipt")
    assert timeouts[1] < timeouts[0]
