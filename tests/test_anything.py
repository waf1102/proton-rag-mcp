from unittest.mock import Mock
import pytest
from proton_rag.anything import Anything

KEY = "proton-mail-" + "a" * 64


def test_unknown_upload_is_not_blindly_replayed():
    backend = Anything("http://127.0.0.1:3001", "fixture")
    backend.find = Mock(return_value=[])
    backend.request = Mock()
    with pytest.raises(RuntimeError, match="reconciliation"):
        backend.recover(KEY, "Synthetic cobalt")
    backend.request.assert_not_called()


def test_recover_committed_upload_reuses_document():
    backend = Anything("http://127.0.0.1:3001", "fixture")
    backend.find = Mock(return_value=["custom-documents/fixture.json"])
    backend.request = Mock(return_value={"success": True})
    paths = backend.recover(KEY, "Synthetic cobalt")
    assert paths == ["custom-documents/fixture.json"]
    assert backend.request.call_count == 1
    assert backend.request.call_args.args[1].endswith("/update-embeddings")


def test_nonlocal_index_refused():
    with pytest.raises(ValueError):
        Anything("https://example.com", "fixture")


def test_document_listing_is_cached_and_upload_updates_cache():
    backend = Anything("http://127.0.0.1:3001", "fixture")
    backend.request = Mock(
        side_effect=[
            {"localFiles": {"type": "folder", "name": "documents", "items": []}},
            {"documents": [{"location": "custom-documents/one.json"}]},
            {"success": True},
        ]
    )
    backend.ensure(KEY, "body")
    assert backend.find(KEY) == ["custom-documents/one.json"]
    assert backend.request.call_count == 3


@pytest.mark.parametrize("suffix", ["", ".txt"])
def test_recovery_recognizes_raw_text_document_titles(suffix):
    backend = Anything("http://127.0.0.1:3001", "fixture")
    backend.request = Mock(
        side_effect=[
            {
                "localFiles": {
                    "type": "folder",
                    "name": "documents",
                    "items": [
                        {
                            "type": "folder",
                            "name": "custom-documents",
                            "items": [
                                {"type": "file", "name": "existing.json", "title": KEY + suffix}
                            ],
                        }
                    ],
                }
            },
            {"success": True},
        ]
    )
    assert backend.recover(KEY, "body") == ["custom-documents/existing.json"]
    assert backend.request.call_count == 2
    assert backend.request.call_args.args[1].endswith("/update-embeddings")
