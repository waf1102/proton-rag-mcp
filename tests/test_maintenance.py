"""Maintenance must back up stopped writers and always recover service availability."""

import json
from pathlib import Path
from unittest.mock import Mock
import pytest
from proton_rag.config import Settings
from proton_rag.sync import Catalog


def test_missing_backup_permission_does_not_stop_services(tmp_path):
    import proton_rag.maintenance as module

    run = Mock()
    with pytest.raises(ValueError, match="backup"):
        module.maintain(Settings(state_dir=tmp_path), backup_dir=tmp_path, engine="podman", run=run)
    run.assert_not_called()


def test_maintenance_backs_up_and_restarts_on_optimizer_failure(tmp_path):
    import proton_rag.maintenance as module

    state = tmp_path / "state"
    state.mkdir()
    Catalog(state / "catalog.db")
    calls = []
    inspect = [
        {
            "Image": "sha256:abc",
            "Config": {"User": "1000"},
            "State": {"Running": True},
            "Mounts": [
                {
                    "Destination": "/app/server/storage",
                    "Type": "volume",
                    "Name": "proton-rag-anything",
                }
            ],
        }
    ]

    def run(args, **kwargs):
        calls.append(args)
        if "ps" in args:
            return Mock(stdout="")
        if "is-active" in args:
            return Mock(stdout="inactive")
        if "--format" in args:
            return Mock(stdout="false")
        if "inspect" in args:
            return Mock(stdout=json.dumps(inspect))
        if "--entrypoint" in args and "tar" in args:
            kwargs["stdout"].write(b"compressed-backup")
            return Mock()
        if "--entrypoint" in args and "node" in args:
            raise RuntimeError("optimizer failed")
        return Mock(stdout="")

    with pytest.raises(RuntimeError, match="optimizer failed"):
        module.maintain(
            Settings(state_dir=state),
            backup_dir=tmp_path / "backups",
            engine="podman",
            backend_service="proton-rag-anything.service",
            run=run,
            ready=lambda: None,
        )
    assert calls[1] == ["systemctl", "--user", "stop", "proton-rag-daemon.service"]
    assert calls[2] == ["systemctl", "--user", "stop", "proton-rag-anything.service"]
    assert calls[-2:] == [
        ["systemctl", "--user", "start", "proton-rag-anything.service"],
        ["systemctl", "--user", "start", "proton-rag-daemon.service"],
    ]
    backups = list((tmp_path / "backups").glob("snapshot-*"))
    assert len(backups) == 1
    assert (backups[0] / "anything.tar.gz").read_bytes() == b"compressed-backup"
    assert (backups[0] / "catalog.db").exists()
    assert Path(backups[0]).stat().st_mode & 0o777 == 0o700


def test_backup_failure_does_not_run_optimizer(tmp_path):
    import proton_rag.maintenance as module

    state = tmp_path / "state"
    state.mkdir()
    Catalog(state / "catalog.db")
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if "ps" in args:
            return Mock(stdout="")
        if "is-active" in args:
            return Mock(stdout="inactive")
        if "--format" in args:
            return Mock(stdout="false")
        if "inspect" in args:
            return Mock(
                stdout=json.dumps(
                    [
                        {
                            "Image": "sha256:abc",
                            "Config": {"User": ""},
                            "State": {"Running": True},
                            "Mounts": [
                                {
                                    "Destination": "/app/server/storage",
                                    "Type": "volume",
                                    "Name": "mail",
                                }
                            ],
                        }
                    ]
                )
            )
        if "tar" in args:
            raise OSError("backup disk full")
        return Mock(stdout="")

    with pytest.raises(OSError):
        module.maintain(
            Settings(state_dir=state),
            backup_dir=tmp_path / "backups",
            engine="docker",
            run=run,
            ready=lambda: None,
        )
    assert not any("node" in c for c in calls)
    assert calls[-1] == ["systemctl", "--user", "start", "proton-rag-daemon.service"]


def test_quadlet_removed_container_can_be_maintained(tmp_path):
    import proton_rag.maintenance as module

    state = tmp_path / "mail"
    state.mkdir()
    Catalog(state / "catalog.db")
    inspections = 0

    def run(args, **kwargs):
        nonlocal inspections
        if "inspect" in args:
            inspections += 1
            if inspections > 1:
                raise RuntimeError("Quadlet removed its container")
            return Mock(
                stdout=json.dumps(
                    [
                        {
                            "Image": "abc",
                            "Config": {"User": "1000"},
                            "State": {"Running": True},
                            "Mounts": [
                                {
                                    "Destination": "/app/server/storage",
                                    "Type": "volume",
                                    "Name": "mail",
                                }
                            ],
                        }
                    ]
                )
            )
        if "ps" in args:
            return Mock(stdout="")
        if "is-active" in args:
            return Mock(stdout="inactive")
        if "tar" in args:
            kwargs["stdout"].write(b"backup")
        if "node" in args:
            return Mock(
                stdout=json.dumps(
                    {
                        "event": "maintenance_ok",
                        "rows_before": 8,
                        "rows_after": 8,
                        "versions_before": 20,
                        "versions_after": 2,
                    }
                )
            )
        return Mock(stdout="")

    result = module.maintain(
        Settings(state_dir=state),
        backup_dir=tmp_path / "backups",
        engine="podman",
        backend_service="proton-rag-anything.service",
        run=run,
        ready=lambda: None,
    )
    assert result["rows_before"] == result["rows_after"] == 8
    assert inspections == 1


