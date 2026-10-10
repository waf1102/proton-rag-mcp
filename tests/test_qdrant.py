import httpx
import pytest
from proton_rag.catalog import Catalog
from proton_rag.config import Settings
from proton_rag.embeddings import EmbeddingProfile, ContextExceeded
from proton_rag.index import IndexFailure
from proton_rag.index_state import IndexState
from proton_rag.qdrant import QdrantIndex


class Embedder:
    profile = EmbeddingProfile("nomic-embed-text", "digest-a")

    def check_profile(self):
        pass

    def embed_documents(self, texts):
        return [[1.0] + [0.0] * 767 for text in texts]

    async def embed_query(self, text):
        return [1.0] + [0.0] * 767


class Service:
    def __init__(self):
        self.points = {}
        self.lost = False
        self.collection = None

    def __call__(self, request):
        import json

        body = json.loads(request.content) if request.content else {}
        path = request.url.path
        if path.endswith("/index"):
            return httpx.Response(200, json={"result": {"status": "completed"}})
        if path.endswith("/points/query/groups"):
            groups = [
                {
                    "id": p["payload"]["message_id"],
                    "hits": [{"id": p["id"], "payload": p["payload"]}],
                }
                for p in self.points.values()
            ]
            return httpx.Response(200, json={"result": {"groups": groups}})
        if path.endswith("/points/delete"):
            key = body["filter"]["must"][0]["match"]["value"]
            self.points = {
                k: p for k, p in self.points.items() if p["payload"]["message_id"] != key
            }
            return httpx.Response(200, json={"result": {"status": "completed"}})
        if path.endswith("/points/count"):
            key = body.get("filter", {}).get("must", [{"match": {"value": None}}])[0]["match"][
                "value"
            ]
            return httpx.Response(
                200,
                json={
                    "result": {
                        "count": sum(
                            key is None or p["payload"]["message_id"] == key
                            for p in self.points.values()
                        )
                    }
                },
            )
        if path.endswith("/points"):
            if request.method == "POST":
                return httpx.Response(
                    200, json={"result": [self.points[k] for k in body["ids"] if k in self.points]}
                )
            self.points.update({p["id"]: p for p in body["points"]})
            if self.lost:
                self.lost = False
                raise httpx.ReadTimeout("Private payload was lost")
            return httpx.Response(200, json={"result": {"status": "completed"}})
        if request.method == "PUT":
            self.collection = body
            return httpx.Response(200, json={"result": True})
        if self.collection is None:
            return httpx.Response(404, json={"status": {"error": "Not found"}})
        return httpx.Response(200, json={"result": {"config": {"params": self.collection}}})


def setup_index(tmp_path, *, service=None, embedder=None):
    catalog = Catalog(tmp_path / "catalog.db")
    service = service or Service()
    model = embedder or Embedder()
    settings = Settings(state_dir=tmp_path)
    state = IndexState(catalog, "proton-mail", model.profile)
    backend = QdrantIndex(
        settings, catalog, state, model, client=httpx.Client(transport=httpx.MockTransport(service))
    )
    backend.bootstrap()
    return catalog, state, backend, service


def test_timeout_replays_same_ids(tmp_path):
    catalog, state, backend, service = setup_index(tmp_path)
    key = "proton-mail-" + "a" * 64
    service.lost = True
    with pytest.raises(IndexFailure):
        backend.ensure(key, "Cobalt invoice")
    assert not state.ready(key)
    ids = set(service.points)
    assert backend.recover(key, "Cobalt invoice") == [key]
    assert set(service.points) == ids
    assert state.ready(key)


def test_restart_after_upsert_before_manifest_commit(tmp_path, monkeypatch):
    catalog, state, backend, service = setup_index(tmp_path)
    key = "proton-mail-" + "a" * 64
    monkeypatch.setattr(state, "finish", lambda *a: (_ for _ in ()).throw(RuntimeError("crash")))
    with pytest.raises(RuntimeError):
        backend.ensure(key, "cobalt invoice")
    new_state = IndexState(catalog, "proton-mail", Embedder.profile)
    reopened = QdrantIndex(
        Settings(state_dir=tmp_path), catalog, new_state, Embedder(), client=backend.client
    )
    reopened.recover(key, "cobalt invoice")
    assert len(service.points) == 1
    assert new_state.ready(key)


def test_empty_text_has_ready_zero_chunk_manifest(tmp_path):
    catalog, state, backend, service = setup_index(tmp_path)
    key = "proton-mail-" + "a" * 64
    backend.ensure(key, "")
    assert state.ready(key)
    assert not service.points


def test_remove_is_scoped_and_idempotent(tmp_path):
    catalog, state, backend, service = setup_index(tmp_path)
    first, second = ["proton-mail-" + c * 64 for c in "ab"]
    backend.ensure(first, "cobalt")
    backend.ensure(second, "saffron")
    backend.remove([first])
    backend.remove([first])
    assert not state.ready(first)
    assert state.ready(second)
    assert {p["payload"]["message_id"] for p in service.points.values()} == {second}


