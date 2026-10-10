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


def test_maintenance_lock_blocks_migration(tmp_path):
    import fcntl

    source, target, backend, key, text = fixture(tmp_path)
    path = tmp_path / "export.jsonl"
    write_export(path, [], profile=Embedder.profile)
    with (target.path.parent / "maintenance.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            import_index(source.path, path, target.path.parent, backend)
    assert backend.state.entry(key) is None


def test_verification_requires_imported_target(tmp_path, monkeypatch):
    import sys
    from proton_rag.migration import main

    source, target, backend, key, text = fixture(tmp_path)
    path = tmp_path / "export.jsonl"
    write_export(path, [], profile=Embedder.profile)
    destination = tmp_path / "absent"
    monkeypatch.setattr(Settings, "from_env", lambda: Settings(state_dir=destination))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "proton-rag-migrate",
            "verify",
            "--source-catalog",
            str(source.path),
            "--export",
            str(path),
        ],
    )
    with pytest.raises(SystemExit):
        main()
    assert not (destination / "catalog.db").exists()


@pytest.mark.parametrize("mapping", ["absent", "empty", "partial"])
def test_reuse_requires_complete_legacy_mapping(tmp_path, mapping):
    import sqlite3

    source, target, backend, key, text = fixture(tmp_path)
    path = tmp_path / "export.jsonl"
    write_export(
        path,
        [{"id": "old", "docSource": key, "text": "Invoice", "vector": [1.0] + [0.0] * 767}],
        profile=Embedder.profile,
    )
    legacy = None
    if mapping != "absent":
        legacy = tmp_path / "legacy.db"
        with sqlite3.connect(legacy) as db:
            db.execute("CREATE TABLE workspace_documents (docpath TEXT,docId TEXT)")
            db.execute("CREATE TABLE document_vectors (docId TEXT,vectorId TEXT)")
            if mapping == "partial":
                db.execute("INSERT INTO workspace_documents VALUES (?,?)", ("known", "document"))
                db.execute("INSERT INTO document_vectors VALUES (?,?)", ("document", "old"))
        if mapping == "partial":
            for catalog in (source, target):
                with catalog.connect() as db:
                    db.execute("UPDATE messages SET paths=?", (json.dumps(["known", "missing"]),))
    report = import_index(source.path, path, target.path.parent, backend, backend_database=legacy)
    assert report["rebuilt_messages"] == 1
    assert next(iter(backend.client._transport.handler.points.values()))["payload"]["text"] == text


def test_complete_rebuild_can_use_explicit_new_profile(tmp_path, monkeypatch):
    from dataclasses import replace

    source, target, old_backend, key, text = fixture(tmp_path)
    path = tmp_path / "export.jsonl"
    write_export(path, [], profile=Embedder.profile)
    destination = tmp_path / "new-profile"
    clone_catalog(source.path, destination / "catalog.db")
    catalog = Catalog(destination / "catalog.db")
    embedder = Embedder()
    embedder.profile = replace(Embedder.profile, digest="digest-b")
    state = IndexState(catalog, "proton-mail", embedder.profile, allow_existing=True)
    backend = QdrantIndex(
        Settings(state_dir=destination),
        catalog,
        state,
        embedder,
        client=httpx.Client(transport=httpx.MockTransport(Service())),
    )
    backend.bootstrap()
    report = import_index(source.path, path, destination, backend, reuse_verified=False)
    assert report["rebuilt_messages"] == 1
    assert backend.state.profile.digest == "digest-b"
    assert verify_target(source.path, catalog.path, backend)["ready"]
    import sys
    from proton_rag.migration import main

    monkeypatch.setattr(Settings, "from_env", lambda: Settings(state_dir=destination))
    monkeypatch.setattr("proton_rag.migration.QdrantIndex", lambda *a, **kw: backend)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "proton-rag-migrate",
            "verify",
            "--source-catalog",
            str(source.path),
            "--export",
            str(path),
        ],
    )
    main()


def test_disk_floor_blocks_and_resumes_migration(tmp_path, monkeypatch):
    from unittest.mock import Mock
    from proton_rag.health import DiskLowError

    source, target, backend, key, text = fixture(tmp_path)
    path = tmp_path / "export.jsonl"
    write_export(path, [], profile=Embedder.profile)
    monkeypatch.setattr("proton_rag.health.shutil.disk_usage", lambda p: Mock(free=1))
    with pytest.raises(DiskLowError):
        import_index(source.path, path, target.path.parent, backend)
    assert backend.state.entry(key) is None
    monkeypatch.setattr("proton_rag.health.shutil.disk_usage", lambda p: Mock(free=20 * 1024**3))
    assert import_index(source.path, path, target.path.parent, backend)["ready_messages"] == 1


def test_disk_drop_mid_import_replays_partial_work(tmp_path, monkeypatch):
    from unittest.mock import Mock
    from proton_rag.health import DiskLowError

    source, target, backend, key, text = fixture(tmp_path)
    text = "Synthetic cobalt. " * 5000
    for catalog in (source, target):
        catalog.store_text(key, text, [], False)
    path = tmp_path / "export.jsonl"
    write_export(path, [], profile=Embedder.profile)
    checks = 0

    def disk(path):
        nonlocal checks
        checks += 1
        return Mock(free=1 if checks >= 4 else 20 * 1024**3)

    monkeypatch.setattr("proton_rag.health.shutil.disk_usage", disk)
    with pytest.raises(DiskLowError):
        import_index(source.path, path, target.path.parent, backend)
    assert not backend.state.ready(key) and backend.count(key) == 32
    monkeypatch.setattr("proton_rag.health.shutil.disk_usage", lambda p: Mock(free=20 * 1024**3))
    assert import_index(source.path, path, target.path.parent, backend)["ready_messages"] == 1
    assert backend.state.replay(key) is None
