"""Offline, backed-up LanceDB maintenance for the dedicated local installation."""

import argparse
import fcntl
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
import httpx
from .config import Settings
from .sync import Catalog


def _ready(settings):
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            with httpx.Client(timeout=5, trust_env=False) as client:
                if client.get(settings.anything_url + "/api/ping").status_code == 200:
                    return
        except httpx.HTTPError:
            pass
        time.sleep(2)
    raise RuntimeError("Local index did not become ready after maintenance")


def maintain(
    settings,
    *,
    backup_dir,
    engine="docker",
    container="proton-rag-anything",
    daemon_service="proton-rag-daemon.service",
    backend_service=None,
    retain_hours=1,
    run=None,
    ready=None,
):
    """Require supervised ingestion; retain two successful, private recovery snapshots."""
    run = subprocess.run if run is None else run
    ready = (lambda: _ready(settings)) if ready is None else ready
    backup_dir = Path(backup_dir).expanduser().absolute()
    state = settings.state_dir.resolve()
    if backup_dir.resolve() == state or backup_dir.resolve().is_relative_to(state):
        raise ValueError("Use a dedicated backup directory outside the mail state directory")
    if engine not in ("docker", "podman") or retain_hours < 1:
        raise ValueError("Invalid maintenance engine or retention")
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", settings.workspace):
        raise ValueError("Invalid workspace")
    marker = backup_dir / ".proton-rag-maintenance"
    if backup_dir.exists() and any(backup_dir.iterdir()) and not marker.is_file():
        raise ValueError("The backup directory must be dedicated to maintenance")
    backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup_dir.chmod(0o700)
    marker.touch(mode=0o600)
    settings.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (settings.state_dir / "maintenance.lock").open("a") as maintenance_lock:
        fcntl.flock(maintenance_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        inspected = json.loads(
            run([engine, "inspect", container], check=True, capture_output=True, text=True).stdout
        )[0]
        if not inspected["State"]["Running"]:
            raise RuntimeError("Start the index before running maintenance")
        mounts = [m for m in inspected["Mounts"] if m["Destination"] == "/app/server/storage"]
        if len(mounts) != 1 or mounts[0]["Type"] != "volume" or not mounts[0].get("Name"):
            raise ValueError("Maintenance requires a dedicated named storage volume")
        image, volume = inspected["Image"], mounts[0]["Name"]
        user = inspected["Config"].get("User")
        stopped_daemon = stopped_backend = False
        helper = None
        success = False
        try:
            stopped_daemon = True
            run(["systemctl", "--user", "stop", daemon_service], check=True, capture_output=True)
            stopped_backend = True
            if backend_service:
                run(
                    ["systemctl", "--user", "stop", backend_service],
                    check=True,
                    capture_output=True,
                )
            else:
                run([engine, "stop", "--time", "150", container], check=True, capture_output=True)
            if backend_service:
                # Quadlet removes its container on stop; a second inspect is invalid.
                status = run(
                    ["systemctl", "--user", "is-active", backend_service],
                    check=False,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                if status not in ("inactive", "failed"):
                    raise RuntimeError("Backend service is still running; refusing maintenance")
            else:
                running = run(
                    [engine, "inspect", "--format", "{{.State.Running}}", container],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                if running != "false":
                    raise RuntimeError("Backend is still running; refusing maintenance")
            consumers = run(
                [engine, "ps", "--filter", f"volume={volume}", "--format", "{{.ID}}"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            if consumers:
                raise RuntimeError(
                    "Another container is using the storage volume; refusing maintenance"
                )
            with (settings.state_dir / "daemon.lock").open("a") as daemon_lock:
                fcntl.flock(daemon_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                catalog = Catalog(settings.state_dir / "catalog.db")
                catalog.bind_workspace(settings.workspace)
                catalog.update_runtime(state="maintenance")
                snapshot = backup_dir / (
                    "snapshot-"
                    + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-")
                    + uuid.uuid4().hex
                )
                snapshot.mkdir(mode=0o700)
                with catalog.connect() as source, sqlite3.connect(snapshot / "catalog.db") as dest:
                    source.backup(dest)
                common = [engine, "run", "--rm", "--pull", "never", "--network", "none"]
                helper = "proton-rag-backup-" + uuid.uuid4().hex
                with (snapshot / "anything.tar.gz").open("xb") as archive:
                    run(
                        [
                            *common,
                            "--name",
                            helper,
                            "--volume",
                            f"{volume}:/app/server/storage:ro",
                            "--entrypoint",
                            "tar",
                            image,
                            "-czf",
                            "-",
                            "-C",
                            "/app/server/storage",
                            ".",
                        ],
                        check=True,
                        stdout=archive,
                        stderr=subprocess.PIPE,
                    )
                run([engine, "rm", "--force", helper], check=False, capture_output=True)
                helper = None
                (snapshot / ".complete").touch(mode=0o600)
                script = Path(__file__).with_name("lance-maintenance.cjs").read_text()
                command = [
                    *common,
                    "--name",
                    "proton-rag-optimize-" + uuid.uuid4().hex,
                    "-i",
                    "--volume",
                    f"{volume}:/app/server/storage",
                    "--workdir",
                    "/app/server",
                    "--entrypoint",
                    "node",
                    "--env",
                    f"RAG_MAINTENANCE_WORKSPACE={settings.workspace}",
                    "--env",
                    f"RAG_MAINTENANCE_RETAIN_HOURS={retain_hours}",
                ]
                if user:
                    command += ["--user", user]
                helper = command[command.index("--name") + 1]
                result = run(
                    [*command, image, "-"], input=script, check=True, capture_output=True, text=True
                )
                report = json.loads(result.stdout.strip().splitlines()[-1])
                if report.get("event") not in ("maintenance_ok", "maintenance_no_table"):
                    raise RuntimeError("Unexpected maintenance result")
                if report.get("rows_before") != report.get("rows_after"):
                    raise RuntimeError("Maintenance row count mismatch; backup retained")
                catalog.update_runtime(
                    last_maintenance={**report, "at": datetime.now(timezone.utc).isoformat()}
                )
                success = True
                # Remove only snapshots created by this utility, after a successful check.
                completed = sorted(
                    p
                    for p in backup_dir.glob("snapshot-*")
                    if p.is_dir() and not p.is_symlink() and (p / ".complete").is_file()
                )
                for old in completed[:-2]:
                    shutil.rmtree(old)
                return report
        finally:
            # Killing the attached engine CLI does not reliably kill its container.
            # Confirm the named helper has stopped before permitting any writers.
            if helper:
                run([engine, "rm", "--force", helper], check=False, capture_output=True)
                remaining = run(
                    [engine, "ps", "--filter", f"name=^{helper}$", "--format", "{{.ID}}"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                if remaining:
                    raise RuntimeError(
                        "Maintenance helper is still running; writers remain stopped"
                    )
            try:
                if stopped_backend:
                    if backend_service:
                        run(
                            ["systemctl", "--user", "start", backend_service],
                            check=True,
                            capture_output=True,
                        )
                    else:
                        run([engine, "start", container], check=True, capture_output=True)
                    ready()
            finally:
                if stopped_daemon:
                    try:
                        Catalog(settings.state_dir / "catalog.db").update_runtime(
                            state="running" if success else "degraded"
                        )
                    finally:
                        run(
                            ["systemctl", "--user", "start", daemon_service],
                            check=True,
                            capture_output=True,
                        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=["docker", "podman"], default="docker")
    parser.add_argument("--container", default="proton-rag-anything")
    parser.add_argument("--daemon-service", default="proton-rag-daemon.service")
    parser.add_argument(
        "--backend-service", help="Required when systemd manages the backend (Quadlet)"
    )
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--retain-hours", type=int, default=1)
    args = parser.parse_args()
    os.umask(0o077)

    def interrupted(*_):
        raise InterruptedError("Maintenance interrupted")

    signal.signal(signal.SIGTERM, interrupted)
    try:
        report = maintain(Settings.from_env(), **vars(args))
        print(json.dumps(report))
    except Exception as error:
        # Container stderr may contain private filenames; do not print it.
        print(json.dumps({"event": "maintenance_failed", "error_type": type(error).__name__}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
