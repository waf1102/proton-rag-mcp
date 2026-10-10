import json
import pytest
from proton_rag.catalog import Catalog
from proton_rag.mailbox import Snapshot
from proton_rag.sync import synchronize
from proton_rag.migration import (
    clone_catalog,
    import_index,
    verify_target,
    bind_target,
    write_export,
    read_export,
)
from proton_rag.index_state import IndexState
from proton_rag.qdrant import QdrantIndex
from proton_rag.config import Settings
from test_sync import Backend
from test_qdrant import Embedder, Service
import httpx
import tracemalloc


def test_export_reader_uses_bounded_memory(tmp_path):
    path = tmp_path / "large.jsonl"
    write_export(
        path,
        (
            {"id": str(i), "docSource": "source", "text": "fixture", "vector": [0.125] * 768}
            for i in range(1000)
        ),
        profile=Embedder.profile,
    )
    tracemalloc.start()
    try:
        _, rows, _ = read_export(path)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert len(rows) == 1000
    assert rows[999]["id"] == "999"
    assert peak < 4 * 1024 * 1024


def fixture(tmp_path):
    source = Catalog(tmp_path / "source.db")
    synchronize(source, Backend(), Snapshot("1", {"7": b"Subject: Cobalt\n\nInvoice AX9385"}))
    key = next(iter(source.rows()))
    text = source.get_text(key)["text"]
    destination = tmp_path / "target"
    destination.mkdir()
    clone_catalog(source.path, destination / "catalog.db")
    target = Catalog(destination / "catalog.db")
    state = IndexState(target, "proton-mail", Embedder.profile, allow_existing=True)
    backend = QdrantIndex(
        Settings(state_dir=destination),
        target,
        state,
        Embedder(),
        client=httpx.Client(transport=httpx.MockTransport(Service())),
    )
    backend.bootstrap()
    return source, target, backend, key, text


def test_migration_preserves_catalog_identity_and_text_hashes(tmp_path):
    source, target, backend, key, text = fixture(tmp_path)
    path = tmp_path / "export.jsonl"
    write_export(
        path,
        [{"id": "legacy-chunk", "docSource": key, "text": text, "vector": [1.0] + [0.0] * 767}],
        profile=Embedder.profile,
    )
    report = import_index(source.path, path, target.path.parent, backend)
    assert report["ready_messages"] == 1
    assert target.namespace == source.namespace
    assert target.get_text(key) == source.get_text(key)
    assert source.rows()[key]["paths"] == json.dumps([key])
    assert verify_target(source.path, target.path, backend)["ready"]
    bind_target(source.path, target.path, backend)
    assert IndexState.saved(target)["published"]


def test_alias_chunks_map_to_canonical_message(tmp_path):
    source, target, backend, key, text = fixture(tmp_path)
    alias = "proton-mail-" + "c" * 64
    for catalog in (source, target):
        with catalog.connect() as db:
            db.execute("INSERT INTO message_aliases VALUES (?,?)", (alias, key))
    path = tmp_path / "export.jsonl"
    write_export(
        path,
        [{"id": "legacy-alias", "docSource": alias, "text": text, "vector": [1.0] + [0.0] * 767}],
        profile=Embedder.profile,
    )
    report = import_index(source.path, path, target.path.parent, backend)
    assert report["alias_chunks"] == 1
    assert backend.count(key) == 1
    assert verify_target(source.path, target.path, backend)["ready"]


def test_missing_chunk_group_rebuilds_from_cache(tmp_path):
    source, target, backend, key, text = fixture(tmp_path)
    path = tmp_path / "export.jsonl"
    write_export(path, [], profile=Embedder.profile)
    report = import_index(source.path, path, target.path.parent, backend)
    assert report["rebuilt_messages"] == 1
    assert backend.count(key) == 1


def test_unexplained_source_blocks_import(tmp_path):
    source, target, backend, key, text = fixture(tmp_path)
    path = tmp_path / "export.jsonl"
    write_export(
        path,
        [
            {
                "id": "unknown",
                "docSource": "proton-mail-" + "f" * 64,
                "text": text,
                "vector": [1.0] + [0.0] * 767,
            }
        ],
        profile=Embedder.profile,
    )
    with pytest.raises(ValueError, match="source"):
        import_index(source.path, path, target.path.parent, backend)
    assert not IndexState.saved(target)["published"]


def test_interrupted_import_resumes_without_duplicate_points(tmp_path, monkeypatch):
    source, target, backend, key, text = fixture(tmp_path)
    path = tmp_path / "export.jsonl"
    write_export(
        path,
        [{"id": "chunk", "docSource": key, "text": text, "vector": [1.0] + [0.0] * 767}],
        profile=Embedder.profile,
    )
    original = backend.state.finish
    monkeypatch.setattr(
        backend.state, "finish", lambda *a: (_ for _ in ()).throw(RuntimeError("interrupted"))
    )
    with pytest.raises(RuntimeError):
        import_index(source.path, path, target.path.parent, backend)
    monkeypatch.setattr(backend.state, "finish", original)
    report = import_index(source.path, path, target.path.parent, backend)
    assert report["ready_messages"] == 1
    assert backend.count(key) == 1
    report = import_index(source.path, path, target.path.parent, backend)
    assert report["skipped_messages"] == 1


def test_corrupt_or_changed_export_is_refused(tmp_path):
    source, target, backend, key, text = fixture(tmp_path)
    path = tmp_path / "export.jsonl"
    write_export(path, [], profile=Embedder.profile)
    import_index(source.path, path, target.path.parent, backend)
    write_export(
        path,
        [{"id": "new", "docSource": key, "text": text, "vector": [1.0] + [0.0] * 767}],
        profile=Embedder.profile,
    )
    with pytest.raises(ValueError, match="changed"):
        import_index(source.path, path, target.path.parent, backend)


def test_bind_replaces_paths_only_in_target(tmp_path):
    source, target, backend, key, text = fixture(tmp_path)
    with source.connect() as db:
        db.execute("UPDATE messages SET paths=?", (json.dumps(["custom-documents/old.json"]),))
    with target.connect() as db:
        db.execute("UPDATE messages SET paths=?", (json.dumps(["custom-documents/old.json"]),))
    path = tmp_path / "export.jsonl"
    write_export(path, [], profile=Embedder.profile)
    import_index(source.path, path, target.path.parent, backend)
    bind_target(source.path, target.path, backend)
    assert json.loads(source.rows()[key]["paths"]) == ["custom-documents/old.json"]
    assert json.loads(target.rows()[key]["paths"]) == [key]
