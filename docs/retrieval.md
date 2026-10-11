# Finding and reading your mail

For questions about situations or meaning, start with `search_mail` in hybrid mode, a short natural-language description and `max_bytes=16000`. You can include two different short paraphrases in `alternate_queries`; these run together with the same filters and interleave unique results within `limit`. This is bounded candidate discovery, not exhaustive coverage. Add an optional `lexical_query` using FTS5 word stems or alternatives when specific wording could help, for example `reschedul* OR postpon*`. It retrieves up to ten ranked full-text passages in the same call, applies the same filters and interleaves them with semantic results within `limit`. Omitting it preserves existing ranking. Only `lexical_query` uses Boolean syntax; hybrid descriptions remain natural language. For identifiers or known phrases, use `grep_mail` with related terms combined in one regex. Check later replies with `related_mail` before calling a promise unresolved. Answer when the evidence supports the requested facts; continue only for a specific missing fact or contradiction. Reserve exhaustive enumeration for explicit requests and disclose the limits of the examined messages.

Header-date filters restrict when an email was sent. Disclose any bounds and allow for earlier bookings and later notices when searching for events in a particular year. Exhaustive event-year coverage may require unbounded header dates. A ticket or boarding pass supports scheduled travel; it does not prove the trip occurred.

## Evidence passages

```json
{"pattern": "invoice|total|paid", "context": 2}
```

The case-insensitive multiline regex searches original cached extracted text from active, committed messages. It returns matching passages with surrounding nonblank lines, merged overlapping windows, compact subject/sender/header date, stable citations, and extraction warnings. Matches in subject/sender bring a message ahead of incidental body mentions while retaining every matching message. Display whitespace is compacted; full original text and character offsets remain available through `read_mail`. Long lines are centered around matches and mark `context_clipped`; separate repeated passages remain visible so conflicting revisions can be distinguished.

`context` accepts 0–10 surrounding nonblank lines (default 2). `max_bytes` accepts 4,000–32,000 serialized UTF-8 bytes (default 16,000). A client may save large results to a generated-output file; reading that file once can replace multiple small retrieval calls. Optional `message_ids` accepts up to 100 canonical IDs, legacy aliases or citations. The existing sender/subject/folder/header-date filters also apply. `correspondence_only=true` restricts the scan to reply/forward subjects or Sent membership and reports that scope. Omit it when checking direct incoming confirmations.

`search_complete` means the bounded regex scan finished over the snapshot's eligible cached text. `searched_messages`, `unavailable_messages`, and `matched_messages` describe that scan; `displayed_messages` describes this page. `has_more` and a cursor-only `next_call` identify undisplayed evidence. Follow every page for an exhaustive request. Cursors expire after ten minutes, restart or bounded-cache eviction. Activity, commit status and filters are rechecked on continuation; deletions can reduce displayed counts, and new mail requires a new search.

Regex execution is limited to 100 ms per message, the scan to ten seconds, each body to eight million characters, and snapshots to 16 sessions/64 MiB estimated memory. Invalid patterns or exceeded limits return actionable errors without claiming completeness. Narrow the pattern, filters or message IDs on those errors. Skipped attachments, unavailable text and unindexed mail remain outside the evidence. Email header dates are not event dates: an itinerary booked in December may concern January travel. Reservations and standby listings do not establish confirmed travel.

Use `search_mail` for semantic discovery or FTS5 enumeration when appropriate. Read full messages only when passages are insufficient or exact formatting matters.

## Search modes

**Hybrid** (default) combines semantic and lexical ranking. It is useful for questions in plain language. It returns a bounded set of candidates, not every matching message. `limit` selects up to 50 candidates (default 10); compact pages may hold fewer. Execute the returned `next_call` to see the rest of that selected set.

