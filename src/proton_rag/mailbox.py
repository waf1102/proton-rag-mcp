"""Read-only IMAP inventory and bounded fetching across selectable folders."""

import base64
import imaplib
import re
import ssl
from dataclasses import dataclass


class SnapshotError(RuntimeError):
    pass


def encode_folder(name):
    if not name or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ValueError("Invalid folder name")
    parts, pending = [], []

    def flush():
        if pending:
            encoded = base64.b64encode("".join(pending).encode("utf-16-be"))
            parts.append(b"&" + encoded.rstrip(b"=").replace(b"/", b",") + b"-")
            pending.clear()

    for char in name:
        if 32 <= ord(char) <= 126:
            flush()
            parts.append(b"&-" if char == "&" else char.encode("ascii"))
        else:
            pending.append(char)
    flush()
    return b"".join(parts)


def decode_folder(value):
    def replace(match):
        data = match.group(1)
        if not data:
            return "&"
        data = data.replace(b",", b"/")
        return base64.b64decode(data + b"=" * (-len(data) % 4), validate=True).decode("utf-16-be")

    parts, start = [], 0
    for match in re.finditer(rb"&([^-]*)-", value):
        parts.extend([value[start : match.start()].decode("ascii"), replace(match)])
        start = match.end()
    parts.append(value[start:].decode("ascii"))
    return "".join(parts)


@dataclass(frozen=True)
class Snapshot:
    validity: str
    messages: dict[str, bytes]
    complete: bool = True
    folder: str = "INBOX"


@dataclass(frozen=True)
class Inventory:
    folder: str
    validity: str
    uids: tuple[str, ...]


class Mailbox:
    def __init__(self, wire, max_message_bytes=32 * 1024 * 1024):
        self.wire = wire
        self.max_message_bytes = max_message_bytes

    @classmethod
    def connect(
        cls,
        user,
        password,
        ca_file=None,
        host="127.0.0.1",
        port=1143,
        max_message_bytes=32 * 1024 * 1024,
    ):
        context = ssl.create_default_context(cafile=ca_file)
        wire = imaplib.IMAP4(host, port, timeout=30)
        try:
            wire.starttls(ssl_context=context)
            wire.login(user, password)
            return cls(wire, max_message_bytes)
        except Exception:
            wire.shutdown()
            raise SnapshotError("Bridge connection failed") from None

    def folders(self):
        status, rows = self.wire.list()
        if status != "OK":
            raise SnapshotError("Folder listing failed")
        names = []
        literal_trailer = False
        for row in rows:
            if literal_trailer and row == b"":
                literal_trailer = False
                continue
            literal_trailer = False
            literal = None
            if isinstance(row, tuple):
                row, literal = row
                literal_trailer = True
            if not isinstance(row, bytes):
                raise SnapshotError("Incomplete folder listing")
            match = re.fullmatch(rb'\(([^)]*)\)\s+(?:"(?:\\.|[^"\\])*"|NIL)\s+(.+)', row)
            if not match:
                raise SnapshotError("Invalid folder listing")
            if b"\\noselect" in match[1].lower().split():
                continue
            name = match[2]
            if literal is not None:
                name = literal
            elif name.startswith(b'"') and name.endswith(b'"'):
                name = re.sub(rb"\\(.)", rb"\1", name[1:-1])
            names.append(decode_folder(name))
        return list(dict.fromkeys(names))

    def _uids(self):
        status, values = self.wire.uid("SEARCH", None, "ALL")
        if status != "OK" or len(values) != 1 or not isinstance(values[0], bytes):
            raise SnapshotError("Incomplete UID listing")
        uids = values[0].split()
        if any(not uid.isdigit() for uid in uids) or len(set(uids)) != len(uids):
            raise SnapshotError("Invalid UID listing")
        return tuple(uid.decode() for uid in uids)

    def inventory(self, folder):
        encoded = encode_folder(folder)
        quoted = b'"' + encoded.replace(b"\\", b"\\\\").replace(b'"', b'\\"') + b'"'
        status, counts = self.wire.select(quoted, readonly=True)
        if status != "OK" or len(counts) != 1 or not counts[0].isdigit():
            raise SnapshotError("Folder unavailable")
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
        return Inventory(folder, validity[0].decode(), uids)

    def verify(self, inventory):
        if set(self._uids()) != set(inventory.uids):
            raise SnapshotError("Mailbox changed during synchronization")

    def fetch(self, uid):
        if not uid.isdigit():
            raise ValueError("Invalid UID")
        status, sizes = self.wire.uid("FETCH", uid, "(UID RFC822.SIZE)")
        expected = None
        if status == "OK":
            for row in sizes:
                if isinstance(row, bytes):
                    identity = re.search(rb"\bUID (\d+)\b", row)
                    size = re.search(rb"\bRFC822.SIZE (\d+)\b", row)
                    if identity and size and identity[1].decode() == uid:
                        expected = int(size[1])
        if expected is None:
            raise SnapshotError("Message size unavailable")
        if expected > self.max_message_bytes:
            return None
        status, rows = self.wire.uid("FETCH", uid, "(UID BODY.PEEK[])")
        bodies = [row for row in rows if isinstance(row, tuple)] if status == "OK" else []
        if len(bodies) != 1 or len(bodies[0][1]) != expected:
            raise SnapshotError("Incomplete message fetch")
        headers = b" ".join(
            row[0] if isinstance(row, tuple) else row
            for row in rows
            if isinstance(row, (bytes, tuple))
        )
        match = re.search(rb"\bUID (\d+)\b", headers)
        if not match or match[1].decode() != uid:
            raise SnapshotError("UID mismatch")
        return bodies[0][1]

    def close(self):
        try:
            self.wire.logout()
        except Exception:
            pass
