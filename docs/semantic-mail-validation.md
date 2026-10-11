# Semantic mail evaluation

This candidate improves evidence delivery and follow-up checks. End-to-end Gemini speed is still an open gate; local tool timings do not establish answer completion speed.

## Implemented changes

- Hybrid searches retain the winning indexed passage, rather than replacing it with a keyword window in the original body. Optional `max_bytes` supports 3,900–32,000 bytes while preserving the legacy default. Unicode passages fit the actual response budget and disclose clipping.
- A primary query plus up to two `alternate_queries` run together, sharing filters and interleaving unique messages within the selected limit. This bounds the exploration instead of requiring separate model rounds for each paraphrase.
- Optional `correspondence_only` restricts candidates before ranking to reply/forward subjects or Sent membership. Responses disclose the exclusions. Candidate IDs have memory and scan-time limits.
- `related_mail` discovers newer messages sharing normalized subjects and at least two participant addresses. It returns cited body excerpts, preserves inline replies and quoted history, discloses heuristic coverage, and rechecks active/committed status on continuation. It does not prove a commitment was never completed.
- Instructions route meaning-based questions to semantic search and identifiers to literal search, request focused follow-up, and distinguish a claimed loss from an assessed fee, payoff from principal, and new commitments from quoted old ones.

No index/profile migration, text rewriting, reindexing, or additional generation service is involved.

## Correctness checks

The complete suite passes **184 tests**, including the isolated live Qdrant tests. Ruff lint/format and Python/TypeScript MCP interoperability pass, including independent frozen-mailbox checks of the missed follow-up and grouped hybrid queries. Independent review found no remaining issues after regressions reproduced and repaired inline-reply deletion, HTML quote misclassification, Unicode budget overflow, and unbounded correspondence candidates. The final paraphrase/cursor changes received a second review.

The private frozen-mailbox audit verifies that related-message discovery retrieves the same-day reply omitted from the original conversation. It also verifies that the documented reimbursement claim and the arithmetic difference between payoff and principal are different quantities. Those messages and citations remain private; no email bodies are committed here.

## Evaluation protocol

Use the original ordinary questions:

1. `find email conversations about postponing a trip`
2. `have I ever received emails where people promised to do things for me that I never received confirmation were completed?`
3. `Can you find a time when I was financially penalized because another organization was slow to complete an administrative process`

Fresh sessions use `gemini-3.1-pro-high`, a read-only frozen catalog containing 22,354 active messages, the existing local Qdrant/embedding services, and only neutral instructions selecting the isolated MCP server. No fast-answer coaching is appended to these prompts. The server's workflow instructions provide the default behavior. Count schema and generated-output file reads in total calls. Record answer completion, client/model time, MCP time, response bytes, citations, and coverage. Empty timeout responses fail even when the CLI reports success.

The original conversation completed these questions in approximately 123.2, 265.9, and 407.9 seconds with 26, 65, and 62 calls. It used the previous deployed release. This is an observational reference, not a controlled same-depth baseline or evidence that RAG beats filesystem search.

An initial harness import-name collision prevented MCP initialization. Those empty attempts are excluded as invalid setup trials; after repair, independent SDK initialization and real-mail follow-up verification complete in under one second. Later trials with valid tool calls remain failures if they time out.

## Final candidate results

One fresh ordinary-prompt run per case used the final implementation, default Pro High effort, and a 180-second ceiling. These are measurements, not a three-repeat acceptance sample.

| Case | Answer completion | Total calls | MCP calls | MCP time | Reported model time | Viewed/inline bytes | Serialized MCP result bytes |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Trip | Empty timeout, 182.2 s wall time | 19 | 9 | 10.9 s | 163.5 s | 95,636 | 197,701 |
| Promises | Completed, 154.2 s | 10 | 3 | 5.1 s | 145.2 s | 47,681 | 77,739 |
| Financial | Empty timeout, 182.4 s wall time | 25 | 11 | 8.2 s | 168.7 s | 173,391 | 336,039 |

The CLI ceiling includes small startup/shutdown overhead. Viewed/inline bytes count schema/generated-output reads using client-reported byte counts plus inline JSON; they do not count the brief stream summaries as evidence. Serialized MCP results include text and structured content, so they can exceed the selected text-response budget. Counts include attempted tools visible at the cutoff.

The promises answer identifies a supported additional example and checks all three related candidates. Its citation and main timestamp are valid, and the absence claim is bounded by altered subjects/addresses. Independent audit found two minor presentation errors: the selection was the previous evening in the answer's timezone, and calling the heuristic candidate set a thread overstates its identity. It provides one example rather than recovering all examples in the original conversation. It does not repeat the original universal no-follow-up claim. No final-answer factual accuracy credit is assigned to empty timeouts.

Two earlier valid 90-second pilot variants each timed out on all three questions. After the first variant, instructions requested fewer schema reads and a three-retrieval-call ordinary-answer budget. After the second, grouped paraphrases were added. The final promise run followed that retrieval budget, while trip/financial continued exploratory calls. This is observed model behavior, not an enforced tool limit.

The completed promise sample uses fewer calls and is shorter than the original observed conversation, but different answer coverage and an unpaired baseline prevent attributing a controlled speedup. These samples fail the 30-second median, 60-second completion ceiling, and eight-total-call targets. No win against direct filesystem retrieval is established. More identical repeats would not turn these failures into a demonstrated win, and the original flight benchmark remains a separate open gate.

Production is unchanged. The implementation is suitable for review as a functional evidence improvement; it is not a verified end-to-end speed release. Local semantic ranking still misses relevant messages, and model/client processing dominates elapsed time. Improved passage delivery and routing instructions alone are insufficient to close the speed gate.

## Separate client diagnostic

The available `gemini-3.1-pro-low` alias was also tried on the same three ordinary prompts, without changing any persistent client setting. All three returned empty 90-second timeouts (approximately 92 seconds wall time), using 14, 24, and 22 total calls respectively. This is a different effort/model selection and is excluded from Pro High acceptance. It does not establish an improvement or justify changing the default. A basic Pro High hello request completed normally, so these search failures are not evidence of a completely unavailable model service.

Reported agent-response intervals include provider/client processing and are not a measurement of pure inference time. No claim is made that every second outside MCP is model reasoning.
