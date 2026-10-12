# Enforce the client limit; reject the compact-page experiment

The released change is a maintained, pinned AnythingLLM client patch. No MCP
retrieval code, instructions, indexed text, embeddings or mailbox state changes.
AnythingLLM retains its existing ten-attempt allowance. The exact dispatch
boundary now works even when a provider keeps returning tool calls after tool
definitions disappear. Final synthesis retains evidence, requests complete
citation links and coverage limits, and falls back to an explicit inconclusive
answer for another tool request, empty text or reasoning without an answer.

The original installed 1.17.0 client executed four tools with a configured limit
of one. The original regression run failed 12 of 20 checks. Additional regressions
exercised malformed native final-only calls, reasoning-only completions with null
or missing content, and a shared response UUID for fallback text, usage, citations
and chat ID. Independent review found the latter two gaps; both were reproduced
before fixing them. All 26 final client regressions pass in the derived image.

## Ordinary-prompt comparisons

These use the same trip-postponement, unconfirmed-promises and administrative
financial-penalty questions as the user conversations. All sessions are fresh,
using the frozen read-only 22,354-message catalog and its original cached extracted
text, with the same local retrieval services. Private transcripts, mail identifiers,
credentials and wire logs remain outside Git. These are single paired samples,
not reliable medians or a controlled comparison between different clients.

AnythingLLM uses the installed MCP adapter, native OpenRouter provider and real
agent loop, `google/gemini-3.8-flash` with default reasoning. Its harness excludes
GUI latency, persistence and telemetry, and uses text-only MCP results in both
variants. The final model trials are serialized with a 90-second answer ceiling.
Counts below are model-requested mail tools; there are no model-requested schema
or generated-output reads. Automatic connection initialization/schema discovery
is separate. The final candidate uses the retained ten-attempt budget; none of
these three comparisons reached it. Timing differences therefore do not prove a
speed improvement caused by enforcing the boundary.

| AnythingLLM case | Original answer time / mail calls | Final patched answer time / mail calls |
| --- | ---: | ---: |
| Trip | 18.8 s / 2 | 14.5 s / 3 |
| Promises | 24.1 s / 3 | 20.7 s / 4 |
| Financial | 23.5 s / 6 | 29.1 s / 9 |

Quality remains variable. The original trip answer recovered the known postponed
processing travel exchange; the final patched sample missed it. Its three calls
never reached the ten-call boundary, and the patch does not change the requests
in that path. The final promises answer checked a temporary-plates promise and
found a later receipt acknowledgment, but included weaker examples without
adequate outcome verification. The final financial answer recovered the reported
extra-payment dispute and later refund, yet overstated the causal/arithmetic
interpretation and used complete citation strings without clickable Markdown
links. These are not counted as uniformly successful accurate, cited answers.
The patch makes dispatch bounded; it does not make synthesis trustworthy.

A separate four-attempt pilot completed in 14.4/21.6/19.2 seconds with 4/3/4 mail
calls. It produced an inconclusive financial answer without recovering the known
reimbursement case; the promises answer also lacked verification. That smaller
budget is not deployed. A content audit overlapped the financial pilot, so its
timing is diagnostic only. The final ten-attempt comparisons ran after all other
benchmarks, tests and builds completed.

AGY uses its actual CLI and `gemini-3.8-flash-medium`, medium effort, with a
120-second print deadline. Its ordinary prompts add only retrieval isolation
instructions, not benchmark coaching. The private compact candidate recommends
4 KB combined-search/related pages and 2,000-character single reads, preserves
explicit larger requests, original text, stable citations and cursor coverage.
It is not shipped. AGY frequently explicitly requested 8–16 KB anyway. Bounded
default tests passed, but actual model behavior failed the intended improvement.

| AGY case | Original wall / mail / all tools | Compact wall / mail / all tools |
| --- | ---: | ---: |
| Trip | 69.5 s / 4 / 13 | 122.3 s, no final answer / 11 / 22 |
| Promises | 46.0 s / 2 / 6 | 85.0 s / 3 / 10 |
| Financial | 122.6 s, no final answer / 8 / 20 | 122.1 s, no final answer / 17 / 30 |

All-tool counts include schema, instruction, generated-output and directory reads.
An empty timeout is a failure even when the CLI exits zero. Baseline promises
briefly overlapped the tail of a separate text-only trip trial; small offline
review-regression runs overlapped compact financial. These AGY timings are
diagnostic, not strict latency estimates. No full suites/builds ran during them.
Both promise answers failed to establish a supported never-confirmed commitment.
The original trip answer included the known rescheduling exchange while opening
with a contradictory absence claim; compact trip produced no final answer.

The separate text-only trip experiment took 107.7 seconds, seven mail tools and
18 total tools, missed the known exchange and still read generated-output files.
That format change is also not deployed to AGY. AGY's original MCP configuration
was restored exactly after removing only the owned test entry. User sessions
were not stopped. OMP retains the same MCP release behavior; no OMP inference
comparison is claimed for this client-only patch.

## Verification and limits

The full suite passes: 219 Python tests against a pinned isolated Qdrant instance;
Ruff, formatting and whitespace checks; TypeScript MCP SDK interoperability in
both text-only and structured-plus-text modes; and 26 real client-loop/native
formatter regressions in the pinned derived image. The build rejects upstream
source drift before modifying either file. The two drift checks pass and their
guard mutation fails. See [build and rollback instructions](../integrations/anythingllm/README.md).

Review decisions, in order:

1. Semantic correctness/exhaustive coverage is outside the deterministic client
   guarantee. Cost: a model can still overclaim despite evidence and guidance.
2. Explicit constructor budgets must be positive; inherited invalid-value
   handling stays unchanged. Cost: unsupported caller input can evade a limit.
3. Provider JSON-repair retries before exhaustion stay unchanged and do not
   dispatch tools. Cost: inference can still take extra time.
4. The limit covers each execution chain, as used by this single-agent deployment.
   Cost: a future group conversation needs a separate aggregate budget.
5. `tool_choice: none` is verified with OpenRouter/Gemini, not every native backend.
   Cost: another provider may reject it.
6. Transport exceptions/malformed response envelopes retain surfaced upstream
   error handling. Cost: provider failures do not guarantee a synthesized answer.
7. Abort/direct-output ordering stays unchanged. Cost: inherited combined-case
   behavior persists.
8. Build tags do not deploy automatically; rollout selects a tested immutable
   image ID. Cost: a failed build may leave an unused local image.
9. Deployment/health/content checks and rollback preservation remain required,
   rather than being inferred from review or tests. Cost: no release claim until
   those checks finish.
10. The AGY experiments remain private and unshipped. Cost: its search/file-read
    overhead remains.

No deferred minor review findings. The original Gemini Pro High 2016–2018 flight
acceptance, universal eight-call limit and superiority over direct grep remain
unproven. The earlier three-minute direct-search timeout remains a censored
failure. This work establishes an enforceable client dispatch limit, not a
blanket speed, recall or answer-quality improvement.
