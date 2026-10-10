"""Incremental read-only synchronization of configured mail folders."""

import argparse
import fcntl
import json
import logging
import os
import signal
import threading
from pathlib import Path
from .runtime import load_index
from .config import Settings
from .mailbox import Mailbox, Snapshot
from .sync import Catalog, synchronize, synchronize_folder, cleanup_duplicates, collect_orphans
from .health import check_storage, report_error, DiskLowError


def record_progress(catalog, values):
    catalog.update_runtime(last_progress=values)
    logging.info(json.dumps({"event": "sync_progress", **values}))


def prepare_batch(settings, catalog, backend, stop):
    check_storage(settings, catalog)
    if not stop.is_set():
        removed = cleanup_duplicates(catalog, backend, settings.batch_size)
        if removed:
            logging.info(json.dumps({"event": "duplicates_removed", "documents": removed}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--synthetic-dir", type=Path, help="Use isolated .eml fixtures instead of IMAP"
    )
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = Settings.from_env()
    settings.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (settings.state_dir / "daemon.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stop = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stop.set())
        catalog = Catalog(settings.state_dir / "catalog.db", writer=True)
        backend = load_index(settings, catalog, writer=True)
        backoff = 2
        while not stop.is_set():
            mailbox = None
            failed = False
            try:
                check_storage(settings, catalog)
                backend.refresh()
                collect_orphans(
                    catalog,
                    backend,
                    interrupted_only=True,
                    stop=stop,
                    before_each=lambda: check_storage(settings, catalog),
                )
                if args.synthetic_dir:
                    files = sorted(args.synthetic_dir.glob("*.eml"))
                    result = synchronize(
                        catalog,
                        backend,
                        Snapshot("1", {p.stem: p.read_bytes() for p in files}, folder="fixtures"),
                        synthetic=True,
                    )
                    logging.info(json.dumps({"event": "sync_ok", **result}))
                else:
                    mailbox = Mailbox.connect(
                        os.environ["IMAP_USER"],
                        os.environ["IMAP_PASS"],
                        os.environ.get("IMAP_CA_FILE"),
                        settings.imap_host,
                        settings.imap_port,
                        settings.max_message_bytes,
                    )
                    available = mailbox.folders()
                    folders = list(settings.folders) if settings.folders else available
                    folders = [f for f in folders if f not in settings.excluded_folders]
                    catalog.set_scope(folders)
                    # Discover coverage before spending hours ingesting the first large folder.
                    for folder in folders:
                        if stop.is_set():
                            break
                        try:
                            if folder not in available:
                                raise ValueError("Configured folder unavailable")
                            catalog.observe(mailbox.inventory(folder))
                        except Exception as error:
                            catalog.folder_finished(folder, successful=False)
                            report_error(catalog, "inventory_failed", error)
                            failed = True
                    for folder in folders:
                        if stop.is_set():
                            break
                        try:
                            if folder not in available:
                                raise ValueError("Configured folder unavailable")
                            result = synchronize_folder(
                                catalog,
                                backend,
                                mailbox,
                                folder,
                                settings,
                                stop,
                                progress=lambda values: record_progress(catalog, values),
                                before_batch=lambda: prepare_batch(
                                    settings, catalog, backend, stop
                                ),
                                on_error=lambda error: report_error(
                                    catalog, "message_sync_failed", error
                                ),
                            )
                            failed = failed or result["failed_messages"] > 0
                            logging.info(json.dumps({"event": "folder_sync_ok", **result}))
                        except DiskLowError:
                            raise
                        except Exception as error:
                            catalog.folder_finished(folder, successful=False)
                            failed = True
                            report_error(catalog, "folder_sync_failed", error)
                    # Missing folders retain their catalog until an operator explicitly reindexes.
                    if not failed and not stop.is_set() and catalog.coverage()["coverage_complete"]:
                        collect_orphans(
                            catalog,
                            backend,
                            stop=stop,
                            before_each=lambda: check_storage(settings, catalog),
                            defer_new=True,
                        )
                if args.once:
                    raise SystemExit(1 if failed else 0)
                catalog.update_runtime(state="degraded" if failed else "running")
                backoff = min(backoff * 2, 60) if failed else settings.poll_seconds
                stop.wait(backoff)
            except Exception as error:
                report_error(catalog, "sync_failed", error)
                if not isinstance(error, DiskLowError):
                    catalog.update_runtime(state="degraded")
                if args.once:
                    raise SystemExit(1) from None
                stop.wait(backoff)
                backoff = min(backoff * 2, 60)
            finally:
                if mailbox:
                    mailbox.close()


if __name__ == "__main__":
    main()
