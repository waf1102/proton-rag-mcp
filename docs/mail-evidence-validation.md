# Mail evidence candidate validation

Status: implemented on `codex/mail-evidence`; live acceptance has failed the timing and call gates. The candidate has not been merged or deployed. No end-to-end Gemini speedup is established.

## Implemented behavior

`grep_mail` scans active, committed cached text independently of MCP registration. It returns supported passages, stable citations, compact metadata, extraction warnings and explicit coverage in a default 16,000-byte response (configurable 4,000–32,000). It merges overlapping nonblank context, compacts only display whitespace and paginates long evidence with opaque cursor-only continuations. It rechecks active/committed status and refreshes active citations after folder changes. Subject/sender matches precede incidental body mentions; all matches remain pageable.

Regex execution shares a 100 ms budget across body and header matching per message. Scans have a 10 s deadline, bounded body/line-map/snapshot memory, and actionable errors without completeness claims. Snapshot cache limits are 16 sessions and 64 MiB estimated memory, with ten-minute expiry.

Batch reads allocate the 3,900-byte budget dynamically, queue requests that do not fit usefully, retain original indices/Unicode offsets, and distinguish standalone metadata overflow from a crowded page. Server instructions default to fast cited findings with coverage disclosure, targeted follow-up and explicit exhaustive enumeration.

## Verified locally

- The corrected five-message fixture updates canonical metadata in `messages`. Its original metadata-budget error was observed before the fix.
- Evidence regressions cover deep facts, Unicode, blank HTML layout, repeated/conflicting passages, cross-year bookings, standby/confirmed language, unsupported attachments, receipt/technical emails, filters/citation IDs, malformed/expensive patterns, time/memory limits, cursor expiry and deletion/commit changes between pages.
- Review findings for stale citations, active-copy ID resolution, unrelated-error allocation overhead and the final header-regex deadline were reproduced with failing tests and repaired.
- Independent review found ordering preserves every message and passage order. Its safe 4 KB Unicode metadata/chunk capacity limitation is disclosed by the tool's explicit error.
- After execution access was restored, the complete suite passed 168 tests in 18.67 seconds against an isolated Qdrant fixture service, with no skips or exclusions. Both Python and TypeScript SDKs passed against the candidate with the frozen real mailbox. The regular TypeScript fixture check also passed. Ruff lint/format and git whitespace checks pass.
- Live SDK testing exposed an invalid assumption: FTS previews can contain generated ellipses absent from cached message text. Both SDK checks now derive their literal regex from `read_mail` original text. This repairs the validation scripts without changing search or extraction semantics.
- SHA-256 checks confirm all 22,353 direct-search files exactly match the active, committed original text in the frozen MCP catalog; no files are missing or extra.

A frozen copy of the existing catalog was used for local mailbox checks, without reindexing or changing the deployed release. The original real five-message batch starts with 2,000 returned characters, reconstructs all five original cached bodies in 27 pages with stable request indices, and never exceeds 3,881 bytes per page.

Using one combined flight/boarding/itinerary/airline/identifier regex and header-date bounds covering December of the prior year through year end, all pages contain the previously recorded 2/2/18 flight numbers for 2016/2017/2018. The 18 include the alternate 2018 itinerary number. First-page evidence contains only 1/2/3 of those numbers respectively. This is a numeric evidence-presence audit against existing findings, not confirmation of travel, event-year completeness or a Gemini answer evaluation. Dates, conflicting itineraries and standby status still require contextual interpretation. Disclose these coverage losses in fast answers.

Measured local tool times and exact response counts are retained in private validation artifacts; `scripts/benchmark-evidence.py` reproduces tool-only timings against a read-only catalog snapshot. These measurements exclude model, transport, schema discovery and generated-output reads.

## Gemini comparison and rollout gate

The earlier execution sandbox denied Gemini localhost sockets and the user service bus; full-suite/SDK attempts stalled or timed out. These restrictions were removed during this session. Gemini now lists `gemini-3.1-pro-high`, the existing email service is active, and all previously stalled checks pass. The implementation has still not been merged or deployed.

A valid fresh 2017 fast-answer pilot timed out at 90 seconds with an empty answer after 11 tool calls. Gemini read unnecessary schemas and started with an unfiltered regex. Based on those actual calls, the instructions now explicitly request relevant filters and the default byte budget in the first `grep_mail` call, and remove the contradictory preliminary `index_status` instruction. Independent review found no Critical or Important issue in these follow-up changes.

The controlled comparison uses three fresh sessions per year/method with `gemini-3.1-pro-high`, the same frozen mailbox and equivalent concise fast-answer instructions. Methods alternate ordering between rounds. MCP uses a temporary candidate server with private payload/timing recording; direct search uses `rg` plus reads of original cached extracted text. Both receive the same question and answer-depth requirements. Each session has a 90-second client cutoff and a 110-second external process bound.

Every started tool call counts, including schema and generated-output reads. A streamed partial answer does not count as complete. Final-answer timing uses the final response log timestamp minus the prompt timestamp, including client startup; it is an approximation, separately retained from total process wall time. Censored timeout wall times are observed runtimes, not uncapped time-to-answer estimates. Provider/client step durations are reported measurements and can be lower bounds when work remains active.

MCP payload recording retains both UTF-8 JSON text bytes and compact result bytes, excluding the JSON-RPC envelope. Structured results duplicate the text payload; the configured byte budget applies to the JSON text, not the entire transport envelope. Direct payload counts are observable CLI output lower bounds because file-read summaries omit body contents. Automated known-number counts are token-presence checks. Flight facts, event dates, booking status and claim-specific citation support are audited manually for each completed answer.

