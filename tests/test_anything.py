from unittest.mock import Mock
import pytest
from proton_rag.anything import Anything

KEY = "proton-test-1-7-" + "a" * 64


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
