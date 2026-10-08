"""Incremental read-only synchronization of configured mail folders."""

import argparse
import fcntl
import json
import logging
import os
import signal
import threading
from pathlib import Path
from .anything import Anything
from .config import Settings
from .mailbox import Mailbox, Snapshot
from .sync import Catalog, synchronize, synchronize_folder


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
        backend = Anything(
            settings.anything_url, os.environ["ANYTHING_API_KEY"], settings.workspace
        )
        catalog = Catalog(settings.state_dir / "catalog.db")
        backoff = 2
        while not stop.is_set():
            mailbox = None
            failed = False
            try:
                backend.refresh()
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
                    for folder in folders:
                        if stop.is_set():
                            break
                        try:
                            if folder not in available:
                                raise ValueError("Configured folder unavailable")
                            result = synchronize_folder(
                                catalog, backend, mailbox, folder, settings, stop
                            )
                            logging.info(json.dumps({"event": "folder_sync_ok", **result}))
                        except Exception:
                            failed = True
                            logging.warning(json.dumps({"event": "folder_sync_failed"}))
                    # Missing folders retain their catalog until an operator explicitly reindexes.
                if args.once:
                    raise SystemExit(1 if failed else 0)
                backoff = min(backoff * 2, 60) if failed else settings.poll_seconds
                stop.wait(backoff)
            except Exception:
                logging.warning(json.dumps({"event": "sync_failed", "retry_seconds": backoff}))
                if args.once:
                    raise SystemExit(1) from None
                stop.wait(backoff)
                backoff = min(backoff * 2, 60)
            finally:
                if mailbox:
                    mailbox.close()


if __name__ == "__main__":
    main()
