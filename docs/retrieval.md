# Finding and reading your mail

Use `search_mail` to discover messages, then `read_mail` or `read_mail_batch` to extract information from full text. Search previews are short; an itinerary's flight numbers or a receipt's totals may be elsewhere in the email.

## Search modes

**Hybrid** (default) combines semantic and lexical ranking. It is useful for questions in plain language. It returns a bounded set of candidates, not every matching message. `limit` selects up to 50 candidates (default 10); compact pages may hold fewer. Follow `next_cursor` to see the rest of that selected set.

**Exact** lists every active, committed message matching the indexed words and filters. It searches subject, sender, and cached extracted body, including supported attachment text. Numeric terms match tokens: `3366` does not match `13366`. It uses [SQLite FTS5 syntax](https://www.sqlite.org/fts5.html#full_text_query_syntax):

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

Responses include `sources`, a small coverage summary, `exhaustive`, `has_more`, and `next_cursor`. `match_count` appears only in exact mode. Repeat the same query, mode, limit and filters with the returned cursor until `has_more` is false. Cursors expire after ten minutes, a server restart, or eviction from the bounded cache; on an explicit cursor error, start the query again. Deletions between pages are hidden; the original match count may then exceed the number returned. Newly arriving messages require a new search.

Every search page fits within 8 KiB of serialized result text. Sources include stable `message_id`, citation, subject/sender/date, folder names and a preview up to 300 characters. Full metadata is available from `read_mail`. An unusually large folder/citation payload produces an explicit budget error instead of silently dropping the message.

## Read full text

`read_mail` accepts `message_id` (or an existing citation), `offset` and `length`. Follow `next_offset` until null. Offsets and lengths count Unicode characters, not bytes. `total_chars` describes cached extracted text; extraction flags report skipped or truncated parts. Raw MIME and binary attachments are not returned.

Use `read_mail_batch` for up to five pages:

```json
{"requests": [{"message_id": "<first ID>", "length": 2000}, {"message_id": "<second ID>", "offset": 0}]}
```

Its `messages` are associated with the input through `request_index`. Each successful item has full metadata, extraction flags and its own continuation offset. The combined response is bounded to 8 KiB, so actual pages may be shorter. Retry individual errors separately; if metadata exceeds the batch budget, use `read_mail`.

## Existing installations

The lexical index is maintained automatically for newly cached text. Upgrading an existing catalog requires a resumable backfill from cached bodies; embeddings and mailbox progress are preserved. Take a paired catalog/Qdrant backup first, pause the indexing daemon, and run with the same configuration as the daemon:

```bash
uv run proton-rag-lexical
```

Resume ingestion and reconnect MCP clients afterward. `index_status` reports `lexical_ready` and `lexical_messages`; exact mode remains unavailable until the backfill verifies and publishes readiness. Interrupted backfills resume on rerun. Consult the installation's [maintenance instructions](operations.md#backups-and-restore-verification) for backup and service commands.
