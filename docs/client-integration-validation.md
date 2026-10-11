# Mail MCP client integration validation

This release fixes duplicated evidence for clients that serialize whole MCP results and puts the essential workflow guidance in tool descriptions. It also brings the previously merged retrieval changes to the shared local launcher. It does **not** establish reliable fast or accurate answers in every client.

## Compatibility and instructions

`RAG_MCP_RESULT_FORMAT=both` remains the default: retrieval tools include JSON text and structured content. `text` omits only the structured copy. The JSON still includes evidence, citations, coverage, errors and continuation arguments. The optional paid `answer_mail` tool retains its existing dict/text return shape. Invalid formats fail with an actionable configuration error.

The installed AnythingLLM adapter forwards tool descriptions, omits initialize instructions, and serializes the whole `CallToolResult` as model context. Configure `RAG_MCP_RESULT_FORMAT=text` in its proton-mail server entry; do not change other clients to text if they consume structured content. No third-party application code patch or automatic client-name detection is needed. Fresh clients must reconnect to load code, schemas and environment changes.

Descriptions now recommend short natural-language hybrid queries with alternate wording, useful evidence budgets, a bounded cited answer, and focused outcome verification. A later receipt can come from a different sender/subject. Absence is limited to examined evidence; reported costs, proposed fees and assessed penalties are distinct. Continuation notices no longer encourage enumeration for an ordinary question. Exhaustive requests still require reading remaining pages. The three-retrieval-call instruction is advisory; it is not enforced as a session quota.

## Evaluation method

Use the three ordinary trip-postponement, unconfirmed-promises and financial-penalty questions recorded in [semantic mail validation](semantic-mail-validation.md). Each prompt has a fresh context and a 90-second answer ceiling. Empty timeout responses count as failures.

The baseline is the release actually behind the local launcher (PR10, `cb60c18`); the candidate includes PR11–13 plus this integration pass. Thus this is a **combined release comparison**, not an isolated causal test of instruction wording, ranking, or response format. Both use the same frozen, read-only 22,354-message catalog and original cached extracted text, shared local embedding/index services, and dependencies. The live index contains one additional message excluded by the snapshot. No reindexing or mailbox changes occur.

AnythingLLM uses its installed 1.17.0 MCP adapter, OpenRouter provider and agent execution loop with `google/gemini-3.8-flash`, default thinking and the ordinary workspace system prompt. A private harness substitutes a fresh context and inert progress handlers; it does not persist chats or exercise GUI/history/storage latency. Baseline uses the default response format, candidate uses text-only.

OMP uses the actual 18.9.1 CLI, `openai-codex/gpt-6.1-sol`, medium thinking, isolated copies of its client configuration, and a neutral instruction to use the mail MCP rather than backend files. Both variants retain ordinary client tools and schema access. Model names here record actual client configuration, not availability claims.

MCP calls are counted at the server, including calls nested in executed scripts and failed calls. Total calls include client schema/output reads and orchestration; direct MCP wrappers count once. Bytes measure serialized MCP responses, not token counts or all model input. Server time is cumulative; provider/client time is separately observed and includes orchestration and request processing, not pure inference. Some pilot trials and verification overlapped on the same machine, so exact latency differences need a larger, serialized benchmark.

There is one paired run per prompt/client in the final comparison, not three repeats of each case. Initial pilots informed stronger stopping/continuation wording; those pilots are recorded separately below. A minor batch-pagination description clarification was made after the AnythingLLM final runs. OMP final trip/promises include it; the tested transport behavior is unchanged. These samples cannot establish reliable medians or the original flight acceptance criteria.

