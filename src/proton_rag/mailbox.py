"""A deliberately read-only IMAP adapter: no mailbox mutation methods exist."""

import imaplib
import re
import ssl
from dataclasses import dataclass

FOLDER = "Folders/test"
MAX_MESSAGE = 8 * 1024 * 1024
MAX_MESSAGES = 2000


class BoundaryError(ValueError):
    pass


class SnapshotError(RuntimeError):
    pass


@dataclass(frozen=True)
class Snapshot:
    validity: str
    messages: dict[str, bytes]
    complete: bool = True


class TestMailbox:
    def __init__(self, wire, folder=FOLDER):
        if folder != FOLDER:
            raise BoundaryError("Only exact Folders/test is allowed")
        self.wire = wire

    @classmethod
    def connect(cls, user, password, ca_file):
        # Runs on the host, never in a container where loopback would mean something else.
        context = ssl.create_default_context(cafile=ca_file)
        wire = imaplib.IMAP4("127.0.0.1", 1143, timeout=30)
        try:
            wire.starttls(ssl_context=context)
            wire.login(user, password)
            return cls(wire)
        except Exception:
            wire.shutdown()
            raise SnapshotError("Bridge connection failed") from None

    def _uids(self):
        status, values = self.wire.uid("SEARCH", None, "ALL")
        if status != "OK" or len(values) != 1 or not isinstance(values[0], bytes):
            raise SnapshotError("Incomplete UID listing")
        uids = values[0].split()
        if len(uids) > MAX_MESSAGES or any(not u.isdigit() for u in uids):
            raise SnapshotError("Invalid or oversized UID listing")
        if len(set(uids)) != len(uids):
            raise SnapshotError("Duplicate UID listing")
        return uids

    def snapshot(self):
        status, counts = self.wire.select('"Folders/test"', readonly=True)
        if status != "OK" or len(counts) != 1 or not counts[0].isdigit():
            raise SnapshotError("Test folder unavailable")
        _, validity = self.wire.response("UIDVALIDITY")
        if (
            not validity
            or len(validity) != 1
            or not isinstance(validity[0], bytes)
            or not validity[0].isdigit()
        ):
            raise SnapshotError("Missing UIDVALIDITY")
        uids = self._uids()
        if len(uids) != int(counts[0]):
            raise SnapshotError("Listing count changed")
        messages = {}
        total_bytes = 0
        for uid in uids:
            status, sizes = self.wire.uid("FETCH", uid, "(UID RFC822.SIZE)")
            expected = None
            if status == "OK":
                for row in sizes:
                    if isinstance(row, bytes):
                        match = re.search(rb"UID (\d+).*RFC822.SIZE (\d+)", row)
                        if match and match[1] == uid:
                            expected = int(match[2])
            if expected is None:
                raise SnapshotError("Message size unavailable")
            total_bytes += min(expected, MAX_MESSAGE)
            if total_bytes > 64 * 1024 * 1024:
                raise SnapshotError("Snapshot byte limit exceeded")
            if expected > MAX_MESSAGE:
                # Preserve identity in the complete inventory, but never fetch an oversized body.
                messages[uid.decode()] = b""
                continue
            status, rows = self.wire.uid("FETCH", uid, "(UID BODY.PEEK[])")
            bodies = [row for row in rows if isinstance(row, tuple)] if status == "OK" else []
            if len(bodies) != 1 or len(bodies[0][1]) != expected:
                raise SnapshotError("Incomplete message fetch")
            match = re.search(rb"UID (\d+)", bodies[0][0])
            if not match or match[1] != uid:
                raise SnapshotError("UID mismatch")
            messages[uid.decode()] = bodies[0][1]
        if self._uids() != uids:
            raise SnapshotError("Mailbox changed during snapshot")
        return Snapshot(validity[0].decode(), messages)

    def close(self):
        # LOGOUT, not CLOSE (which can expunge in writable sessions).
        try:
            self.wire.logout()
        except Exception:
            pass
