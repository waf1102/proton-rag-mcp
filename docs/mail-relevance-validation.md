# Combined evidence and outcome verification

This change rejects unsupported MCP arguments, adds the correspondence scope to `grep_mail`, and adds optional keyword passages to hybrid searches and mailbox-wide outcome candidates to related-message checks. Outcome slots exclude already-included thread IDs, so repeated thread matches cannot crowd out a separate confirmation. Both outcome scans share the original five-second deadline. Existing calls keep their ranking and response defaults; searches opting into `lexical_query` default to 16 KB. Explicit budgets and original full queries remain bound to cursor continuations.

Automatic natural-language ranking blends were tested against 36 existing labeled cases and discarded: equal-weight fusion lost an existing recovery without adding coverage, while heavier semantic weighting preserved coverage without improving it. The shipped keyword query is explicit, not an automatic synonym parser or a claim of improved ranking across all queries.

## Method

Use the ordinary trip-postponement, unconfirmed-promises and financial-penalty prompts from the previous client evaluation. The baseline is current main/PR14, `a89f642`, and the candidate uses the same frozen, read-only 22,354-message catalog, original cached extracted text and shared local retrieval services. No embeddings, indexed content or mailbox migration change. This compares the combined new features and instructions, not an isolated causal test of one field.

AnythingLLM uses its installed 1.17.0 MCP adapter, OpenRouter provider and agent loop, `google/gemini-3.8-flash`, default thinking, an ordinary workspace system prompt and fresh context. The private harness omits persistence, telemetry and GUI latency. Both releases use text-only MCP results. OMP uses its actual 18.9.1 CLI, `openai-codex/gpt-6.1-sol`, medium thinking, fresh sessions and isolated configuration, with neutral mail-tool-only instructions. Its total calls include schema/output reads. Both harness variants omit the optional paid generation tool; the retrieval tools remain unchanged except for the feature under test.

Final client runs are sequential, with a 90-second answer ceiling; empty timeouts are failures. Server logs record all MCP calls, arguments, duration and serialized response bytes, including errors and calls nested in scripts. Client session/provider records measure answer completion and usage. Provider time includes request/client processing; it is not pure inference. The final comparison contains one baseline and one final candidate run per prompt/client, so it does not establish reliable medians. A content-hash audit overlapped one intermediate financial candidate run; final candidate repeats were serialized after it completed. Earlier pilots and comparisons before the combined-search default was enlarged and before thread/outcome allocation was separated are retained privately and summarized below.

| Client / release | Prompt | Process wall | Answer completion | Total / MCP calls | MCP time | Provider/client time | MCP bytes | Citation links |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| anything / baseline | trip | 29.7 s | 27.6 s | 4 / 4 | 11.0 s | 16.6 s | 59,977 | 6 |
| anything / candidate | trip | 21.5 s | 18.8 s | 3 / 3 | 6.5 s | 12.2 s | 19,749 | 1 |
| anything / baseline | promises | 20.6 s | 18.9 s | 4 / 4 | 6.4 s | 12.5 s | 63,439 | 2 |
| anything / candidate | promises | 28.1 s | 25.6 s | 6 / 6 | 3.3 s | 22.3 s | 88,559 | 3 |
| anything / baseline | financial | 92.6 s | 90 s timeout — failure | 28 / 28 | 24.1 s | 65.6 s | 150,936 | 0 |
| anything / candidate | financial | 18.6 s | 16.8 s | 4 / 4 | 1.9 s | 15.0 s | 58,705 | 3 |
| omp / baseline | trip | 21.2 s | 19.7 s | 4 / 3 | 1.6 s | 17.9 s | 74,739 | 0 |
| omp / candidate | trip | 18.3 s | 17.1 s | 4 / 2 | 0.8 s | 16.2 s | 28,452 | 1 |
| omp / baseline | promises | 29.3 s | 27.8 s | 5 / 3 | 2.3 s | 25.4 s | 110,238 | 2 |
| omp / candidate | promises | 31.5 s | 30.2 s | 5 / 3 | 1.1 s | 28.9 s | 60,338 | 2 |
| omp / baseline | financial | 34.3 s | 32.9 s | 6 / 4 | 2.9 s | 29.8 s | 76,861 | 3 |
| omp / candidate | financial | 38.3 s | 37.0 s | 7 / 4 | 1.3 s | 35.6 s | 106,623 | 2 |