**Exact** lists every active, committed message matching the indexed words and filters. It searches subject, sender, and cached extracted body, including supported attachment text. Matches are ordered by text relevance, with extra weight for the subject and sender; pagination retains every match. Numeric terms match tokens: `3366` does not match `13366`. It uses [SQLite FTS5 syntax](https://www.sqlite.org/fts5.html#full_text_query_syntax):

```json
{"query": "\"American Airlines\" OR \"United Airlines\"", "mode": "exact", "limit": 50}
```

Other examples: `receipt AND hotel`, `flight NOT cancelled`, `"AA 3366"`. Quote punctuation-containing phrases. Exact search does not expand synonyms or infer that every itinerary contains the word “flight.” Its `match_count` counts emails at the start of the search, not flights or receipts inside them.

## Filters and pagination

Both modes accept case-insensitive `sender` and `subject` substrings and an exact `folder` membership. One email in several folders remains one result. `sent_after` is inclusive, `sent_before` exclusive; ISO dates without a timezone mean midnight UTC. Missing or invalid header dates are excluded when a date filter is used.

```json
{"query": "receipt", "mode": "exact", "sender": "airline", "sent_after": "2018-01-01", "sent_before": "2019-01-01", "limit": 50}
```

An empty exact query with filters browses the matching messages. Header dates are **email send dates**, not travel dates, invoice dates, or other dates in their contents.

Responses include `sources`, a small coverage summary, `exhaustive`, `has_more`, `remaining_count`, and a ready-to-use `next_call`. The existing `next_cursor` field remains available. `match_count` appears only in exact mode. When `has_more` is true, the page is incomplete. Execute `next_call.tool` with `next_call.arguments` until it is false. A continuation needs only `{"cursor":"<returned cursor>"}`; the server retains the original query, mode, limit and filters. Repeating those arguments also works, but changing them produces an explicit error. Cursors expire after ten minutes, a server restart, or eviction from the bounded cache; on an explicit cursor error, start the query again. Deletions between pages are hidden; the original match count may then exceed the number returned. Newly arriving messages require a new search.

Existing calls default to a search page under 4 KB of compact UTF-8 JSON result text. Calls that opt into `lexical_query` default to 16,000 bytes so keyword and semantic passages can fit together; an explicit budget always wins. `max_bytes` optionally accepts 3,900–32,000 bytes; use 16,000 for semantic evidence. Larger responses preserve up to 1,600 compacted characters of the actual retrieved chunk, including extraction warnings and clipping flags. They can require one generated-output file read. Hybrid matching passages are retained instead of being replaced by a keyword window in the full message. The response budget is included in the saved cursor. The same data is also provided as MCP structured content. The default stays below Gemini’s observed inline-output threshold; larger selections continue on subsequent pages. Sources include stable `message_id`, citation, subject/sender/date and folder names. Legacy default previews remain up to 300 characters. Full metadata is available from `read_mail`. An unusually large folder/citation payload produces an explicit budget error instead of silently dropping the message.

## Check related replies

`related_mail` accepts a found `message_id` or citation. It returns newest messages first, matching normalized subjects (removing Re/Fw/Fwd prefixes) and at least two shared participant addresses. Optional `subject` broadens the match to a substring for renamed exchanges. This is candidate discovery, not an RFC-thread identity or exhaustive coverage across alternate subjects/addresses.

```json
{"message_id": "<found ID>", "subject": "Receipt correction"}
```

Include optional `outcome_query` to add up to ten ranked FTS5 candidates across indexed mail outside the already-included thread, plus keyword passages for up to ten matching thread messages in the same call. Combine a quoted amount or reference with likely completion words, such as `"47.52" AND (refund OR received OR sent)`. An amount alone may be dominated by repeated requests in quoted threads. This separate query has no sender, subject or correspondence restriction. Additional candidates appear first with `relation="outcome_query"`; thread matches retain newest-first ordering and `relation="subject_participants"`. Matches are potential evidence: verify identity, chronology and authored text before treating them as confirmation. The response reports the bounded outcome scope. Query echoes are limited to 200 characters and mark truncation; continuations retain the original full query. Long keyword passages are clipped around a match. A deep same-thread match is shown alongside an authored-prefix excerpt. Omitting this option preserves thread-only behavior. The shared scan deadline still applies.

The default response budget is 16,000 bytes, configurable from 4,000–32,000. Responses preserve body excerpts, including quoted history, mark clipping and skipped attachments, and retain citations. They do not classify quoted text as new statements. Read the original when exact formatting or a clipped body matters. Candidate scans are limited to five seconds and 1,000 messages, with bounded snapshot memory; limits return actionable errors instead of claiming completeness. Continuations take the returned cursor alone and recheck active/committed status.

Follow every candidate page before saying no confirmation was found **in this set**. Even then, do not conclude confirmation was never sent or work was never completed. A later reply may resolve an old promise quoted below it. Attribute disputed amounts to the email; distinguish an out-of-pocket payment from an assessed fee and a payoff amount from principal.

For exchanges between people obscured by automated mail, `search_mail(correspondence_only=true)` or `grep_mail(correspondence_only=true)` restricts candidates before ranking to reply/forward subjects or Sent membership. The response discloses this scope because it can miss direct incoming messages. It combines with existing filters and is retained in cursor continuations. Candidate IDs have a 64 MiB limit and a five-second scan deadline; exceeding either requires narrower filters.

All tools reject unsupported arguments rather than silently ignoring a requested filter. Nested batch requests also reject unsupported fields. Use the published schema to correct an error.

For ordinary questions, reserve a focused verification call after finding a candidate. An old missing update does not establish a promise that was never confirmed. When verification is incomplete, report unknown status; when retrieval misses, say no supported example was found in these results rather than claiming none exist.

## Read full text

`read_mail` accepts `message_id` (or an existing citation), `offset` and `length`. Follow `next_offset` until null. Offsets and lengths count Unicode characters, not bytes. `total_chars` describes cached extracted text; extraction flags report skipped or truncated parts. Raw MIME and binary attachments are not returned.

Prefer `read_mail_batch` for inline reading. Start with up to five IDs:

```json
{"message_ids": ["<first ID>", "<second ID>"]}
```

Or specify offsets and lengths:

```json
{"requests": [{"message_id": "<first ID>", "length": 2000}, {"message_id": "<second ID>", "offset": 0}]}
```

Its `messages` are associated with the original input through `request_index`, including on cursor continuations. Each successful item has full metadata, extraction flags and its own continuation offset. When text remains, execute the returned `next_call` with its cursor to read the unfinished messages; finished items are omitted from the next batch. Alternatively, construct `requests` using each `next_offset`. Supply one of `message_ids`, `requests`, or `cursor` per call. The combined response is bounded below 4 KB. It allocates space to messages dynamically and queues messages that would otherwise receive fewer than 100 characters (unless fewer remain). A message whose metadata cannot fit alone returns an explicit `read_mail` fallback. Cite the returned `citation` for extracted facts. Text is never whitespace-normalized, and offsets continue to refer to the original cached Unicode text. Retry individual errors separately; if metadata exceeds the batch budget, use `read_mail`.

## Existing installations

The lexical index is maintained automatically for newly cached text. Upgrading an existing catalog requires a resumable backfill from cached bodies; embeddings and mailbox progress are preserved. Take a paired catalog/Qdrant backup first, pause the indexing daemon, and run with the same configuration as the daemon:

```bash
uv run proton-rag-lexical
```

Resume ingestion and reconnect MCP clients afterward. `index_status` reports `lexical_ready` and `lexical_messages`; exact mode remains unavailable until the backfill verifies and publishes readiness. Interrupted backfills resume on rerun. Consult the installation's [maintenance instructions](operations.md#backups-and-restore-verification) for backup and service commands.

## Measuring a candidate

The independent scanner can be measured without MCP or a model. Use a frozen catalog copy and avoid writing private evidence to this repository:

```bash
.venv/bin/python scripts/benchmark-evidence.py --catalog /private/snapshot.db \
  --pattern 'invoice|total|paid' --repeats 3 --all-pages
```

These are local tool timings. End-to-end acceptance additionally requires fresh Gemini Pro High sessions, identical snapshots/instructions for MCP and direct `rg` plus reads, and transcript audits counting schema/generated-output reads. Record final-answer completion, every tool call, payload bytes, supported facts/citations and remaining coverage. Empty timeout responses are failures. See [candidate validation](mail-evidence-validation.md) for the current results and pending gates.
