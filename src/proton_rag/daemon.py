import argparse
import fcntl
import json
import logging
import os
import signal
import threading
from pathlib import Path
from .anything import Anything
from .mailbox import Snapshot, TestMailbox
from .sync import Catalog, synchronize


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--synthetic-dir", type=Path)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    state = Path(os.environ["RAG_STATE_DIR"])
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (state / "daemon.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stop = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stop.set())
        backend = Anything(
            os.environ.get("ANYTHING_URL", "http://127.0.0.1:3001"), os.environ["ANYTHING_API_KEY"]
        )
        catalog = Catalog(state / "catalog.db")
        backoff = 2
        while not stop.is_set():
            mailbox = None
            try:
                if args.synthetic_dir:
                    # Fixture directory must be explicitly selected, never infer synthetic from mail headers.
                    files = sorted(args.synthetic_dir.glob("*.eml"))
                    snapshot = Snapshot("1", {p.stem: p.read_bytes() for p in files})
                else:
                    if os.environ.get("RAG_ENABLE_PRIVATE_INGESTION") != "1":
                        raise RuntimeError("Live ingestion disabled")
                    mailbox = TestMailbox.connect(
                        os.environ["IMAP_USER"], os.environ["IMAP_PASS"], os.environ["IMAP_CA_FILE"]
                    )
                    snapshot = mailbox.snapshot()
                result = synchronize(catalog, backend, snapshot, synthetic=bool(args.synthetic_dir))
                logging.info(json.dumps({"event": "sync_ok", **result}))
                backoff = 2
                if args.once:
                    return
                stop.wait(30)
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
