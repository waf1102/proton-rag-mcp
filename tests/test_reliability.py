"""Recovery and diagnostics must preserve indexed mail and redact private data."""

import json
from unittest.mock import Mock
import httpx
import pytest
from proton_rag.anything import Anything
from proton_rag.config import Settings
from proton_rag.mailbox import Inventory, Snapshot
from proton_rag.sync import Catalog, synchronize, synchronize_folder
from test_sync import Backend


@pytest.mark.parametrize(
    "failure,code,status",
    [
        (httpx.ReadTimeout("private payload"), "index_timeout", None),
        (httpx.ConnectError("Bearer private-key"), "index_connection", None),
        (
            httpx.HTTPStatusError(
                "private body",
                request=httpx.Request("POST", "http://localhost"),
                response=httpx.Response(503),
            ),
            "index_http",
            503,
        ),
    ],
)
def test_backend_failure_reports_safe_reason(monkeypatch, failure, code, status):
    import proton_rag.anything as module

    wire = Mock()
    wire.request.side_effect = failure
    monkeypatch.setattr(module.httpx, "Client", lambda **kwargs: wire)
    wire.__enter__ = Mock(return_value=wire)
    wire.__exit__ = Mock(return_value=False)
    with pytest.raises(RuntimeError) as caught:
        Anything("http://localhost:3001", "private-key").request(
            "POST", "/workspace/proton-mail/update-embeddings", {"text": "private payload"}
        )
    assert caught.value.diagnostic["code"] == code
    assert caught.value.diagnostic["operation"] == "embed"
    assert caught.value.diagnostic.get("http_status") == status
    assert "private" not in json.dumps(caught.value.diagnostic)
    assert "private" not in str(caught.value)


def test_runtime_status_survives_reopen_and_is_returned_by_mcp(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    catalog.update_runtime(state="paused", reason="low_disk", free_bytes=17)
    reopened = Catalog(catalog.path)
    status = reopened.coverage()["runtime"]
    assert status["state"] == "paused" and status["reason"] == "low_disk"
    assert status["free_bytes"] == 17 and status["updated_at"]


def test_disk_guard_pauses_and_resumes_without_touching_mail(tmp_path, monkeypatch):
    import proton_rag.health as module

    catalog = Catalog(tmp_path / "catalog.db")
    settings = Settings(state_dir=tmp_path, min_free_bytes=100, warn_free_bytes=200)
    usage = Mock(total=1000, used=950, free=50)
    monkeypatch.setattr(module.shutil, "disk_usage", lambda path: usage)
    with pytest.raises(module.DiskLowError):
        module.check_storage(settings, catalog)
    assert catalog.coverage()["runtime"]["state"] == "paused"
    assert catalog.rows() == {}
    usage.free = 300
    module.check_storage(settings, catalog)
    assert Catalog(catalog.path).coverage()["runtime"]["state"] == "running"


def test_low_disk_between_batches_preserves_existing_index(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    backend = Backend()
    synchronize(catalog, backend, Snapshot("1", {"9": b"\nold mail"}))
    box = Mock()
    box.inventory.return_value = Inventory("INBOX", "1", ("1", "2"))
    box.fetch.return_value = b"\nnew mail"
    checks = 0

    def guard():
        nonlocal checks
        checks += 1
        if checks == 2:
            raise RuntimeError("low disk")

    with pytest.raises(RuntimeError, match="low disk"):
        synchronize_folder(
            catalog, backend, box, "INBOX", Settings(batch_size=1), before_batch=guard
        )
    assert {r["uid"] for r in catalog.rows().values()} == {"9", "2"}
    assert len(backend.docs) == 2
    box.verify.assert_not_called()


def test_config_rejects_disk_warning_below_pause_limit():
    with pytest.raises(ValueError, match="disk"):
        Settings.from_env({"RAG_MIN_FREE_BYTES": "200", "RAG_WARN_FREE_BYTES": "100"})


async def test_mcp_reports_persisted_pause_and_last_error(tmp_path):
    from test_mail_reading import call
    from proton_rag.mcp_server import build

    catalog = Catalog(tmp_path / "catalog.db")
    catalog.update_runtime(
        state="paused",
        reason="low_disk",
        last_error={"code": "index_timeout", "operation": "embed", "at": "2026-10-09T12:00:00Z"},
    )
    report = await call(build(Catalog(catalog.path), object()), "index_status", {})
    assert report["runtime"]["state"] == "paused"
    assert report["runtime"]["last_error"]["code"] == "index_timeout"


def test_embedding_requests_get_longer_read_deadline(monkeypatch):
    import proton_rag.anything as module

    requests = []
    original = httpx.Client

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"success": True})

    monkeypatch.setattr(
        module.httpx,
        "Client",
        lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(respond)),
    )
    backend = Anything("http://127.0.0.1:3001", "fixture", embedding_timeout=240)
    backend.request("POST", "/workspace/proton-mail/update-embeddings", {"adds": []})
    backend.request("POST", "/document/raw-text", {"textContent": "synthetic"})
    assert requests[0].extensions["timeout"]["read"] == 240
    assert requests[1].extensions["timeout"]["read"] == 60
    assert requests[0].extensions["timeout"]["connect"] == 10
