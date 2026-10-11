"""Bounded related-message discovery; subject/participant matches are not RFC threads."""

from contextlib import closing
from email.utils import getaddresses
import json
import re
import sqlite3
import time

from .evidence import MAX_MESSAGE_CHARS, citation, resolve
from .lexical import predicate
from .retrieval import Pages, coverage_summary, serialize, wire_size


def normalized_subject(subject):
    return (
        re.sub(r"^(?:(?:re|fw|fwd)\s*:\s*)+", "", str(subject).strip(), flags=re.I)
        .strip()
        .casefold()
    )


def correspondence_keys(
    catalog, selected, *, max_bytes=64 * 1024 * 1024, scan_seconds=5, clock=time.monotonic
):
    """Optional, explicitly limited scope; apply before dense/BM25 ranking."""
    where, args = predicate(catalog, selected)
    deadline = clock() + scan_seconds
    with closing(catalog.connect()) as db:
        db.set_progress_handler(lambda: int(clock() >= deadline), 1000)
        db.create_function(
            "reply_subject",
            1,
            lambda s: bool(re.match(r"^\s*(?:re|fw|fwd)\s*:", str(s or ""), re.I)),
            deterministic=True,
        )
        keys, size = [], 0
        try:
            for row in db.execute(
                "SELECT m.key FROM messages m LEFT JOIN message_dates md ON md.key=m.key WHERE "
                + where
                + " AND (reply_subject(json_extract(m.metadata,'$.subject')) OR EXISTS "
                "(SELECT 1 FROM documents d WHERE d.message_key=m.key AND d.active=1 AND d.folder='Sent'))",
                args,
            ):
                if clock() >= deadline:
                    raise ValueError("Correspondence search time limit reached; narrow filters")
                size += len(row[0].encode())
                if size > max_bytes:
                    raise ValueError("Correspondence snapshot exceeds memory limit; narrow filters")
                keys.append(row[0])
        except sqlite3.OperationalError:
            raise ValueError("Correspondence search time limit reached; narrow filters") from None
        return keys


def participants(metadata):
    return {
        address.casefold()
        for _, address in getaddresses(
            [str(metadata.get(k, "")) for k in ["sender", "recipients", "cc"]]
        )
        if address
    }


def body_excerpt(text):
    # Preserve inline/bottom replies and HTML-flattened quoted text. Cached headers
    # are separate; the body cannot reliably distinguish new text from history.
    if re.match(r"^subject:", text, flags=re.I) and "\n\n" in text:
        text = text.split("\n\n", 1)[1]
    return "\n".join(" ".join(line.split()) for line in text.splitlines() if line.strip())


