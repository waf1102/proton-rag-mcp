# Faster, complete Proton Mail retrieval

## Summary
Preserve Qdrant vectors, cached text, stable message IDs and citations. Remove repeated catalog scans, make tool results compact, and add exact SQLite FTS5 retrieval with filters, exhaustive pagination and batch reading.

## Tasks
1. Replace catalog scans with targeted hydration and batched manifests; aggregate coverage in SQL with normalized header dates. Respect requested hybrid limits and retain bounded overfetch. Target warm search median <1s and status <200ms.
2. Make search responses compact: subject/sender/date, stable ID/citation, folder names, match-centered preview <=300 characters. Bound serialized MCP text to 8KiB and paginate omitted selected hits. Preserve full internal excerpts for answer_mail and existing read_mail pagination. Add read_mail_batch (<=5 requests, default 2000 characters, 8KiB total, accurate Unicode offsets, individual errors).
3. Extend search_mail with mode=hybrid|exact, sent_after inclusive/sent_before exclusive UTC header-date bounds, sender/subject case-insensitive substrings, exact folder membership, cursor. Exact FTS5 supports terms, phrases and Boolean expressions, and empty query with filters. Maintain one lexical record per canonical cached message transactionally. Only active committed messages qualify. Hybrid filters apply before Qdrant ranking through indexed message_id. Return next_cursor/has_more/match_count (exact only)/explicit exhaustiveness. Snapshot IDs, opaque query-bound cursors: 10 minutes, 16 sessions, 64MiB ID payload; clear errors on expiry/eviction/overflow/parameter changes. Revalidate activity each page; event dates differ from header dates.
4. Test rank-42 AA3366 pagination, >50 exact matches, duplicates, missing dates/timezones, Unicode, malformed queries, expiry/deletion, numeric token precision, interrupted backfill and subsequent ingestion/deletion. Preserve old IDs/citations, commit validation and local-only behavior. Update tool guidance and README. Run complete pytest, Ruff and Python/JS MCP interop; replay recorded queries and verify answer completeness.
5. Isolated worktree; independent review before merge. Paired catalog/Qdrant backup; pause ingestion for schema and resumable cached-text lexical backfill; verify readiness before publishing. No redownload/re-embedding/vector rebuild. Merge, deploy new release, restart ingestion and reconnect clients. Verify coverage, latency and Gemini regression. Keep previous release and paired backup for rollback.

## Defaults and boundaries
Full retrieval improvements, compact defaults. No flight-specific parser, OCR, cloud service or hardware change. Exhaustive describes the indexed predicate, never every natural-language interpretation.
