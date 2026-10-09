"""Local disk backpressure and explicitly redacted operator diagnostics."""

import imaplib
import logging
import json
import shutil
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from .anything import IndexError, PendingUploadError
from .mailbox import SnapshotError


class DiskLowError(RuntimeError):
    pass


def diagnostic(error):
    if isinstance(error, IndexError):
        return error.diagnostic
    if isinstance(error, PendingUploadError):
        return {"code": "upload_reconciliation", "operation": "upload"}
    if isinstance(error, DiskLowError):
        return {"code": "low_disk", "operation": "storage"}
    if isinstance(error, (TimeoutError, ConnectionError, imaplib.IMAP4.abort)):
        return {"code": "bridge_connection", "operation": "imap"}
    if isinstance(error, sqlite3.Error):
        return {"code": "catalog_error", "operation": "catalog"}
    if isinstance(error, SnapshotError):
        # These are fixed application strings. Never include arbitrary exception text.
        codes = {
            "Mailbox changed during synchronization": "mailbox_changed",
            "Listing count changed": "mailbox_changed",
            "Parser failed; preserve prior retrieval data": "parse_failed",
            "Synchronization interrupted": "interrupted",
            "Bridge connection failed": "bridge_connection",
            "Message size unavailable": "message_size_unavailable",
            "Incomplete message fetch": "message_fetch_incomplete",
            "UID mismatch": "message_uid_mismatch",
            "Folder unavailable": "folder_unavailable",
            "Missing UIDVALIDITY": "uidvalidity_missing",
        }
        return {"code": codes.get(str(error), "imap_snapshot"), "operation": "imap"}
    return {"code": "unexpected_error", "operation": "sync"}


def report_error(catalog, event, error):
    details = diagnostic(error)
    failure = {**details, "at": datetime.now(timezone.utc).isoformat()}
    catalog.update_runtime(last_error=failure)
    logging.warning(json.dumps({"event": event, **details}))


def check_storage(settings, catalog):
    paths = [settings.state_dir, *(Path(p).expanduser() for p in settings.storage_paths)]
    free = min(shutil.disk_usage(path).free for path in paths)
    state = "paused" if free < settings.min_free_bytes else "running"
    catalog.update_runtime(
        state=state,
        reason="low_disk" if state == "paused" else None,
        free_bytes=free,
        min_free_bytes=settings.min_free_bytes,
        disk_warning=free < settings.warn_free_bytes,
    )
    if free < settings.warn_free_bytes:
        logging.warning(
            json.dumps(
                {"event": "disk_low", "free_bytes": free, "min_free_bytes": settings.min_free_bytes}
            )
        )
    if state == "paused":
        raise DiskLowError("Ingestion paused until disk space recovers")