class RelatedMail:
    def __init__(self, catalog, state=None, *, clock=time.monotonic, scan_seconds=5, ttl=600):
        self.catalog, self.state, self.clock, self.scan_seconds = (
            catalog,
            state,
            clock,
            scan_seconds,
        )
        self.pages = Pages(clock=clock, ttl=ttl)

    def _active(self, db, key):
        where, args = predicate(self.catalog, {})
        active = (
            db.execute(
                "SELECT 1 FROM messages m LEFT JOIN message_dates md ON md.key=m.key WHERE "
                + where
                + " AND m.key=?",
                [*args, key],
            ).fetchone()
            is not None
        )
        return active and (self.state is None or self.state.phases([key]).get(key) == "ready")

    def query(self, *, message_id=None, subject=None, max_bytes=None, cursor=None):
        if cursor:
            if message_id is not None or subject is not None or max_bytes is not None:
                raise ValueError("Continue with cursor alone")
            snapshot, offset = self.pages.resume(cursor, None)
            saved = json.loads(snapshot.signature)
            max_bytes = saved["max_bytes"]
            seed_key = saved["seed"]
        else:
            max_bytes = 16000 if max_bytes is None else max_bytes
            if not 4000 <= max_bytes <= 32000:
                raise ValueError("max_bytes must be between 4000 and 32000")
            seed = resolve(self.catalog, message_id or "")
            if seed is None:
                raise ValueError("Use an active message_id or citation returned by search")
            seed_key = seed["message_key"]
            metadata = json.loads(seed["metadata"])
            people = participants(metadata)
            needle = normalized_subject(
                subject if subject is not None else metadata.get("subject", "")
            )
            if len(needle) < 3 or len(needle) > 1000 or len(people) < 2:
                raise ValueError(
                    "Related search needs a subject and at least two participant addresses; use search_mail filters instead"
                )
            deadline = self.clock() + self.scan_seconds
            where, args = predicate(self.catalog, {})
            with closing(self.catalog.connect()) as db:
                if not self._active(db, seed_key):
                    raise ValueError("Seed message is unavailable or not committed")
                db.create_function("mail_subject", 1, normalized_subject, deterministic=True)
                db.create_function(
                    "shared_people",
                    1,
                    lambda raw: len(people & participants(json.loads(raw))),
                    deterministic=True,
                )
                db.set_progress_handler(lambda: int(self.clock() >= deadline), 1000)
                match = (
                    "instr(mail_subject(json_extract(m.metadata,'$.subject')),?)>0"
                    if subject is not None
                    else "mail_subject(json_extract(m.metadata,'$.subject'))=?"
                )
                try:
                    rows = db.execute(
                        "SELECT m.key,m.metadata FROM messages m LEFT JOIN message_dates md ON md.key=m.key WHERE "
                        + where
                        + " AND "
                        + match
                        + " AND shared_people(m.metadata)>=2 ORDER BY md.sent_at DESC,m.key LIMIT 1001",
                        [*args, needle],
                    ).fetchall()
                except sqlite3.OperationalError:
                    raise ValueError(
                        "Related search time limit reached; narrow subject or use search_mail filters"
                    ) from None
                if len(rows) > 1000:
                    raise ValueError(
                        "More than 1000 related candidates; narrow the subject or use search_mail with date filters"
                    )
                units = []
                for row in rows:
                    if self.clock() >= deadline:
                        raise ValueError("Related search time limit reached; narrow the subject")
                    if (
                        self.state is not None
                        and self.state.phases([row["key"]]).get(row["key"]) != "ready"
                    ):
                        continue
                    cached = self.catalog.get_text(row["key"])
                    if cached is None:
                        raise ValueError(
                            "Related message text is unavailable; cannot claim search coverage"
                        )
                    if len(cached["text"]) > MAX_MESSAGE_CHARS:
                        raise ValueError("Related message exceeds text limit; use read_mail by ID")
                    text = body_excerpt(cached["text"])
                    md = json.loads(row["metadata"])
                    units.append(
                        {
                            "message_id": row["key"],
                            "metadata": {
                                k: str(md.get(k, "") or "")[:300]
                                for k in ["subject", "sender", "date"]
                            },
                            "text": text[:1600],
                            "display_truncated": len(text) > 1600,
                            "quoted_history_omitted": False,
                            "extraction": {
                                "text_truncated": cached["truncated"],
                                "skipped_parts": json.loads(cached["skipped"]),
                            },
                            "untrusted": True,
                        }
                    )
            snapshot = self.pages.create(
                serialize({"seed": seed_key, "max_bytes": max_bytes}),
                [u["message_id"] for u in units],
                False,
                payload=units,
            )
            offset = 0
        result = {
            "sources": [],
            "data_path": "local_only",
            "coverage": coverage_summary(self.catalog),
            "matched_messages": len(snapshot.keys),
            "displayed_messages": 0,
            "has_more": True,
            "next_call": {
                "tool": "related_mail",
                "arguments": {"cursor": self.pages.token(snapshot, offset)},
            },
            "exhaustive": False,
            "notice": "Related candidates share normalized subjects and at least two participant addresses; newest first. Renamed subjects/other addresses may be missed. Body excerpts can include quoted history; do not treat quoted promises as new commitments. Clipped bodies need read_mail before claiming no confirmation in a message. No matches do not prove a promise was never completed. Follow next_call to check remaining candidates before claiming no confirmation was found in this set.",
        }
        with closing(self.catalog.connect()) as db:
            if not self._active(db, seed_key):
                raise ValueError("Seed message is unavailable or not committed; repeat search")
            while offset < len(snapshot.keys):
                key = snapshot.keys[offset]
                if not self._active(db, key):
                    offset += 1
                    continue
                groups, _ = self.catalog.locations([key])
                if key not in groups:
                    offset += 1
                    continue
                location = sorted(
                    groups[key], key=lambda r: (r["key"] != key, r["folder"], r["uid"])
                )[0]
                source = {**snapshot.payload[offset], "citation": citation(location)}
                trial = {
                    **result,
                    "sources": [*result["sources"], source],
                    "displayed_messages": len(result["sources"]) + 1,
                }
                if wire_size(trial) > max_bytes:
                    if result["sources"]:
                        break
                    source["display_truncated"] = True
                    original = source["text"]
                    lo, hi = 0, len(original)
                    while lo < hi:
                        mid = (lo + hi + 1) // 2
                        source["text"] = original[:mid]
                        trial["sources"] = [source]
                        if wire_size(trial) <= max_bytes:
                            lo = mid
                        else:
                            hi = mid - 1
                    source["text"] = original[:lo]
                    if not lo:
                        raise ValueError(
                            "Related metadata exceeds page budget; increase max_bytes or use read_mail"
                        )
                result["sources"].append(source)
                offset += 1
        result["displayed_messages"] = len(result["sources"])
        result["has_more"] = offset < len(snapshot.keys)
        result["next_call"] = (
            {"tool": "related_mail", "arguments": {"cursor": self.pages.token(snapshot, offset)}}
            if result["has_more"]
            else None
        )
        return result
