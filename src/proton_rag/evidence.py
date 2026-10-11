"""Bounded local regex evidence extraction, independent of MCP registration."""

from bisect import bisect_right
from contextlib import closing
import json
import re
import sqlite3
import time
from urllib.parse import quote, unquote, urlsplit

import regex

from .lexical import predicate
from .retrieval import Pages, coverage_summary, serialize, wire_size


DEFAULT_BYTES = 16000
MAX_MESSAGE_CHARS = 8 * 1024 * 1024


def citation(row):
    return f"imap:///{quote(row['folder'], safe='')}/{row['validity']}/{row['uid']}#{row['digest']}"


def resolve(catalog, message_id):
    """Resolve canonical IDs, legacy aliases and existing stable citations."""
    row = catalog.message(key=message_id)
    if row is not None and not row["active"]:
        groups, _ = catalog.locations([message_id])
        active = groups.get(row["key"], [])
        if active:
            row = catalog.message(key=active[0]["key"])
    if row is None:
        parsed = urlsplit(message_id)
        parts = parsed.path.removeprefix("/").split("/")
        if parsed.scheme == "imap" and not parsed.netloc and not parsed.query and len(parts) == 3:
            row = catalog.message(identity=(unquote(parts[0]), parts[1], parts[2], parsed.fragment))
            if row and citation(row) != message_id:
                row = None
    return row


def passages(text, compiled, context, *, deadline, clock, regex_seconds, max_bytes):
    """Merge match windows across nonblank lines, retaining separate repeated evidence.

    Pathological single lines are centered around matches, with explicit clipping flags.
    The regex runs against original text, before display whitespace is compacted.
    """
    windows, nonblank, clipped = [], None, False
    starts, ends = [], []
    size = 0
    remaining = deadline - clock()
    if remaining <= 0:
        raise ValueError("Evidence scan time limit reached; narrow filters or message_ids")
    try:
        matches = compiled.finditer(text, timeout=min(regex_seconds, remaining))
        # Find matches before building line positions for nonmatching messages.
        for match in matches:
            if clock() >= deadline:
                raise ValueError("Evidence scan time limit reached; narrow filters or message_ids")
            if nonblank is None:
                nonblank = []
                for line in re.finditer(r"[^\r\n]+", text):
                    if line[0].strip():
                        nonblank.append(line.span())
                        if len(nonblank) * 256 > max_bytes:
                            raise ValueError(
                                "Evidence line map exceeds memory limit; narrow filters"
                            )
                    if clock() >= deadline:
                        raise ValueError(
                            "Evidence scan time limit reached; narrow filters or message_ids"
                        )
                starts = [start for start, _ in nonblank]
                ends = [end for _, end in nonblank]
            first = bisect_right(ends, match.start())
            last = max(0, bisect_right(starts, max(match.start(), match.end() - 1)) - 1)
            if nonblank:
                first = min(first, len(nonblank) - 1)
                last = min(last, len(nonblank) - 1)
                first_context = max(0, first - context)
                last_context = min(len(nonblank) - 1, last + context)
                start = nonblank[first_context][0]
                end = nonblank[last_context][1]
            else:
                start, end = match.span()
            # Avoid returning thousands of irrelevant characters from one HTML-derived line.
            bounded_start, bounded_end = start, end
            if nonblank:
                # Blank HTML layout gaps must not count against nonblank-line context.
                if nonblank[first_context][1] - start > 1000:
                    bounded_start = (
                        max(start, match.start() - 500)
                        if first_context == first
                        else nonblank[first_context][1] - 500
                    )
                if end - nonblank[last_context][0] > 1000:
                    bounded_end = (
                        min(end, match.end() + 500)
                        if last_context == last
                        else nonblank[last_context][0] + 500
                    )
            clipped |= bounded_start != start or bounded_end != end
            start, end = bounded_start, bounded_end
            if windows and start <= windows[-1][1]:
                old_start, old_end = windows[-1]
                end = max(end, old_end)
                size += end - old_end
                windows[-1] = min(start, old_start), end
            else:
                windows.append((start, end))
                size += end - start + 128
            if size * 8 > max_bytes:
                raise ValueError(
                    "Evidence snapshot exceeds memory limit; narrow pattern or filters"
                )
    except TimeoutError:
        raise ValueError(
            "Regex too expensive (timeout); simplify pattern or narrow message_ids"
        ) from None
    result = []
    for start, end in windows:
        result.append(
            "\n".join(
                " ".join(line.split()) for line in text[start:end].splitlines() if line.strip()
            )
        )
    return result, clipped


