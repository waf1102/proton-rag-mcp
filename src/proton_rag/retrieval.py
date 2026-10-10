"""Bounded query snapshots and compact MCP pages; no cloud retrieval."""

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
import base64
import hmac
import json
import re
import secrets
import time

BUDGET = 8192


def wire_size(value):
    # Conservative for both SDK JSON serializers (escaping Unicode costs more).
    return len(json.dumps(value, ensure_ascii=True, indent=2).encode())


def filters(sent_after=None, sent_before=None, sender=None, subject=None, folder=None):
    result = {
        "sent_after": sent_after,
        "sent_before": sent_before,
        "sender": sender,
        "subject": subject,
        "folder": folder,
    }
    for field in ["sent_after", "sent_before"]:
        value = result[field]
        if value is not None:
            try:
                date = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
                result[field] = date.timestamp()
            except (ValueError, TypeError, OverflowError):
                raise ValueError("Invalid header date; use an ISO date or timestamp") from None
    if (
        result["sent_after"] is not None
        and result["sent_before"] is not None
        and result["sent_after"] >= result["sent_before"]
    ):
        raise ValueError("Header date range must have sent_after before sent_before")
    return result


@dataclass
class Snapshot:
    identity: str
    signature: str
    keys: list[str]
    created: float
    exact: bool
    size: int


class Pages:
    def __init__(
        self, *, clock=time.monotonic, ttl=600, max_sessions=16, max_bytes=64 * 1024 * 1024
    ):
        self.clock, self.ttl, self.max_sessions, self.max_bytes = (
            clock,
            ttl,
            max_sessions,
            max_bytes,
        )
        self.sessions = OrderedDict()
        self.secret = secrets.token_bytes(32)

    def expire(self):
        for identity, s in list(self.sessions.items()):
            if self.clock() - s.created >= self.ttl:
                del self.sessions[identity]

    def create(self, signature, keys, exact):
        self.expire()
        size = sum(len(k.encode()) for k in keys)
        if size > self.max_bytes:
            raise ValueError("Search snapshot exceeds memory limit; narrow the filters")
        while self.sessions and (
            len(self.sessions) >= self.max_sessions
            or sum(s.size for s in self.sessions.values()) + size > self.max_bytes
        ):
            self.sessions.popitem(last=False)
        snapshot = Snapshot(secrets.token_hex(12), signature, keys, self.clock(), exact, size)
        self.sessions[snapshot.identity] = snapshot
        return snapshot

    def token(self, snapshot, offset):
        raw = bytes.fromhex(snapshot.identity) + offset.to_bytes(4, "big")
        sig = hmac.digest(self.secret, raw, "sha256")[:12]
        return base64.urlsafe_b64encode(raw + sig).decode().rstrip("=")

    def resume(self, token, signature):
        self.expire()
        try:
            value = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
            raw, sig = value[:16], value[16:]
            if len(value) != 28 or not hmac.compare_digest(
                hmac.digest(self.secret, raw, "sha256")[:12], sig
            ):
                raise ValueError()
            s = self.sessions[raw[:12].hex()]
            offset = int.from_bytes(raw[12:], "big")
            if s.signature != signature or offset > len(s.keys):
                raise ValueError()
            return s, offset
        except (ValueError, KeyError, OverflowError):
            raise ValueError(
                "Invalid, expired or evicted cursor; repeat the original query and filters"
            ) from None


def preview(text, query):
    terms = re.findall(r"\w+", query)
    match = None
    for term in sorted(terms, key=len, reverse=True):
        match = re.search(r"\b" + re.escape(term) + r"\b", text, re.IGNORECASE)
        if match:
            break
    start = max(0, match.start() - 100) if match else 0
    return text[start : start + 300]


def compact(hit, query):
    meta = hit["metadata"]
    return {
        "message_id": hit["message_id"],
        "citation": hit["citation"],
        "metadata": {
            key: str(meta.get(key, "") or "")[:300] for key in ["subject", "sender", "date"]
        },
        "folders": [location["folder"] for location in hit["locations"]],
        "text": preview(hit["text"], query),
        "excerpt": True,
        "untrusted": True,
    }


def coverage_summary(catalog):
    status = catalog.coverage()
    return {
        key: status[key]
        for key in [
            "coverage_complete",
            "unique_messages_indexed",
            "unique_full_text_available",
            "indexed_date_range",
        ]
    }
