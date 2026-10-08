import pytest
from proton_rag.sync import Catalog, synchronize
from proton_rag.mailbox import Snapshot, SnapshotError


class Backend:
    def __init__(self):
        self.docs = {}
        self.calls = 0

    def ensure(self, key, text):
        self.calls += 1
        self.docs[key] = text
        return [key]

    def remove(self, paths):
        for path in paths:
            self.docs.pop(path, None)


def test_retry_restart_and_delete_are_idempotent(tmp_path):
    db = tmp_path / "state.db"
    backend = Backend()
    snapshot = Snapshot("1", {"7": b"Subject: test\n\nThe cobalt shipment arrives Tuesday."})
    synchronize(Catalog(db), backend, snapshot)
    synchronize(Catalog(db), backend, snapshot)
    assert len(backend.docs) == 1 and backend.calls == 1
    with pytest.raises(SnapshotError):
        synchronize(Catalog(db), backend, Snapshot("1", {}, complete=False))
    assert len(backend.docs) == 1
    synchronize(Catalog(db), backend, Snapshot("1", {}))
    assert not backend.docs


def test_upload_crash_recovers_and_uidvalidity_replaces_identity(tmp_path):
    class CrashBackend(Backend):
        fail = True

        def ensure(self, key, text):
            paths = super().ensure(key, text)
            if self.fail:
                self.fail = False
                raise ConnectionError("response lost")
            return paths

    backend = CrashBackend()
    catalog = Catalog(tmp_path / "state.db")
    snap = Snapshot("1", {"7": b"\nFixture cobalt"})
    with pytest.raises(ConnectionError):
        synchronize(catalog, backend, snap)
    assert not next(iter(catalog.rows().values()))["active"]
    synchronize(catalog, backend, snap)
    assert len(backend.docs) == 1
    synchronize(catalog, backend, Snapshot("2", snap.messages))
    assert len(backend.docs) == 1
    assert next(iter(catalog.rows().values()))["validity"] == "2"


def test_parser_resource_failure_never_purges_previous_data(tmp_path, monkeypatch):
    import proton_rag.sync as sync

    catalog = Catalog(tmp_path / "state.db")
    backend = Backend()
    synchronize(catalog, backend, Snapshot("1", {"7": b"\nKeep cobalt"}))
    monkeypatch.setattr(
        sync, "extract", lambda raw: {"text": "", "skipped": ["parse_timeout_or_resource_limit"]}
    )
    with pytest.raises(SnapshotError):
        synchronize(catalog, backend, Snapshot("1", {"8": b"\nNew message"}))
    assert len(backend.docs) == 1


def test_private_and_synthetic_datasets_cannot_mix(tmp_path):
    catalog = Catalog(tmp_path / "state.db")
    backend = Backend()
    snapshot = Snapshot("1", {"7": b"\nFixture"})
    synchronize(catalog, backend, snapshot, synthetic=True)
    with pytest.raises(SnapshotError):
        synchronize(catalog, backend, snapshot, synthetic=False)


def test_parser_timeout_can_retry_without_pending_upload(tmp_path, monkeypatch):
    import proton_rag.sync as sync
    from proton_rag.anything import Anything

    class RecoveringBackend(Backend):
        recover = Anything.recover

        def find(self, key):
            return [key] if key in self.docs else []

    path = tmp_path / "state.db"
    backend = RecoveringBackend()
    synchronize(Catalog(path), backend, Snapshot("1", {"7": b"\nOld message"}))
    snapshot = Snapshot("1", {"8": b"\nNew message"})
    original_extract = sync.extract
    monkeypatch.setattr(
        sync, "extract", lambda raw: {"text": "", "skipped": ["parse_timeout_or_resource_limit"]}
    )
    with pytest.raises(SnapshotError):
        synchronize(Catalog(path), backend, snapshot)
    assert backend.calls == 1 and len(backend.docs) == 1
    monkeypatch.setattr(sync, "extract", original_extract)
    synchronize(Catalog(path), backend, snapshot)
    synchronize(Catalog(path), backend, snapshot)
    assert backend.calls == 2 and len(backend.docs) == 1
    assert next(iter(Catalog(path).rows().values()))["uid"] == "8"
