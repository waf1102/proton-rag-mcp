import fcntl
import pytest
from proton_rag.backup import backup, validate_backup, paused_writer
from proton_rag.config import Settings
from test_qdrant import setup_index


def snapshot_backend(backend):
    original = backend.request

    def request(method, path, data=None, **kwargs):
        if path == "/snapshots" and method == "POST":
            return {"name": "fixture.snapshot"}
        return original(method, path, data, **kwargs)

    backend.request = request


def test_backup_rejects_running_writer_before_snapshot(tmp_path):
    catalog, state, backend, service = setup_index(tmp_path)
    with (tmp_path / "daemon.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            backup(
                Settings(state_dir=tmp_path),
                tmp_path.parent / (tmp_path.name + "-backups"),
                backend,
            )


def test_backup_pair_is_complete_only_after_snapshot_download(tmp_path, monkeypatch):
    catalog, state, backend, service = setup_index(tmp_path)
    snapshot_backend(backend)
    monkeypatch.setattr(
        "proton_rag.backup.download", lambda *a: a[-1].write_bytes(b"synthetic snapshot")
    )
    destination = tmp_path.parent / (tmp_path.name + "-backups")
    result = backup(Settings(state_dir=tmp_path), destination, backend)
    manifest = validate_backup(result)
    assert manifest["binding"] == state.binding
    assert (result / "catalog.db").exists()
    (result / "qdrant.snapshot").write_bytes(b"corruption")
    with pytest.raises(ValueError, match="checksum"):
        validate_backup(result)


def test_failed_backup_has_no_complete_marker(tmp_path, monkeypatch):
    catalog, state, backend, service = setup_index(tmp_path)
    snapshot_backend(backend)

    def fail(*a):
        raise RuntimeError("download failed")

    monkeypatch.setattr("proton_rag.backup.download", fail)
    destination = tmp_path.parent / (tmp_path.name + "-backups")
    with pytest.raises(RuntimeError):
        backup(Settings(state_dir=tmp_path), destination, backend)
    assert not list(destination.glob("*/.complete"))


def test_service_resumes_after_failed_backup_and_inactive_stays_inactive():
    import subprocess

    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "active\n", "")

    with pytest.raises(RuntimeError):
        with paused_writer("fixture.service", run=run):
            raise RuntimeError("failed")
    assert calls[-1] == ["systemctl", "--user", "start", "fixture.service"]
    calls.clear()

    def inactive(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 3, "inactive\n", "")

    with paused_writer("fixture.service", run=inactive):
        pass
    assert len(calls) == 1


def test_low_disk_refuses_backup_before_copy(tmp_path, monkeypatch):
    from unittest.mock import Mock

    catalog, state, backend, service = setup_index(tmp_path)
    destination = tmp_path.parent / (tmp_path.name + "-backups")
    monkeypatch.setattr("proton_rag.backup.shutil.disk_usage", lambda p: Mock(free=1))
    with pytest.raises(RuntimeError, match="space"):
        backup(Settings(state_dir=tmp_path), destination, backend)
    assert not destination.exists()


def test_low_disk_restore_refuses_target_creation(tmp_path, monkeypatch):
    from unittest.mock import Mock
    from proton_rag.backup import restore_check
    from proton_rag.health import DiskLowError

    catalog, state, backend, service = setup_index(tmp_path)
    snapshot_backend(backend)
    monkeypatch.setattr("proton_rag.backup.download", lambda *a: a[-1].write_bytes(b"fixture"))
    pair = backup(
        Settings(state_dir=tmp_path), tmp_path.parent / (tmp_path.name + "-backups"), backend
    )
    target = tmp_path.parent / (tmp_path.name + "-restore")
    monkeypatch.setattr("proton_rag.health.shutil.disk_usage", lambda p: Mock(free=1))
    with pytest.raises(DiskLowError):
        restore_check(Settings(state_dir=tmp_path), pair, target, "new-collection")
    assert not target.exists()
