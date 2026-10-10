import uuid

from proton_rag.chunks import split_text, point_id


def test_point_ids_are_stable_and_namespace_scoped():
    first = point_id("account-a", "message", "profile", "chunk")
    assert uuid.UUID(first).version == 5
    assert first == point_id("account-a", "message", "profile", "chunk")
    assert first != point_id("account-b", "message", "profile", "chunk")


def test_chunks_preserve_unicode_and_long_paragraphs():
    text = "日本語🦊" * 1300 + "\n\nEnd of the attachment."
    chunks = split_text(text)
    recovered = chunks[0].text
    for chunk in chunks[1:]:
        recovered += chunk.text[200:]
    assert recovered == text
    assert max(len(c.text) for c in chunks) <= 1800
    assert chunks == split_text(text)
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    assert split_text("") == []
