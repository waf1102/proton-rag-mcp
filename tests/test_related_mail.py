"""Related-message evidence must expose replies without claiming global thread coverage."""

import json

import pytest

from proton_rag.catalog import Catalog
from proton_rag.mcp_server import build
from proton_rag.retrieval import wire_size
from test_mail_reading import call


def message(catalog, i, subject, body, *, sender="peer@example.test", recipient="me@example.test"):
    key = "proton-mail-" + format(i, "064x")
    metadata = {
        "subject": subject,
        "sender": sender,
        "recipients": recipient,
        "date": f"Wed, {i:02d} Jun 2018 12:00:00 +0000",
    }
    catalog.intent(key, "INBOX", "1", str(i), str(i), False, metadata)
    catalog.store_text(key, f"subject: {subject}\nsender: {sender}\n\n{body}", [], False)
    catalog.activate(key, [])
    return key


async def test_related_finds_reply_and_preserves_quoted_context(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    seed = message(
        catalog,
        1,
        "Service Record",
        "Did you find my record?",
        sender="me@example.test",
        recipient="peer@example.test",
    )
    reply = message(
        catalog,
        2,
        "RE: Fwd: Service Record",
        "I have it. We can meet Friday.\n\nOn Tuesday, Me wrote:\nDid you find my record?",
    )
    message(
        catalog, 3, "Service Record", "Different person's records", sender="stranger@example.test"
    )
    result = await call(build(catalog, object()), "related_mail", {"message_id": seed})
    assert [s["message_id"] for s in result["sources"]] == [reply, seed]
    assert "I have it. We can meet Friday." in result["sources"][0]["text"]
    assert "Did you find" in result["sources"][0]["text"]
    assert result["sources"][0]["quoted_history_omitted"] is False
    assert result["matched_messages"] == 2
    assert result["has_more"] is False
    assert result["exhaustive"] is False


async def test_related_subject_override_includes_renamed_exchange(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    seed = message(catalog, 1, "Service Record Community Hours", "I will update the hours")
    reply = message(catalog, 2, "Re: Service Record", "Here are the records.")
    result = await call(
        build(catalog, object()), "related_mail", {"message_id": seed, "subject": "Service Record"}
    )
    assert [s["message_id"] for s in result["sources"]] == [reply, seed]


async def test_related_cursor_preserves_budget_rechecks_deletion_and_rejects_changed_arguments(
    tmp_path,
):
    catalog = Catalog(tmp_path / "catalog.db")
    keys = [message(catalog, i, "Receipt correction", "日本語 🦊 " * 300) for i in range(1, 9)]
    server = build(catalog, object())
    result = await call(server, "related_mail", {"message_id": keys[0], "max_bytes": 4000})
    assert result["has_more"]
    assert wire_size(result) <= 4000
    first = [s["message_id"] for s in result["sources"]]
    with pytest.raises(Exception, match="cursor alone"):
        await server.call_tool(
            "related_mail",
            {"cursor": result["next_call"]["arguments"]["cursor"], "subject": "Changed"},
        )
    catalog.retire(keys[1])
    recovered = first
    while result["has_more"]:
        result = await call(server, "related_mail", result["next_call"]["arguments"])
        assert wire_size(result) <= 4000
        recovered += [s["message_id"] for s in result["sources"]]
    assert recovered == [key for key in reversed(keys) if key != keys[1]]


async def test_related_excludes_uncommitted_content_and_reports_skipped_attachments(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    seed = message(catalog, 1, "Technical change", "I will deploy the change")
    pending = message(catalog, 2, "Re: Technical change", "Deployment completed")
    with catalog.connect() as db:
        db.execute("CREATE TABLE index_messages (key TEXT PRIMARY KEY, phase TEXT)")
        db.executemany(
            "INSERT INTO index_messages VALUES (?,?)", [(seed, "ready"), (pending, "uploading")]
        )
    catalog.store_text(
        seed,
        "The deployment plan is attached",
        [{"name": "scan.png", "reason": "unsupported"}],
        False,
    )
    result = await call(build(catalog, object()), "related_mail", {"message_id": seed})
    assert [s["message_id"] for s in result["sources"]] == [seed]
    assert result["sources"][0]["extraction"]["skipped_parts"] == [
        {"name": "scan.png", "reason": "unsupported"}
    ]
    assert "unsupported" in json.dumps(result)


async def test_related_does_not_treat_technical_from_field_as_quoted_history(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    text = "From: service@example.test\nTo: relay@example.test\nUse these values in the relay configuration."
    seed = message(catalog, 1, "Relay configuration", text)
    result = await call(build(catalog, object()), "related_mail", {"message_id": seed})
    assert result["sources"][0]["text"] == text
    assert result["sources"][0]["quoted_history_omitted"] is False


async def test_related_preserves_inline_completion_and_quoted_context(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    text = "See my answers inline.\n> Can you send the corrected receipt?\nYes, attached. It is now corrected."
    seed = message(catalog, 1, "Receipt correction", text)
    result = await call(build(catalog, object()), "related_mail", {"message_id": seed})
    assert "Yes, attached. It is now corrected." in result["sources"][0]["text"]


async def test_correspondence_candidates_are_memory_and_time_bounded(tmp_path):
    from proton_rag.related import correspondence_keys

    catalog = Catalog(tmp_path / "catalog.db")
    message(catalog, 1, "Re: Technical reply", "Done")
    with pytest.raises(ValueError, match="memory limit"):
        correspondence_keys(catalog, {}, max_bytes=1)
    with pytest.raises(ValueError, match="time limit"):
        correspondence_keys(catalog, {}, scan_seconds=0)


async def test_related_preserves_html_flattened_reply_and_history(tmp_path):
    from proton_rag.extract import parse

    raw = b"Content-Type: text/html\r\n\r\n<div>Done, the receipt is attached.</div><div>On Tuesday, Me wrote:</div><blockquote>I will send tomorrow.</blockquote>"
    extracted = parse(raw)
    catalog = Catalog(tmp_path / "catalog.db")
    seed = message(catalog, 1, "Corrected receipt", extracted["text"])
    result = await call(build(catalog, object()), "related_mail", {"message_id": seed})
    assert "Done, the receipt is attached." in result["sources"][0]["text"]
    assert "I will send tomorrow." in result["sources"][0]["text"]
    assert result["sources"][0]["quoted_history_omitted"] is False
    assert "quoted history" in result["notice"]


def test_related_expiry_and_retired_seed(tmp_path):
    from proton_rag.related import RelatedMail

    catalog = Catalog(tmp_path / "catalog.db")
    keys = [message(catalog, i, "Receipt correction", "日本語 🦊 " * 300) for i in range(1, 4)]
    now = [0]
    reader = RelatedMail(catalog, clock=lambda: now[0], ttl=1)
    result = reader.query(message_id=keys[0], max_bytes=4000)
    now[0] = 2
    with pytest.raises(ValueError, match="expired"):
        reader.query(**result["next_call"]["arguments"])
    result = reader.query(message_id=keys[0], max_bytes=4000)
    catalog.retire(keys[0])
    with pytest.raises(ValueError, match="Seed message is unavailable"):
        reader.query(**result["next_call"]["arguments"])
