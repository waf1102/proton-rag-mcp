# Mail evidence candidate validation

Status: implemented on `codex/mail-evidence`; live acceptance is pending. The candidate has not been merged or deployed. No end-to-end Gemini speedup is established.

## Implemented behavior

`grep_mail` scans active, committed cached text independently of MCP registration. It returns supported passages, stable citations, compact metadata, extraction warnings and explicit coverage in a default 16,000-byte response (configurable 4,000–32,000). It merges overlapping nonblank context, compacts only display whitespace and paginates long evidence with opaque cursor-only continuations. It rechecks active/committed status and refreshes active citations after folder changes. Subject/sender matches precede incidental body mentions; all matches remain pageable.

Regex execution shares a 100 ms budget across body and header matching per message. Scans have a 10 s deadline, bounded body/line-map/snapshot memory, and actionable errors without completeness claims. Snapshot cache limits are 16 sessions and 64 MiB estimated memory, with ten-minute expiry.

Batch reads allocate the 3,900-byte budget dynamically, queue requests that do not fit usefully, retain original indices/Unicode offsets, and distinguish standalone metadata overflow from a crowded page. Server instructions default to fast cited findings with coverage disclosure, targeted follow-up and explicit exhaustive enumeration.

## Verified locally

- The corrected five-message fixture updates canonical metadata in `messages`. Its original metadata-budget error was observed before the fix.
- Evidence regressions cover deep facts, Unicode, blank HTML layout, repeated/conflicting passages, cross-year bookings, standby/confirmed language, unsupported attachments, receipt/technical emails, filters/citation IDs, malformed/expensive patterns, time/memory limits, cursor expiry and deletion/commit changes between pages.
- Review findings for stale citations, active-copy ID resolution, unrelated-error allocation overhead and the final header-regex deadline were reproduced with failing tests and repaired.
- Independent review found ordering preserves every message and passage order. Its safe 4 KB Unicode metadata/chunk capacity limitation is disclosed by the tool's explicit error.
- The final available suite passed 158 tests, with 5 optional Qdrant-service tests skipped and 5 runtime-stalled checks excluded. Ruff lint/format, JS syntax and git whitespace checks pass.

A frozen copy of the existing catalog was used for local mailbox checks, without reindexing or changing the deployed release. The original real five-message batch starts with 2,000 useful characters, reconstructs all five original cached bodies in 27 pages with stable request indices, and never exceeds 3,881 bytes per page.

Using one combined flight/boarding/itinerary/airline/identifier regex and header-date bounds covering December of the prior year through year end, all pages contain the previously recorded 2/2/18 flight numbers for 2016/2017/2018. The 18 include the alternate 2018 itinerary number. First-page evidence contains only 1/2/3 of those numbers respectively. This is a numeric evidence-presence audit against existing findings, not confirmation of travel, event-year completeness or a Gemini answer evaluation. Dates, conflicting itineraries and standby status still require contextual interpretation. Disclose these coverage losses in fast answers.

Measured local tool times and exact response counts are retained in private validation artifacts; `scripts/benchmark-evidence.py` reproduces tool-only timings against a read-only catalog snapshot. These measurements exclude model, transport, schema discovery and generated-output reads.

## Pending acceptance and rollout gates

The current execution sandbox blocks Gemini startup with `listen tcp 127.0.0.1:0: socket: operation not permitted`, and blocks the user service bus. Full-suite and Python/TypeScript stdio SDK attempts timed out (exit 124). The five isolated stalled tests are:

- `test_query_uses_query_prefix_without_document_prefix`
- `test_hybrid_filter_applies_to_both_prefetches_before_ranking`
- `test_uncommitted_or_orphan_hits_are_hidden`
- `test_python_sdk_client`
- `test_raw_protocol_harness_negotiation_errors_cancellation`

These checks use async workers or subprocess clients. Attempts stall without completion; the exact runtime cause has not been established, and these checks are not reported as passed. Real Qdrant checks also require their optional fixture service.

Before merge/deploy, run the complete suite/lint and both SDK checks in a working runtime; then compare three fresh sessions per year/method with `gemini-3.1-pro-high`, the same frozen mailbox and equivalent fast-answer instructions. Compare MCP with direct `rg` plus reads of original cached extracted text. Audit final-answer completion time, every call (including schema/output-file reads), payloads, model/client time, supported facts/citations and coverage. Also test ordinary uncoached prompts.

Require median answer completion ≤30 seconds, no completed run >60 seconds,≤8 total calls per prompt and a demonstrated win against the equivalent direct-search baseline. Empty timeout answers fail. The historical 180-second/31-call direct-search trial is censored and cannot serve as a successful baseline.

After these gates pass, merge/deploy under existing authorization, retain the previous release, and verify content fingerprints, service health and fresh-client behavior. No mailbox migration, reindexing or new cloud generation service is required.
