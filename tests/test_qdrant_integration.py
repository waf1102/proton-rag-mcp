import os
import uuid
import pytest
from proton_rag.catalog import Catalog
from proton_rag.config import Settings
from proton_rag.index_state import IndexState
from proton_rag.qdrant import QdrantIndex
from test_qdrant import Embedder


async def test_real_hybrid_retry_and_manifest_persistence(tmp_path):
    url = os.environ.get("QDRANT_TEST_URL")
    if not url:
        pytest.skip("Set QDRANT_TEST_URL to an isolated Qdrant server")
    settings = Settings(state_dir=tmp_path, qdrant_url=url, workspace="fixture-" + uuid.uuid4().hex)
    catalog = Catalog(tmp_path / "catalog.db")
    state = IndexState(catalog, settings.workspace, Embedder.profile)
    backend = QdrantIndex(settings, catalog, state, Embedder())
    backend.bootstrap()
    first, second = ["proton-mail-" + c * 64 for c in "ab"]
    try:
        for key, text in [
            (first, "Invoice AX9385 cobalt Tuesday"),
            (second, "Saffron garden Saturday"),
        ]:
            catalog.intent(key, "fixtures", "1", str(len(catalog.rows()) + 1), key, True, {})
            catalog.store_text(key, text, [], True)
            backend.ensure(key, text)
            backend.ensure(key, text)
            catalog.activate(key, [key])
        hits = await backend.search("AX9385", 10)
        assert hits[0]["metadata"]["docSource"] == first
        assert backend.count(first) == 1
        reopened = IndexState(catalog, settings.workspace, Embedder.profile)
        assert reopened.ready(first)
        backend.remove([first])
        assert backend.count(first) == 0
        assert backend.count(second) == 1
    finally:
        backend.request("DELETE", "", operation="fixture_cleanup")


def test_real_paired_backup_and_isolated_restore(tmp_path):
    from proton_rag.backup import backup, restore_check

    url = os.environ.get("QDRANT_TEST_URL")
    if not url:
        pytest.skip("Set QDRANT_TEST_URL to an isolated Qdrant server")
    source = tmp_path / "source"
    source.mkdir()
    settings = Settings(state_dir=source, qdrant_url=url, workspace="fixture-" + uuid.uuid4().hex)
    catalog = Catalog(source / "catalog.db")
    state = IndexState(catalog, settings.workspace, Embedder.profile)
    backend = QdrantIndex(settings, catalog, state, Embedder())
    backend.bootstrap()
    key = "proton-mail-" + "c" * 64
    target_collection = "restore-" + uuid.uuid4().hex
    try:
        catalog.intent(key, "fixtures", "1", "1", key, True, {})
        catalog.store_text(key, "Synthetic invoice AX9385", [], True)
        backend.ensure(key, "Synthetic invoice AX9385")
        catalog.activate(key, [key])
        path = backup(settings, tmp_path / "backups", backend)
        report = restore_check(settings, path, tmp_path / "restored", target_collection)
        assert report["ready"] and report["messages"] == 1 and report["chunks"] == 1
        assert backend.count(key) == 1
    finally:
        backend.client.delete(settings.qdrant_url + "/collections/" + target_collection)
        backend.request("DELETE", "", operation="fixture_cleanup")
