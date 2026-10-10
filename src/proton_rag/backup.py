"""Paired private SQLite/Qdrant backups and isolated restore verification."""

import argparse
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid
from .catalog import Catalog
from .embeddings import EmbeddingProfile, Ollama
from .index import IndexFailure
from .index_state import IndexState
from .migration import clone_catalog, verify_target
from .migration_report import readonly, save_report
from .qdrant import QdrantIndex
from .config import Settings


def checksum(path):
    with Path(path).open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


@contextmanager
def paused_writer(service, *, run=subprocess.run):
    if not re.fullmatch(r"[A-Za-z0-9_.@-]+\.service", service):
        raise ValueError("Invalid daemon service")
    status = run(["systemctl", "--user", "is-active", service], capture_output=True, text=True)
    active = status.stdout.strip() == "active"
    if status.stdout.strip() not in ("active", "inactive", "failed"):
        raise RuntimeError("Cannot determine writer status")
    try:
        if active:
            run(["systemctl", "--user", "stop", service], check=True, capture_output=True)
        yield
    finally:
        if active:
            run(["systemctl", "--user", "start", service], check=True, capture_output=True)


def download(backend, name, target):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise ValueError("Invalid snapshot name")
    try:
        with backend.client.stream("GET", backend.url + "/snapshots/" + name) as response:
            response.raise_for_status()
            with target.open("xb") as file:
                target.chmod(0o600)
                for chunk in response.iter_bytes(1024 * 1024):
                    file.write(chunk)
    except Exception:
        raise IndexFailure("backup_download", "backup") from None


def backup(settings, backup_dir, backend):
    destination, state = Path(backup_dir).resolve(), settings.state_dir.resolve()
    if destination == state or destination.is_relative_to(state):
        raise ValueError("Use a backup directory outside the active state")
    existing = destination
    while not existing.exists():
        existing = existing.parent
    count = backend.request("POST", "/points/count", {"exact": True})["count"]
    estimate = (
        backend.catalog.path.stat().st_size * 2
        + count * (settings.embedding_dimension * 4 + 2048) * 2
    )
    if any(
        shutil.disk_usage(p).free < settings.min_free_bytes + estimate for p in (state, existing)
    ):
        raise RuntimeError("Insufficient backup space")
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination.chmod(0o700)
    with (state / "maintenance.lock").open("a") as maintenance:
        fcntl.flock(maintenance, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with (state / "daemon.lock").open("a") as writer:
            fcntl.flock(writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
            backend.bootstrap(create=False)
            result = destination / (
                datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex
            )
            result.mkdir(mode=0o700)
            clone_catalog(backend.catalog.path, result / "catalog.db")
            snapshot = backend.request("POST", "/snapshots", operation="backup")
            name = snapshot["name"]
            download(backend, name, result / "qdrant.snapshot")
            manifest = {
                "format": 1,
                "binding": backend.state.binding,
                "files": {
                    name: checksum(result / name) for name in ("catalog.db", "qdrant.snapshot")
                },
            }
            save_report(result / "manifest.json", manifest)
            validate_backup(result, require_complete=False)
            (result / ".complete").touch(mode=0o600)
            # The private downloaded snapshot is the recovery artifact. Avoid keeping a second
            # unbounded copy inside Qdrant's storage after every successful backup.
            backend.request("DELETE", "/snapshots/" + name, operation="backup_cleanup")
            return result


def validate_backup(path, *, require_complete=True):
    path = Path(path)
    if require_complete and not (path / ".complete").is_file():
        raise ValueError("Backup is incomplete")
    manifest = json.loads((path / "manifest.json").read_text())
    if manifest.get("format") != 1 or set(manifest.get("files", {})) != {
        "catalog.db",
        "qdrant.snapshot",
    }:
        raise ValueError("Unsupported backup manifest")
    for name, expected in manifest["files"].items():
        if checksum(path / name) != expected:
            raise ValueError("Backup checksum mismatch")
    with readonly(path / "catalog.db") as db:
        binding, published = db.execute(
            "SELECT binding,published FROM index_binding WHERE id=1"
        ).fetchone()
    if not published or json.loads(binding) != manifest["binding"]:
        raise ValueError("Backup binding mismatch")
    return manifest


def restore_check(settings, backup_dir, target_state, collection):
    """Restore only into a new directory and absent collection; never overwrite an index."""
    manifest = validate_backup(backup_dir)
    target = Path(target_state).resolve()
    if target.exists() or collection == manifest["binding"]["collection"]:
        raise ValueError("Restore verification requires a fresh state and distinct collection")
    settings = replace(settings, state_dir=target, workspace=collection)
    profile = EmbeddingProfile(**manifest["binding"]["profile"])
    target.mkdir(mode=0o700, parents=True)
    clone_catalog(Path(backup_dir) / "catalog.db", target / "catalog.db")
    binding = {**manifest["binding"], "collection": collection}
    catalog = Catalog(target / "catalog.db")
    with catalog.connect() as db:
        db.execute(
            "UPDATE index_binding SET binding=?,published=0 WHERE id=1", (json.dumps(binding),)
        )
        db.execute("UPDATE catalog_settings SET value=? WHERE name='workspace'", (collection,))
    state = IndexState(catalog, collection, profile)
    backend = QdrantIndex(
        settings,
        catalog,
        state,
        Ollama(settings.ollama_url, profile),
        key=os.environ.get("QDRANT_API_KEY", ""),
    )
    try:
        backend.request("GET", "")
    except IndexFailure as error:
        if error.diagnostic.get("http_status") != 404:
            raise
    else:
        raise ValueError("Restore collection already exists")
    try:
        with (Path(backup_dir) / "qdrant.snapshot").open("rb") as file:
            response = backend.client.post(
                backend.url + "/snapshots/upload?priority=snapshot",
                files={"snapshot": ("restore.snapshot", file)},
            )
            response.raise_for_status()
            if response.json().get("result") is not True:
                raise ValueError("Snapshot restore not acknowledged")
    except Exception:
        raise IndexFailure("backup_restore", "restore") from None
    original = backend.request("GET", "")
    metadata = original["config"].get("metadata", {}) or {}
    if metadata.get("proton_rag_binding") != manifest["binding"]:
        raise ValueError("Restored snapshot has a different binding")
    backend.request("PATCH", "", {"metadata": {"proton_rag_binding": binding}})
    backend.bootstrap(create=False)
    report = verify_target(Path(backup_dir) / "catalog.db", catalog.path, backend)
    state.publish()
    save_report(target / "restore-verification.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["backup", "restore-check"])
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--daemon-service", default="proton-rag-daemon.service")
    parser.add_argument("--target-state", type=Path)
    parser.add_argument("--collection")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        settings = Settings.from_env()
        if args.command == "backup":
            from .runtime import load_index

            with paused_writer(args.daemon_service):
                catalog = Catalog(settings.state_dir / "catalog.db")
                index = load_index(settings, catalog)
                path = backup(settings, args.backup_dir, index)
                print(json.dumps({"event": "backup_complete", "path": str(path)}))
        else:
            if not args.target_state or not args.collection:
                raise ValueError("Supply --target-state and --collection")
            print(
                json.dumps(
                    restore_check(settings, args.backup_dir, args.target_state, args.collection)
                )
            )
    except Exception as error:
        print(json.dumps({"event": "backup_failed", "error_type": type(error).__name__}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