def test_embedding_context_rejection_splits_without_truncation(tmp_path):
    class SmallModel(Embedder):
        def embed_documents(self, texts):
            if any(len(t) > 100 for t in texts):
                raise ContextExceeded()
            return super().embed_documents(texts)

    _, state, backend, service = setup_index(tmp_path, embedder=SmallModel())
    key = "proton-mail-" + "a" * 64
    text = "日本語🦊" * 100
    backend.ensure(key, text)
    chunks = sorted(service.points.values(), key=lambda p: p["payload"]["ordinal"])
    assert "".join(p["payload"]["text"] for p in chunks) == text
    assert state.ready(key)


async def test_uncommitted_or_orphan_hits_are_hidden(tmp_path):
    catalog, state, backend, service = setup_index(tmp_path)
    key = "proton-mail-" + "a" * 64
    backend.ensure(key, "cobalt")
    assert await backend.search("cobalt", 10) == []
    catalog.intent(key, "INBOX", "1", "7", "digest", False, {})
    catalog.activate(key, [key])
    hits = await backend.search("cobalt", 10)
    assert hits[0]["metadata"]["docSource"] == key
    with catalog.connect() as db:
        db.execute("UPDATE index_messages SET phase='pending'")
    assert await backend.search("cobalt", 10) == []


def test_backend_binding_mismatch_refuses_startup(tmp_path):
    catalog, state, backend, service = setup_index(tmp_path)
    with pytest.raises(ValueError, match="binding"):
        IndexState(catalog, "another-collection", Embedder.profile)


def test_manifest_is_not_ready_after_partial_batch(tmp_path):
    class PartialService(Service):
        writes = 0

        def __call__(self, req):
            if req.method == "PUT" and req.url.path.endswith("/points"):
                self.writes += 1
                if self.writes == 2:
                    return httpx.Response(503, json={"status": {"error": "Private error"}})
            return super().__call__(req)

    service = PartialService()
    _, state, backend, _ = setup_index(tmp_path, service=service)
    key = "proton-mail-" + "a" * 64
    with pytest.raises(IndexFailure):
        backend.ensure(key, "Cobalt sentence. " * 5000)
    assert len(service.points) == 32
    assert not state.ready(key)
    backend.recover(key, "Cobalt sentence. " * 5000)
    assert state.ready(key)
    assert len(service.points) == len(state.entry(key)["points"])


def test_existing_unbound_catalog_is_rejected_without_schema_changes(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    catalog.intent("proton-mail-" + "a" * 64, "INBOX", "1", "7", "digest", False, {})
    with catalog.connect() as db:
        before = set(r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'"))
    with pytest.raises(ValueError, match="migration"):
        IndexState(catalog, "proton-mail", Embedder.profile)
    with catalog.connect() as db:
        after = set(r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'"))
    assert after == before


async def test_index_readiness_is_separate_from_mailbox_coverage(tmp_path):
    from proton_rag.mcp_server import build
    from test_mail_reading import call

    catalog, state, backend, service = setup_index(tmp_path)
    key = "proton-mail-" + "a" * 64
    backend.ensure(key, "invoice")
    report = await call(build(catalog, backend), "index_status", {})
    assert report["index_backend"] == "qdrant"
    assert report["index_ready_messages"] == 1
    assert report["coverage_complete"] is False


def test_migrated_active_mail_is_not_refetched(tmp_path):
    from proton_rag.sync import synchronize, synchronize_folder
    from proton_rag.mailbox import Snapshot, Inventory
    from unittest.mock import Mock

    catalog, state, backend, service = setup_index(tmp_path)
    raw = b"Subject: Invoice\n\nCobalt invoice AX9385"
    synchronize(catalog, backend, Snapshot("1", {"7": raw}))
    before = dict(service.points)
    mailbox = Mock()
    mailbox.inventory.return_value = Inventory("INBOX", "1", ("7",))
    synchronize_folder(catalog, backend, mailbox, "INBOX")
    mailbox.fetch.assert_not_called()
    assert service.points == before


def test_verification_detects_corrupt_dense_vector(tmp_path):
    catalog, state, backend, service = setup_index(tmp_path)
    key = "proton-mail-" + "a" * 64
    backend.ensure(key, "synthetic cobalt")
    point = next(iter(service.points.values()))
    point["vector"]["dense"] = [0.0, 1.0] + [0.0] * 766
    with pytest.raises(IndexFailure):
        backend.verify_message(key)


def test_recovery_rejects_different_cached_text(tmp_path):
    catalog, state, backend, service = setup_index(tmp_path)
    key = "proton-mail-" + "a" * 64
    backend.ensure(key, "synthetic cobalt")
    with pytest.raises(ValueError, match="content"):
        backend.recover(key, "changed text")