| Client / release | Prompt | Process wall | Answer phase | Total / MCP calls | MCP time | Provider time | MCP bytes | Final |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| anything / baseline | trip | 56.7 s | 55.0 s | 24 / 24 | 3.3 s | 51.6 s | 226,638 | answer |
| anything / candidate | trip | 31.2 s | 29.0 s | 9 / 9 | 7.6 s | 21.4 s | 79,070 | answer |
| anything / baseline | promises | 64.1 s | 62.1 s | 20 / 20 | 26.5 s | 35.5 s | 148,368 | answer |
| anything / candidate | promises | 28.7 s | 25.8 s | 5 / 5 | 13.1 s | 12.7 s | 50,881 | answer |
| anything / baseline | financial | 92.4 s | timeout | 39 / 39 | 6.5 s | 75.4 s | 270,825 | none — failure |
| anything / candidate | financial | 92.4 s | timeout | 15 / 15 | 53.3 s | 32.6 s | 197,781 | none — failure |
| omp / baseline | trip | 90.6 s | timeout | 124 / 112 | 11.9 s | 70.4 s | 813,056 | none — failure |
| omp / candidate | trip | 26.2 s | 24.7 s | 4 / 3 | 3.3 s | 21.3 s | 66,605 | answer |
| omp / baseline | promises | 90.5 s | timeout | 160 / 151 | 22.8 s | 60.8 s | 1,135,855 | none — failure |
| omp / candidate | promises | 34.2 s | 31.7 s | 5 / 3 | 4.4 s | 27.2 s | 86,065 | answer |
| omp / baseline | financial | 90.4 s | timeout | 22 / 19 | 12.6 s | 72.2 s | 123,347 | none — failure |
| omp / candidate | financial | 31.8 s | 30.0 s | 4 / 3 | 7.0 s | 22.9 s | 85,116 | answer |

## Accuracy and coverage

Final candidate OMP trip recovered two cited postponement exchanges and correctly distinguished discussion from a confirmed replacement date. Its promises answer found a candidate promise plus a same-day work-in-progress update, qualified the absence of explicit confirmation to the excerpts examined, and disclosed clipped/heuristic coverage. Its financial answer was inconclusive: it distinguished a proposed expense from an assessed penalty, including an explicit statement that penalties would not accrue. This is accurate caution but misses a known financial-impact example; it is not full fact recovery.

Final candidate AnythingLLM promises produced citations, a concrete promise and later partial fulfillment, and qualified its remaining findings to examined messages. Its trip answer cited travel delay/cancellation notices but **missed the known postponement conversation**; its absence wording was too broad after an opening scope caveat. Its financial run timed out with no answer. These quality failures prevent claiming reliable bounded performance or overall answer-quality success.

An initial candidate AnythingLLM trip pilot recovered the postponed processing exchange and later scheduling material, but omitted citations and over-linked the later schedule to the original postponement. Its initial promises pilot omitted citations and overclaimed that a label was never received despite a later refund. Initial candidate OMP promises independently found an apparently unresolved reimbursement and then verified a later bank confirmation from another sender, correctly excluding it from unresolved promises. No universal absence or independent proof of an assessed penalty is established by these searches.

The final samples reduce OMP calls sharply relative to timeout baselines and remove duplicate representation in AnythingLLM, but AnythingLLM still exceeds the advisory budget and can miss facts. Model/client behavior remains a material limitation. The 30-second median, 60-second maximum, eight-total-call target, direct-grep comparison and 2016–2018 flight acceptance remain open.

## Engineering verification

All 204 tests pass against an isolated pinned Qdrant instance. Ruff and formatting pass. Python and TypeScript SDK checks pass against the frozen real mailbox in both formats; TypeScript fixture checks also pass in both. New real-SDK regressions verify one versus two copies of a deep-body marker, unchanged JSON payloads, continued batch offsets, unknown-message errors, and the optional generation tool's original return behavior. Existing byte-budget, pagination, deletion, Unicode, regex and active/committed tests remain green.

Independent review found no blocking issues and passed 59 focused Python tests plus both TypeScript fixture modes. Its minor batch-pagination wording concern was fixed. Private logs, prompts with email content, credentials, message IDs and citations remain outside the repository.