Completion time did not improve in every case: final OMP financial recovery used more model time for a fuller answer, and both promise runs were slower than their baselines. These results support improved evidence extraction and targeted verification, not a blanket speed or answer-quality claim.

## Evidence quality

Both final candidates recovered the known postponed-processing trip conversation with correct citations and no invented replacement date. Both baseline trip runs missed it; the baseline OMP answer also omitted citation links. Final OMP promises checked two candidates with mailbox-wide outcome queries, excluded one with an explicit refund confirmation, and qualified the remaining conditional promise as unknown rather than never completed. Final OMP financial recovered the reported extra-payment dispute and the later bank refund under another sender/subject, distinguished financial burden from an assessed penalty, and attributed causality to the sender's account. The baseline financial OMP run returned an accurate but inconclusive registration example and missed that known reimbursement case.

Final AnythingLLM promises and financial answers remained weaker. The promises answer did not recover a clearly unresolved example and overstated the overall closure of commitments. The financial answer found an assessment-payment receipt but generalized absence of penalties beyond the evidence examined; a receipt without a late-fee line cannot rule out every other charge. All final citation links exactly matched citations returned by MCP, but these inference/coverage failures remain. No search establishes universal absence.

Before the outcome allocation fix, a known bank confirmation ranked eleventh behind duplicated thread evidence in an actual OMP outcome query. Excluding already-included thread IDs moved it to second among additional candidates. The regression and final outcome checks exercise that correction. A previous AnythingLLM candidate run recovered the reimbursement and refund but still overstated the payoff arithmetic and contractual conclusion. Earlier client trials ranged from 2–14 MCP calls; one intermediate financial answer omitted citations. These failures are retained, not discarded as successful faster answers. The final ordinary financial OMP answer used four MCP calls, exceeding the advisory three-call target. One current-release AnythingLLM baseline timed out after 28 calls with no answer; its true completion latency is unknown.

Even valid citation links do not guarantee the associated inference. A payment made to avoid a late mark is not proof of an assessed penalty. Reported payoff amounts must not be reconciled by assumption. A receipt under another sender can resolve an old request; a missing update in a clipped or bounded thread does not prove a never-confirmed promise. Model behavior remains a limitation, particularly in AnythingLLM.

## Engineering verification

All 217 tests pass against an isolated pinned Qdrant instance. Ruff, formatting and whitespace checks pass. Python and JavaScript MCP SDK checks pass against the frozen real mailbox in both text-only and structured-plus-text formats; JavaScript synthetic fixture checks also pass in both formats. Checks cover strict schemas/unknown arguments, deep keyword recovery, different-sender receipt lookup, legacy defaults, continued byte budgets, Unicode, invalid/time-limited queries, deletion and commit-state changes, stable citations and batch continuations. No new dependencies are required. Existing flight, receipt and technical fixtures remain green.

Independent review identified and fixed oversized Unicode query echoes, long-token snippets that lost the matching fact, and same-thread outcome matches hidden by body prefixes. Each has a failing regression before the fix and a passing regression after it. Query echoes are bounded and marked when clipped, while continuations retain full queries. Snippets center around marked matches; deep thread outcomes preserve an authored-prefix excerpt alongside the match. A later review found no blocking concerns, including the optional 16 KB default and preserved legacy/cursor behavior.

Private email content, identifiers, citations, credentials and client transcripts remain outside Git. These cases do not establish the original Gemini Pro High 2016–2018 flight acceptance, the eight-total-call limit in every client, or superiority over direct grep. The previous direct-search three-minute timeout remains a censored failure rather than a completed baseline.
