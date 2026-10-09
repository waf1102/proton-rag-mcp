"""Content deduplication must retain distinct mail, locations and old citations."""

import hashlib
import json
import sqlite3
from unittest.mock import Mock
import pytest
from proton_rag.sync import Catalog, synchronize, synchronize_folder
from proton_rag.mailbox import Snapshot, Inventory
from proton_rag.mcp_server import build, search
from proton_rag.config import Settings
from test_sync import Backend
from test_mail_reading import call

RAW = b"Subject: receipt\nMessage-ID: <one@example>\n\nOrder 123 costs $42."


def test_three_locations_share_one_parsed_body_and_embedding(tmp_path, monkeypatch):
    import proton_rag.sync as module

    catalog = Catalog(tmp_path / "catalog.db")
    backend = Backend()
    synchronize(catalog, backend, Snapshot("1", {"7": RAW}, folder="INBOX"))
    monkeypatch.setattr(module, "extract", lambda *a, **k: pytest.fail("duplicate reparsed"))
    synchronize(catalog, backend, Snapshot("2", {"3": RAW}, folder="Labels/Work"))
    synchronize(catalog, backend, Snapshot("9", {"8": RAW}, folder="All Mail"))
    assert len(catalog.rows()) == 3
    assert len(backend.docs) == backend.calls == 1
    with catalog.connect() as db:
        assert db.execute("select count(*) from message_text").fetchone()[0] == 1
    catalog.set_scope(["INBOX", "Labels/Work", "All Mail"])
    for folder, validity, uid in [
        ("INBOX", "1", "7"),
        ("Labels/Work", "2", "3"),
        ("All Mail", "9", "8"),
    ]:
        catalog.observe(Inventory(folder, validity, (uid,)))
        catalog.folder_finished(folder, True)
    status = catalog.coverage()
    assert status["unique_messages_indexed"] == 1
    assert status["folder_entries_indexed"] == 3
    assert status["shared_folder_entries"] == 2


