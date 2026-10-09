import pytest
from proton_rag.sync import Catalog, synchronize
from proton_rag.mailbox import Snapshot, SnapshotError


class Backend:
    def __init__(self):
        self.docs = {}
        self.calls = 0

    def ensure(self, key, text, before_upload=None):
        if before_upload:
            before_upload()
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

        def ensure(self, key, text, before_upload=None):
            paths = super().ensure(key, text, before_upload=before_upload)
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

        def refresh(self):
            pass

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


def test_same_uid_in_different_folders_survives_reconciliation(tmp_path):
    catalog = Catalog(tmp_path / "state.db")
    backend = Backend()
    raw = b"Subject: hi\nMessage-ID: <same@example>\n\nContent"
    synchronize(catalog, backend, Snapshot("1", {"7": raw}, folder="INBOX"))
    synchronize(catalog, backend, Snapshot("1", {"7": raw}, folder="Folders/Archive"))
    assert len(catalog.rows()) == 2 and len(backend.docs) == 1
    synchronize(catalog, backend, Snapshot("1", {}, folder="INBOX"))
    assert len(catalog.rows()) == 1
    assert next(iter(catalog.rows().values()))["folder"] == "Folders/Archive"


def test_incremental_sync_fetches_new_uids_only(tmp_path):
    from proton_rag.sync import synchronize_folder
    from proton_rag.mailbox import Inventory
    from unittest.mock import Mock

    catalog = Catalog(tmp_path / "state.db")
    backend = Backend()
    box = Mock()
    box.inventory.return_value = Inventory("INBOX", "1", ("7", "8"))
    box.fetch.side_effect = [b"Subject: first\n\nA", b"Subject: second\n\nB"]
    synchronize_folder(catalog, backend, box, "INBOX")
    synchronize_folder(catalog, backend, box, "INBOX")
    assert box.fetch.call_count == 2 and backend.calls == 2
    assert all(r["metadata"] for r in catalog.rows().values())
    box.inventory.return_value = Inventory("INBOX", "1", ("8",))
    box.verify.side_effect = SnapshotError("changed")
    with pytest.raises(SnapshotError):
        synchronize_folder(catalog, backend, box, "INBOX")
    assert len(backend.docs) == 2
    box.verify.side_effect = None
    synchronize_folder(catalog, backend, box, "INBOX")
    from proton_rag.sync import collect_orphans

    collect_orphans(catalog, backend)
    assert len(backend.docs) == 1


def test_preupload_lookup_failure_can_retry(tmp_path):
    from proton_rag.anything import Anything
    from unittest.mock import Mock

    backend = Anything("http://127.0.0.1:3001", "fixture")
    backend.request = Mock(side_effect=ConnectionError("unavailable"))
    catalog = Catalog(tmp_path / "state.db")
    snap = Snapshot("1", {"7": b"\nContent"})
    with pytest.raises(ConnectionError):
        synchronize(catalog, backend, snap)
    backend.request.side_effect = [
        {"localFiles": {"type": "folder", "name": "documents", "items": []}},
        {"documents": [{"location": "custom-documents/mail.json"}]},
        {"success": True},
    ]
    synchronize(catalog, backend, snap)
    assert next(iter(catalog.rows().values()))["active"] == 1


def test_separate_catalogs_own_distinct_remote_documents(tmp_path):
    backend = Backend()
    raw = b"Message-ID: <same>\n\nContent"
    snapshot = Snapshot("1", {"7": raw})
    first = Catalog(tmp_path / "first.db")
    second = Catalog(tmp_path / "second.db")
    synchronize(first, backend, snapshot)
    synchronize(second, backend, snapshot)
    assert len(backend.docs) == 2
    synchronize(Catalog(tmp_path / "first.db"), backend, Snapshot("1", {}))
    assert len(backend.docs) == 1
    assert next(iter(second.rows())) in backend.docs


def test_catalog_refuses_workspace_switch(tmp_path):
    catalog = Catalog(tmp_path / "state.db")
    catalog.bind_workspace("first")
    with pytest.raises(ValueError, match="workspace"):
        Catalog(tmp_path / "state.db").bind_workspace("second")


def test_bad_message_does_not_starve_remaining_folder(tmp_path, monkeypatch):
    from proton_rag.sync import synchronize_folder
    from proton_rag.mailbox import Inventory
    from unittest.mock import Mock
    import proton_rag.sync as sync

    catalog = Catalog(tmp_path / "state.db")
    backend = Backend()
    box = Mock()
    box.inventory.return_value = Inventory("INBOX", "1", ("1", "2"))
    box.fetch.side_effect = lambda uid: uid.encode()
    monkeypatch.setattr(
        sync,
        "extract",
        lambda raw, **kwargs: (
            {"text": "", "skipped": ["parse_failed"]}
            if raw == b"2"
            else {"text": "good", "skipped": [], "metadata": {}}
        ),
    )
    result = synchronize_folder(catalog, backend, box, "INBOX")
    assert result["failed_messages"] == 1
    assert len(backend.docs) == 1


def test_shutdown_finishes_current_message_and_resumes(tmp_path):
    import threading
    from unittest.mock import Mock
    from proton_rag.sync import synchronize_folder
    from proton_rag.mailbox import Inventory

    stop = threading.Event()
    catalog = Catalog(tmp_path / "state.db")
    backend = Backend()
    box = Mock()
    box.inventory.return_value = Inventory("INBOX", "1", ("1", "2", "3"))

    def fetch(uid):
        stop.set()
        return b"Subject: stop test\n\nContent"

    box.fetch.side_effect = fetch
    with pytest.raises(SnapshotError, match="interrupted"):
        synchronize_folder(catalog, backend, box, "INBOX", stop=stop)
    assert len(backend.docs) == 1
    box.verify.assert_not_called()
    stop.clear()
    box.fetch.side_effect = None
    box.fetch.return_value = b"Subject: stop test\n\nContent"
    result = synchronize_folder(Catalog(tmp_path / "state.db"), backend, box, "INBOX", stop=stop)
    assert result["fetched"] == 2
    assert len(backend.docs) == 1 and backend.calls == 1


def test_existing_index_backfills_full_text_without_reembedding(tmp_path):
    from unittest.mock import Mock
    from proton_rag.sync import synchronize_folder
    from proton_rag.mailbox import Inventory

    catalog = Catalog(tmp_path / "state.db")
    backend = Backend()
    raw = b"Subject: Itinerary\n\nFlight details\nConfirmation: ABC123"
    synchronize(catalog, backend, Snapshot("1", {"7": raw}))
    key = next(iter(catalog.rows()))
    with catalog.connect() as db:
        db.execute("delete from message_text")
    box = Mock()
    box.inventory.return_value = Inventory("INBOX", "1", ("7",))
    box.fetch.return_value = raw
    synchronize_folder(catalog, backend, box, "INBOX")
    assert catalog.get_text(key)["text"].endswith("Confirmation: ABC123")
    assert backend.calls == 1