def test_readiness_failure_still_attempts_daemon_restart(tmp_path):
    import proton_rag.maintenance as module

    state = tmp_path / "mail"
    state.mkdir()
    Catalog(state / "catalog.db")
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if "inspect" in args:
            return Mock(
                stdout=json.dumps(
                    [
                        {
                            "Image": "abc",
                            "Config": {"User": ""},
                            "State": {"Running": True},
                            "Mounts": [
                                {
                                    "Destination": "/app/server/storage",
                                    "Type": "volume",
                                    "Name": "mail",
                                }
                            ],
                        }
                    ]
                )
            )
        if "is-active" in args:
            return Mock(stdout="inactive")
        if "tar" in args:
            kwargs["stdout"].write(b"backup")
        if "node" in args:
            return Mock(
                stdout=json.dumps({"event": "maintenance_ok", "rows_before": 8, "rows_after": 8})
            )
        return Mock(stdout="")

    def ready():
        raise RuntimeError("health check failed")

    with pytest.raises(RuntimeError, match="health check failed"):
        module.maintain(
            Settings(state_dir=state),
            backup_dir=tmp_path / "backups",
            engine="podman",
            backend_service="proton-rag-anything.service",
            run=run,
            ready=ready,
        )
    assert calls[-1] == ["systemctl", "--user", "start", "proton-rag-daemon.service"]


def test_interrupted_optimizer_stops_helper_before_writers_restart(tmp_path):
    import proton_rag.maintenance as module

    state = tmp_path / "mail"
    state.mkdir()
    Catalog(state / "catalog.db")
    calls = []
    helper_running = False

    def run(args, **kwargs):
        nonlocal helper_running
        calls.append(args)
        if "inspect" in args:
            return Mock(
                stdout=json.dumps(
                    [
                        {
                            "Image": "abc",
                            "Config": {"User": ""},
                            "State": {"Running": True},
                            "Mounts": [
                                {
                                    "Destination": "/app/server/storage",
                                    "Type": "volume",
                                    "Name": "mail",
                                }
                            ],
                        }
                    ]
                )
            )
        if "is-active" in args:
            return Mock(stdout="inactive")
        if "tar" in args:
            kwargs["stdout"].write(b"backup")
        if "node" in args:
            helper_running = True
            raise InterruptedError("signal")
        if "rm" in args:
            helper_running = False
        if args[:4] == ["systemctl", "--user", "start", "proton-rag-anything.service"]:
            assert not helper_running, "optimizer is still writing"
        return Mock(stdout="")

    with pytest.raises(InterruptedError):
        module.maintain(
            Settings(state_dir=state),
            backup_dir=tmp_path / "backups",
            engine="podman",
            backend_service="proton-rag-anything.service",
            run=run,
            ready=lambda: None,
        )
    helper_call = next(c for c in calls if "node" in c)
    assert "--name" in helper_call


def test_another_volume_writer_prevents_backup_and_optimize(tmp_path):
    import proton_rag.maintenance as module

    state = tmp_path / "mail"
    state.mkdir()
    Catalog(state / "catalog.db")
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if "inspect" in args:
            return Mock(
                stdout=json.dumps(
                    [
                        {
                            "Image": "abc",
                            "Config": {"User": ""},
                            "State": {"Running": True},
                            "Mounts": [
                                {
                                    "Destination": "/app/server/storage",
                                    "Type": "volume",
                                    "Name": "mail",
                                }
                            ],
                        }
                    ]
                )
            )
        if "is-active" in args:
            return Mock(stdout="inactive")
        if "ps" in args:
            return Mock(stdout="another-writer")
        return Mock(stdout="")

    with pytest.raises(RuntimeError, match="volume"):
        module.maintain(
            Settings(state_dir=state),
            backup_dir=tmp_path / "backups",
            engine="podman",
            backend_service="proton-rag-anything.service",
            run=run,
            ready=lambda: None,
        )
    assert not any("tar" in c or "node" in c for c in calls)


@pytest.mark.parametrize("service", ["proton-rag-daemon.service", "proton-rag-anything.service"])
def test_interrupted_stop_still_restores_service(tmp_path, service):
    import proton_rag.maintenance as module

    state = tmp_path / "mail"
    state.mkdir()
    Catalog(state / "catalog.db")
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if "inspect" in args:
            return Mock(
                stdout=json.dumps(
                    [
                        {
                            "Image": "abc",
                            "Config": {"User": ""},
                            "State": {"Running": True},
                            "Mounts": [
                                {
                                    "Destination": "/app/server/storage",
                                    "Type": "volume",
                                    "Name": "mail",
                                }
                            ],
                        }
                    ]
                )
            )
        if args == ["systemctl", "--user", "stop", service]:
            raise InterruptedError("stop already took effect")
        return Mock(stdout="")

    with pytest.raises(InterruptedError):
        module.maintain(
            Settings(state_dir=state),
            backup_dir=tmp_path / "backups",
            engine="podman",
            backend_service="proton-rag-anything.service",
            run=run,
            ready=lambda: None,
        )
    assert ["systemctl", "--user", "start", service] in calls