def test_last_membership_controls_deletion(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    backend = Backend()
    synchronize(catalog, backend, Snapshot("1", {"7": RAW}, folder="INBOX"))
    synchronize(catalog, backend, Snapshot("2", {"3": RAW}, folder="Labels/Work"))
    synchronize(catalog, backend, Snapshot("1", {}, folder="INBOX"))
    assert len(backend.docs) == 1 and len(catalog.rows()) == 1
    synchronize(catalog, backend, Snapshot("2", {}, folder="Labels/Work"))
    assert backend.docs == {} and catalog.rows() == {}
    with catalog.connect() as db:
        assert db.execute("select count(*) from message_text").fetchone()[0] == 0


@pytest.mark.parametrize("raw", [RAW, b"Subject: no ID\n\nA message without Message-ID"])
def test_same_content_inside_one_folder_is_shared(tmp_path, raw):
    catalog = Catalog(tmp_path / "catalog.db")
    backend = Backend()
    synchronize(catalog, backend, Snapshot("1", {"1": raw, "2": raw}))
    assert len(backend.docs) == backend.calls == 1
    assert len(catalog.rows()) == 2
    synchronize(catalog, backend, Snapshot("1", {"2": raw}))
    assert len(backend.docs) == 1


def test_reused_message_id_with_different_content_remains_distinct(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    backend = Backend()
    synchronize(catalog, backend, Snapshot("1", {"1": RAW, "2": RAW.replace(b"$42", b"$43")}))
    assert len(backend.docs) == 2
    assert catalog.coverage()["unique_messages_indexed"] == 2


async def test_canonical_id_survives_original_location_removal(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    backend = Backend()
    synchronize(catalog, backend, Snapshot("1", {"7": RAW}, folder="INBOX"))
    canonical = next(iter(backend.docs))
    synchronize(catalog, backend, Snapshot("2", {"3": RAW}, folder="Labels/Work"))
    synchronize(catalog, backend, Snapshot("1", {}, folder="INBOX"))
    peer = Mock()
    peer.search = __import__("unittest.mock", fromlist=["AsyncMock"]).AsyncMock(
        return_value=[{"text": "Order 123", "metadata": {"docSource": canonical}}]
    )
    hits = await search(catalog, peer, "Order", 10, Settings())
    assert len(hits) == 1 and hits[0]["message_id"] == canonical
    assert hits[0]["locations"][0]["folder"] == "Labels/Work"
    body = await call(build(catalog, object()), "read_mail", {"message_id": canonical})
    assert "$42" in body["text"]


def test_move_discovered_in_next_folder_reuses_embedding(tmp_path):
    from proton_rag.sync import collect_orphans

    catalog = Catalog(tmp_path / "catalog.db")
    backend = Backend()
    synchronize(catalog, backend, Snapshot("1", {"7": RAW}))
    canonical = next(iter(backend.docs))
    box = Mock()
    box.inventory.return_value = Inventory("INBOX", "1", ())
    synchronize_folder(catalog, backend, box, "INBOX")
    box.inventory.return_value = Inventory("Folders/Archive", "2", ("4",))
    box.fetch.return_value = RAW
    synchronize_folder(catalog, backend, box, "Folders/Archive")
    collect_orphans(catalog, backend)
    assert backend.calls == 1 and list(backend.docs) == [canonical]


def legacy_catalog(path):
    digest = hashlib.sha256(RAW).hexdigest()
    keys = ["proton-mail-" + "a" * 64, "proton-mail-" + "b" * 64]
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE documents (key TEXT PRIMARY KEY, folder TEXT, validity TEXT, "
            "uid TEXT, digest TEXT, paths TEXT, active INTEGER, synthetic INTEGER, metadata TEXT, phase TEXT)"
        )
        db.execute(
            "CREATE TABLE message_text (key TEXT PRIMARY KEY, text TEXT, skipped TEXT, truncated INTEGER)"
        )
        for key, folder, uid in zip(keys, ["INBOX", "Labels/Work"], ["7", "3"]):
            db.execute(
                "INSERT INTO documents VALUES (?,?,?,?,?,?,1,0,?,?)",
                (
                    key,
                    folder,
                    "1",
                    uid,
                    digest,
                    json.dumps([key]),
                    json.dumps({"message_id": "<one@example>"}),
                    "active",
                ),
            )
            db.execute(
                "INSERT INTO message_text VALUES (?,?,?,0)", (key, "Order 123 costs $42.", "[]")
            )
    return keys, digest


async def test_migration_keeps_old_ids_and_citations_without_reembedding(tmp_path):
    from proton_rag.sync import cleanup_duplicates

    path = tmp_path / "catalog.db"
    keys, digest = legacy_catalog(path)
    catalog = Catalog(path)
    backend = Backend()
    backend.docs = {key: "Order 123 costs $42." for key in keys}
    assert catalog.coverage()["unique_messages_indexed"] == 1
    cleanup_duplicates(catalog, backend)
    assert len(backend.docs) == 1 and backend.calls == 0
    server = build(Catalog(path), object())
    for identity in [*keys, f"imap:///INBOX/1/7#{digest}", f"imap:///Labels%2FWork/1/3#{digest}"]:
        result = await call(server, "read_mail", {"message_id": identity})
        assert result["text"] == "Order 123 costs $42."
    with catalog.connect() as db:
        assert db.execute("select count(*) from message_text").fetchone()[0] == 1
    assert len(catalog.rows()) == 2


def test_duplicate_cleanup_can_resume_after_lost_delete_response(tmp_path):
    from proton_rag.sync import cleanup_duplicates

    keys, _ = legacy_catalog(tmp_path / "catalog.db")
    catalog = Catalog(tmp_path / "catalog.db")

    class LostResponse(Backend):
        fail = True

        def find(self, key):
            return [key] if key in self.docs else []

        def remove(self, paths):
            super().remove(paths)
            if self.fail:
                self.fail = False
                raise ConnectionError("response lost")

    backend = LostResponse()
    backend.docs = {key: "body" for key in keys}
    with pytest.raises(ConnectionError):
        cleanup_duplicates(catalog, backend)
    cleanup_duplicates(Catalog(catalog.path), backend)
    assert len(backend.docs) == 1
    assert catalog.coverage()["pending_duplicate_cleanup"] == 0
    assert len(catalog.rows()) == 2


def test_migration_keeps_private_legacy_catalog_backup(tmp_path):
    path = tmp_path / "catalog.db"
    legacy_catalog(path)
    Catalog(path)
    backup = path.with_name("catalog.pre-dedup.db")
    assert backup.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(backup) as db:
        assert db.execute("select count(*) from documents").fetchone()[0] == 2
        columns = {r[1] for r in db.execute("pragma table_info(documents)")}
        assert "metadata" in columns and "message_key" not in columns


def test_migration_is_atomic_and_refuses_an_active_older_writer(tmp_path, monkeypatch):
    import fcntl
    import proton_rag.catalog_migration as module

    path = tmp_path / "catalog.db"
    legacy_catalog(path)
    with path.with_name("daemon.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="older indexer"):
            Catalog(path)
    original = module.create_locations
    monkeypatch.setattr(
        module, "create_locations", lambda db: (_ for _ in ()).throw(RuntimeError("interrupted"))
    )
    with pytest.raises(RuntimeError, match="interrupted"):
        Catalog(path)
    with sqlite3.connect(path) as db:
        assert db.execute("select count(*) from documents").fetchone()[0] == 2
        assert db.execute("select count(*) from message_text").fetchone()[0] == 2
    monkeypatch.setattr(module, "create_locations", original)
    assert len(Catalog(path).rows()) == 2


def test_new_copy_backfills_missing_text_without_embedding(tmp_path):
    catalog = Catalog(tmp_path / "catalog.db")
    backend = Backend()
    synchronize(catalog, backend, Snapshot("1", {"7": RAW}))
    with catalog.connect() as db:
        db.execute("delete from message_text")
    synchronize(catalog, backend, Snapshot("2", {"3": RAW}, folder="Labels/Work"))
    assert backend.calls == 1
    key = next(iter(backend.docs))
    assert "$42" in catalog.get_text(key)["text"]


def test_pending_upload_in_another_folder_is_not_blindly_replayed(tmp_path):
    from proton_rag.anything import Anything, PendingUploadError

    catalog = Catalog(tmp_path / "catalog.db")
    key = "proton-mail-" + "d" * 64
    digest = hashlib.sha256(RAW).hexdigest()
    catalog.intent(key, "INBOX", "1", "7", digest, False, {})
    catalog.store_text(key, "Order 123 costs $42.", [], False)
    catalog.dispatched(key)

    class RecoveringBackend(Backend):
        recover = Anything.recover

        def refresh(self):
            pass

        def find(self, key):
            return [key] if key in self.docs else []

    backend = RecoveringBackend()
    with pytest.raises(PendingUploadError):
        synchronize(catalog, backend, Snapshot("2", {"3": RAW}, folder="Labels/Work"))
    assert backend.docs == {} and backend.calls == 0
    assert len(catalog.rows()) == 2
    backend.docs[key] = "Order 123 costs $42."
    synchronize(catalog, backend, Snapshot("2", {"3": RAW}, folder="Labels/Work"))
    assert list(backend.docs) == [key] and backend.calls == 1


def test_last_reference_delete_recovers_after_lost_response(tmp_path):
    from proton_rag.sync import collect_orphans

    catalog = Catalog(tmp_path / "catalog.db")

    class LostDelete(Backend):
        fail = True

        def find(self, key):
            return [key] if key in self.docs else []

        def remove(self, paths):
            super().remove(paths)
            if self.fail:
                self.fail = False
                raise ConnectionError("response lost")

    backend = LostDelete()
    synchronize(catalog, backend, Snapshot("1", {"7": RAW}))
    with pytest.raises(ConnectionError):
        synchronize(catalog, backend, Snapshot("1", {}))
    assert catalog.orphan_messages()[0]["phase"] == "deleting"
    collect_orphans(Catalog(catalog.path), backend, interrupted_only=True)
    assert catalog.orphan_messages() == [] and backend.docs == {}


def test_gc_preserves_uncertain_upload_after_last_location_disappears(tmp_path):
    from proton_rag.sync import collect_orphans

    catalog = Catalog(tmp_path / "catalog.db")
    key = "proton-mail-" + "d" * 64
    catalog.intent(key, "INBOX", "1", "7", hashlib.sha256(RAW).hexdigest(), False, {})
    catalog.store_text(key, "body", [], False)
    catalog.dispatched(key)
    catalog.forget(key, retain_message=True)
    backend = Backend()
    backend.find = lambda identity: []
    collect_orphans(catalog, backend)
    assert catalog.orphan_messages()[0]["phase"] == "uploading"
    assert catalog.get_text(key)["text"] == "body"


def test_gc_grace_cycle_preserves_move_into_already_processed_folder(tmp_path):
    from proton_rag.sync import collect_orphans

    catalog = Catalog(tmp_path / "catalog.db")
    backend = Backend()
    synchronize(catalog, backend, Snapshot("1", {"7": RAW}))
    canonical = next(iter(backend.docs))
    box = Mock()
    box.inventory.return_value = Inventory("Archive", "2", ())
    synchronize_folder(catalog, backend, box, "Archive")
    # Mail moved into Archive after its inventory; INBOX is now empty.
    box.inventory.return_value = Inventory("INBOX", "1", ())
    synchronize_folder(catalog, backend, box, "INBOX")
    collect_orphans(catalog, backend, defer_new=True)
    assert list(backend.docs) == [canonical]
    box.inventory.return_value = Inventory("Archive", "2", ("4",))
    box.fetch.return_value = RAW
    synchronize_folder(catalog, backend, box, "Archive")
    collect_orphans(catalog, backend, defer_new=True)
    assert list(backend.docs) == [canonical] and backend.calls == 1


def test_gc_removes_real_orphan_after_second_stable_cycle(tmp_path):
    from proton_rag.sync import collect_orphans

    catalog = Catalog(tmp_path / "catalog.db")
    backend = Backend()
    synchronize(catalog, backend, Snapshot("1", {"7": RAW}))
    catalog.forget(next(iter(catalog.rows())), retain_message=True)
    collect_orphans(catalog, backend, defer_new=True)
    assert len(backend.docs) == 1
    collect_orphans(Catalog(catalog.path), backend, defer_new=True)
    assert backend.docs == {} and catalog.orphan_messages() == []


def test_inactive_legacy_rows_reconcile_deleted_embedding(tmp_path):
    path = tmp_path / "catalog.db"
    keys, digest = legacy_catalog(path)
    with sqlite3.connect(path) as db:
        db.execute("update documents set active=0")
    catalog = Catalog(path)
    backend = Backend()
    backend.find = lambda key: [key] if key in backend.docs else []
    synchronize(catalog, backend, Snapshot("2", {"9": RAW}, folder="Archive"))
    assert len(backend.docs) == 1 and backend.calls == 1


@pytest.mark.parametrize("retired_paths", [False, True])
def test_pending_migration_preserves_dispatched_identity(tmp_path, retired_paths):
    path = tmp_path / "catalog.db"
    keys, digest = legacy_catalog(path)
    with sqlite3.connect(path) as db:
        db.execute("update documents set active=0,paths='[]',phase='prepared'")
        db.execute("update documents set phase='uploading' where key=?", (keys[1],))
        if retired_paths:
            db.execute(
                "update documents set phase='active', paths=? where key=?",
                (json.dumps([keys[0]]), keys[0]),
            )
    catalog = Catalog(path)
    content = catalog.content(digest)
    assert content["key"] == keys[1] and content["phase"] == "uploading"