class Evidence:
    def __init__(
        self,
        catalog,
        state=None,
        *,
        clock=time.monotonic,
        scan_seconds=10,
        regex_seconds=0.1,
        max_memory=64 * 1024 * 1024,
        ttl=600,
    ):
        self.catalog, self.state = catalog, state
        self.clock, self.scan_seconds, self.regex_seconds = clock, scan_seconds, regex_seconds
        self.pages = Pages(clock=clock, ttl=ttl, max_bytes=max_memory)

    def scan(self, pattern, selected, message_ids=None, context=2):
        if not pattern or len(pattern) > 4000:
            raise ValueError("Provide a nonempty regex pattern of at most 4000 characters")
        if not 0 <= context <= 10:
            raise ValueError("Context must be between 0 and 10 nonblank lines")
        try:
            compiled = regex.compile(pattern, regex.IGNORECASE | regex.MULTILINE)
        except (regex.error, RecursionError):
            raise ValueError(
                "Invalid regex pattern; use terms joined with | or a valid regex"
            ) from None
        where, args = predicate(self.catalog, selected)
        if message_ids is not None:
            if not 1 <= len(message_ids) <= 100:
                raise ValueError("Provide between 1 and 100 message_ids")
            keys = []
            for key in message_ids:
                row = resolve(self.catalog, key)
                if row is None or not row["active"]:
                    raise ValueError("Requested message not found in the active index")
                keys.append(row["key"])
            where += " AND m.key IN (" + ",".join("?" for _ in keys) + ")"
            args.extend(keys)
        sql = (
            "SELECT m.key,m.metadata,t.text,t.skipped,t.truncated,length(t.text) AS chars "
            "FROM messages m LEFT JOIN message_dates md ON md.key=m.key "
            "LEFT JOIN message_text t ON t.key=m.key WHERE " + where + " ORDER BY m.key"
        )
        deadline = self.clock() + self.scan_seconds
        units, scanned, missing, matched, size = [], 0, 0, 0, 0
        priorities = {}
        try:
            with closing(self.catalog.connect()) as db:
                db.set_progress_handler(lambda: int(self.clock() >= deadline), 1000)
                # SQL guards avoid materializing an oversized cached body in Python.
                oversized = db.execute(
                    "SELECT 1 FROM messages m LEFT JOIN message_dates md ON md.key=m.key "
                    "JOIN message_text t ON t.key=m.key WHERE "
                    + where
                    + " AND length(t.text)>? LIMIT 1",
                    [*args, MAX_MESSAGE_CHARS],
                ).fetchone()
                if oversized:
                    raise ValueError("Message exceeds scan size limit; narrow filters or read_mail")
                bound = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='index_messages'"
                ).fetchone()
                for row in db.execute(sql, args):
                    if self.clock() >= deadline:
                        raise ValueError(
                            "Evidence scan time limit reached; narrow filters or message_ids"
                        )
                    if (
                        not bound
                        and self.state is not None
                        and self.state.phases([row["key"]]).get(row["key"]) != "ready"
                    ):
                        continue
                    if row["text"] is None:
                        missing += 1
                        continue
                    scanned += 1
                    message_deadline = min(deadline, self.clock() + self.regex_seconds)
                    found, clipped = passages(
                        row["text"],
                        compiled,
                        context,
                        deadline=message_deadline,
                        clock=self.clock,
                        regex_seconds=self.regex_seconds,
                        max_bytes=self.pages.max_bytes - size,
                    )
                    if not found:
                        continue
                    locations, _ = self.catalog.locations([row["key"]])
                    if row["key"] not in locations:
                        continue
                    location = sorted(
                        locations[row["key"]],
                        key=lambda r: (r["key"] != row["key"], r["folder"], r["uid"]),
                    )[0]
                    metadata = json.loads(row["metadata"])
                    try:

                        def header_match(field):
                            remaining = message_deadline - self.clock()
                            if remaining <= 0:
                                raise TimeoutError()
                            matched = compiled.search(
                                str(metadata.get(field, "") or ""), timeout=remaining
                            )
                            if self.clock() >= message_deadline:
                                raise TimeoutError()
                            return bool(matched)

                        priorities[row["key"]] = (
                            not header_match("subject"),
                            not header_match("sender"),
                            row["key"],
                        )
                    except TimeoutError:
                        raise ValueError(
                            "Regex too expensive (timeout); simplify pattern or narrow message_ids"
                        ) from None
                    source = {
                        "message_id": row["key"],
                        "citation": citation(location),
                        "metadata": {
                            k: str(metadata.get(k, "") or "")[:300]
                            for k in ["subject", "sender", "date"]
                        },
                        "extraction": {
                            "text_truncated": bool(row["truncated"])
                            if row["truncated"] is not None
                            else None,
                            "skipped_parts": json.loads(row["skipped"]),
                        },
                        "context_clipped": clipped,
                        "untrusted": True,
                    }
                    # Fixed small units let an opaque offset paginate even a huge regex match.
                    for passage_index, passage in enumerate(found):
                        for start in range(0, max(1, len(passage)), 500):
                            unit = {
                                **source,
                                "passages": [passage[start : start + 500]],
                                "_passage": passage_index,
                            }
                            size += 8 * wire_size(unit) + 128
                            if size > self.pages.max_bytes:
                                raise ValueError(
                                    "Evidence snapshot exceeds memory limit; narrow pattern or filters"
                                )
                            units.append(unit)
                    matched += 1
        except sqlite3.OperationalError as error:
            if "interrupt" in str(error):
                raise ValueError(
                    "Evidence scan time limit reached; narrow filters or message_ids"
                ) from None
            raise
        units.sort(key=lambda unit: priorities[unit["message_id"]])
        if self.clock() >= deadline:
            raise ValueError("Evidence scan time limit reached; narrow filters or message_ids")
        return {
            "units": units,
            "searched_messages": scanned,
            "unavailable_messages": missing,
            "matched_messages": matched,
        }

    def query(
        self,
        *,
        pattern=None,
        selected=None,
        message_ids=None,
        context=None,
        max_bytes=None,
        cursor=None,
    ):
        if cursor:
            if (
                pattern is not None
                or message_ids is not None
                or context is not None
                or max_bytes is not None
                or any(v is not None for v in (selected or {}).values())
            ):
                raise ValueError("Continue with cursor alone; repeat the query to change arguments")
            snapshot, offset = self.pages.resume(cursor, None)
            saved = json.loads(snapshot.signature)
            selected, max_bytes = saved["filters"], saved["max_bytes"]
        else:
            selected = selected or {}
            context = 2 if context is None else context
            max_bytes = DEFAULT_BYTES if max_bytes is None else max_bytes
            if not 4000 <= max_bytes <= 32000:
                raise ValueError("max_bytes must be between 4000 and 32000")
            data = self.scan(pattern, selected, message_ids, context)
            snapshot = self.pages.create(
                serialize({"filters": selected, "max_bytes": max_bytes}),
                [u["message_id"] for u in data["units"]],
                True,
                payload=data,
            )
            offset = 0
        data = snapshot.payload
        result = {
            "sources": [],
            "data_path": "local_only",
            "coverage": coverage_summary(self.catalog),
            "search_complete": True,
            "searched_messages": data["searched_messages"],
            "unavailable_messages": data["unavailable_messages"],
            "matched_messages": data["matched_messages"],
            "displayed_messages": 0,
            "has_more": False,
            "next_call": None,
            "notice": "Cached-text regex scan complete; only displayed passages support this answer. Header dates are not event dates. Skipped attachments and uncached mail are outside this search.",
        }
        where, args = predicate(self.catalog, selected)
        # Reserve all pagination framing before allocating evidence bytes.
        framing = {
            **result,
            "has_more": True,
            "next_call": {
                "tool": "grep_mail",
                "arguments": {"cursor": self.pages.token(snapshot, offset)},
            },
        }
        checked = {}
        units = data["units"]

        def render(start, end, current_citation):
            source = {k: v for k, v in units[start].items() if k != "_passage"}
            source["citation"] = current_citation
            source["passages"] = []
            previous = None
            for unit in units[start:end]:
                if unit["_passage"] == previous:
                    source["passages"][-1] += unit["passages"][0]
                else:
                    source["passages"].append(unit["passages"][0])
                previous = unit["_passage"]
            return source

        def fits(source, current):
            sources = [*current, source]
            return (
                wire_size({**framing, "sources": sources, "displayed_messages": len(sources)})
                <= max_bytes
            )

        with closing(self.catalog.connect()) as db:
            while offset < len(units):
                key = units[offset]["message_id"]
                end = offset + 1
                while end < len(units) and units[end]["message_id"] == key:
                    end += 1
                if key not in checked:
                    checked[key] = (
                        db.execute(
                            "SELECT 1 FROM messages m LEFT JOIN message_dates md ON md.key=m.key WHERE "
                            + where
                            + " AND m.key=?",
                            [*args, key],
                        ).fetchone()
                        is not None
                    )
                    if checked[key] and self.state is not None:
                        checked[key] = self.state.phases([key]).get(key) == "ready"
                if not checked[key]:
                    offset = end
                    continue
                groups, _ = self.catalog.locations([key])
                if key not in groups:
                    offset = end
                    continue
                current = sorted(
                    groups[key], key=lambda r: (r["key"] != key, r["folder"], r["uid"])
                )[0]
                current_citation = citation(current)
                # Keep a message together whenever it can fit alone on a fresh page.
                # Avoid repeatedly materializing very large matches just to reject them.
                whole = None
                if sum(len(u["passages"][0]) for u in units[offset:end]) <= max_bytes:
                    whole = render(offset, end, current_citation)
                if whole is not None and fits(whole, result["sources"]):
                    result["sources"].append(whole)
                    offset = end
                    continue
                if whole is not None and result["sources"] and fits(whole, []):
                    break
                low, high = offset, min(end, offset + max_bytes // 500 + 1)
                while low < high:
                    middle = (low + high + 1) // 2
                    if fits(render(offset, middle, current_citation), result["sources"]):
                        low = middle
                    else:
                        high = middle - 1
                if low == offset:
                    if not result["sources"]:
                        raise ValueError(
                            "Evidence metadata and passage chunk exceed page budget; use read_mail by message_id or increase max_bytes"
                        )
                    break
                result["sources"].append(render(offset, low, current_citation))
                offset = low
                if offset < end:
                    break
        result["displayed_messages"] = len(result["sources"])
        result["has_more"] = offset < len(snapshot.keys)
        if result["has_more"]:
            result["next_call"] = {
                "tool": "grep_mail",
                "arguments": {"cursor": self.pages.token(snapshot, offset)},
            }
        return result