| Method/year | Completed answers / 3 | Answer seconds, attempts 1 / 2 / 3 | Total calls, attempts 1 / 2 / 3 | Verified known numbers in completed answers |
| --- | --- | --- | --- | --- |
| MCP 2016 | 1 | timeout / 91.1 / timeout | 12 / 8 / 7 | 1 of 2 |
| MCP 2017 | 3 | 51.6 / 85.1 / 80.0 | 6 / 4 / 8 | 2 of 2 in each |
| MCP 2018 | 0 | timeout / timeout / partial timeout | 7 / 9 / 12 | No completed cited answer; 18 known |
| Direct 2016 | 0 | timeout / timeout / timeout | 9 / 8 / 9 | No completed answer |
| Direct 2017 | 0 | timeout / timeout / timeout | 9 / 10 / 16 | No completed answer |
| Direct 2018 | 0 | timeout / timeout / timeout | 5 / 9 / 18 | No completed answer |

Timeouts occur at the 90-second client cutoff; observed process wall time ranges from 91.9 to 93.3 seconds, including startup/exit overhead. All nine direct trials return empty responses. A successful direct-search answer-time median is therefore unavailable; these censored failures cannot establish the required speedup. MCP has a higher observed completion rate but still fails its own time and call limits. Three of nine MCP trials exceed eight total calls.

| Measurement per controlled comparison session | MCP trials | Direct trials |
| --- | --- | --- |
| Total time inside MCP | 0.40–0.86 s | Not used |
| Reported model step time | 48.3–87.6 s | 69.4–86.3 s |
| Reported client tool step time | 0.59–1.97 s | 0.78–4.30 s |
| Total MCP JSON text bytes | 13,854–36,013 | Not used |
| Total compact MCP result bytes | 28,136–74,583 | Not used |
| Observable direct tool-output bytes | Not comparable | 23,956–65,810; lower bounds |

These payload totals include all tool responses in a session, not only its first evidence page. Most elapsed time is outside MCP; reported model/client times do not fully account for unfinished generation or provider/client overhead. Some later runs used fewer calls than earlier pilots; the end-to-end latency requirement remains unmet.

The final MCP comparison returned four completed answers across nine trials: one 2016 answer and all three 2017 answers. The other 2016 trials returned empty timeout responses. No 2018 trial completed; its third trial streamed a fragment with a truncated citation and an active generation step at the cutoff, which is a failure.

Manual checks found the completed 2016 answer supports one of two previously known numbers, with the missing number retained as a coverage loss. Each completed 2017 answer supports both previously known numbers, their dates/routes and the associated citation. All four distinguish scheduled bookings from proof of completed travel and accurately disclose displayed matches and header-date limits. The 2017 median completed-answer time is approximately 80.0 seconds. The completed 2016 answer takes approximately 91.1 seconds. These fail the 30-second median and 60-second completion limits.

The exact original ordinary prompts are `in my email find flights from 2016`, `give me flight numbers for flights in 2017` and `give me flight numbers for flights in 2018`. They are tested without fast-answer coaching, apart from identifying the isolated mailbox server. Additional ordinary wording checks use the comparison question without coaching.

| Ordinary prompt year | Exact original result | Exact original calls | Additional wording result / calls |
| --- | --- | --- | --- |
| 2016 | Empty timeout | 11 | Empty timeout / 16 |
| 2017 | Completed in 65.5 s | 4 | Empty timeout / 14 |
| 2018 | Completed in 83.9 s | 7 | Empty timeout / 6 |

The exact 2017 answer supports both known numbers, with valid citation identifiers and accurate booking/coverage caveats. The exact 2018 answer mentions four of the existing 18 known numbers plus one additional supported standby listing. Its coverage caveat correctly reports five displayed messages out of 79 matches, but Gemini prepends `file:///` to the returned `imap:///` citation links. Two event dates are absent from the viewed passages; their original messages confirm 2018. These limitations are retained in the manual audit. Neither completed ordinary answer meets the 60-second ceiling, and the others fail to deliver an answer.

Earlier tuning trials remain retained separately from this fixed comparison, including a valid 2017 empty timeout at 90 seconds/11 calls, a wider-lookback 2017 completion at approximately 79.5 seconds/13 calls, and failed 2016/2018 trials. A 2016 partial pilot incorrectly called a reservation confirmed travel; the final instruction explicitly addresses this. The excluded wrapper setup failure occurred before valid tool execution. No further fast-search settings changed during the final comparison; the exhaustive-search clarification preserves its fast-search window.

All 24 final sessions and private payload/manual audit records are retained for review. Final fresh verification passes 168 tests with no skips, both real-mailbox SDKs, lint/format and whitespace checks. Both the frozen source catalog and candidate runtime still hash-match all 22,353 original-text baseline files. The temporary Gemini server/schema cache and isolated Qdrant test container have been removed; the deployed release remains unchanged and its service is active.

Require median answer completion ≤30 seconds, no completed run >60 seconds, and ≤8 total calls per prompt, plus a demonstrated win against equivalent direct search. Empty timeout answers fail. The historical 180-second/31-call direct-search trial is censored and cannot serve as a successful baseline.

The completed comparison samples fail the timing criteria, so merge and deployment are withheld under the plan's acceptance gate. The existing rollout authorization remains valid if those gates are later satisfied. No mailbox migration, reindexing or new cloud generation service is required.
