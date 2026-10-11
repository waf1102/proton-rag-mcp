# Retrieval ranking evaluation

The new default improves recovery of known evidence on a small, varied mail evaluation set. It does **not** establish fast Gemini answer completion or a win against direct filesystem search.

## Implementation

Hybrid search embeds each query once and requests two bounded rankings from the same local Qdrant collection in parallel:

- The existing sharp dense/BM25 RRF ranking, with its existing candidate depth.
- A smoother dense/BM25 RRF ranking (`k=20`), with 500 candidates per channel.

After active membership, committed manifests, checksums, embedding profile and explicit message scope are checked, message-level reciprocal-rank fusion combines the lists with offset 10 and weights 1:2. Each message votes once per list. Ties are stable within the returned lists. The passage with the largest contribution is returned verbatim from a verified indexed chunk. Requested result limits and response paging/budgets remain unchanged.

This adds one local Qdrant request per hybrid query, including each alternate query, without adding MCP calls or re-embedding documents. There is no reranker dependency, new model service, cached-text rewriting, or index migration. Exact search and `grep_mail` retain their existing behavior. `RAG_SEARCH_RANKING=legacy` restores the previous single-ranking search for comparisons or rollback.

## Local quality and timing

The private read-only catalog contains 22,354 messages. Thirty-six manually selected queries each identify a known supporting email: travel, commitments, financial/admin disputes, receipts, appointments, orders, and technical messages. These labels are **known-evidence recovery**, not exhaustive relevance judgments or final-answer accuracy. Equivalent quoted messages can also be relevant without earning credit for the labeled ID.

Twelve queries were used for tuning; twelve validation and twelve additional confirmation queries checked candidate changes. Several candidate designs were rejected during exploration. This is a small exploratory evaluation, not an independently administered benchmark. The final ensemble parameters were selected using training results. Private queries, message IDs and email text are not committed.

The final live benchmark alternates strategy order, includes query embedding and client-side validation in timing, and repeats each query three times. Both strategies use the same frozen catalog, shared local index/profile and services. The live index continued ordinary ingestion and had one additional message by the final content audit; the read-only catalog excludes that message from returned evidence. All 22,354 frozen message records, cached texts and commit manifests remained identical to live entries, and the frozen database hash was unchanged. The index is shared, rather than a separately restored immutable collection, so new-mail/tie effects remain a reproducibility limitation. Each strategy has 108 query trials; repeats are correlated observations of 36 cases, not 108 independent labels.

| Metric | Previous ranking | Ensemble |
| --- | ---: | ---: |
| Known evidence recovered in top 10 | 86/108 (79.6%) | 93/108 (86.1%) |
| Mean reciprocal rank, zero when outside top 10 | 0.519 | 0.594 |
| Median local retrieval time | 97.7 ms | 133.0 ms |
| Maximum local retrieval time | 2.45 s | 2.89 s |
| Median returned index-hit bytes | 18,531 | 19,094 |

An earlier aggregate-only measurement found 85/108 versus 93/108, with medians 82.6 versus 104.6 ms. The detailed repeat was necessary to check per-case coverage. Some ranks fluctuate with approximate retrieval/ties and machine load. The detailed run found no case with fewer top-ten recoveries under the ensemble, and recovered two tuning examples previously below the first ten plus one inconsistent confirmation example.

Ranking tradeoffs remain: a travel-stipend example, certificate-email policy notice, and game receipt appear lower, though still within the top ten. The broad trip-postponement and financial-penalty queries still miss their known targets. The lease-reimbursement, membership-pause and delivery-delay queries also remain outside the first ten. Better ranking cannot create evidence absent from the bounded candidates. No completeness claim follows from these results.

The small local MiniLM cross encoder was also evaluated and rejected: approximately two seconds for 20 passages and 11 seconds for 100 on this machine, with coverage/ranking regressions. Individual fusion changes and hard head-preservation blends also regressed validation cases. They are not shipped.

## Reproduce local comparisons

Use `scripts/benchmark-ranking.py` with a private frozen catalog and external JSON labels. Each label needs `query` and nonempty canonical `expected_ids`; optional fields are `correspondence_only`, `filters` (existing header-date/sender/subject/folder filters), and short `split`/`category` labels. Known evidence must be active, committed, and included by the filters. Use the local index configuration matching the snapshot.

```bash
uv run python scripts/benchmark-ranking.py \
  --catalog /private/snapshot.db --cases /private/cases.json --repeats 3
```

The script opens SQLite read-only and prints aggregate metrics without query text, message IDs or email bodies. Hit bytes measure returned index hits; they are not MCP/client payload bytes. Keep private label files outside the repository.

## Gemini ordinary-prompt checks

Use the same three ordinary questions recorded in [the semantic mail evaluation](semantic-mail-validation.md), fresh `gemini-3.1-pro-high` sessions, unchanged server workflow instructions and a neutral instruction selecting the frozen-mailbox test server. Count schemas, generated-output reads and attempted MCP calls. Empty timeout answers fail even if the client reports success.

One baseline and one candidate run per question used a 90-second client ceiling. All six produced empty timeout answers:

| Ranking | Question | Wall time | Total calls | MCP calls | MCP time | Reported agent time | Viewed/inline bytes | Serialized MCP bytes |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Legacy | Trip | 92.2 s | 12 | 4 | 6.8 s | 72.6 s | 48,732 | 111,632 |
| Ensemble | Trip | 93.2 s | 11 | 4 | 12.6 s | 70.8 s | 49,959 | 82,811 |
| Legacy | Promises | 92.0 s | 6 | 2 | 2.2 s | 83.9 s | 21,658 | 39,311 |
| Ensemble | Promises | 92.1 s | 9 | 3 | 3.0 s | 82.6 s | 49,781 | 88,042 |
| Legacy | Financial | 92.3 s | 10 | 3 | 2.2 s | 83.0 s | 42,095 | 86,243 |
| Ensemble | Financial | 92.2 s | 12 | 3 | 6.9 s | 81.0 s | 83,662 | 149,978 |

Viewed bytes use client-reported schema/generated-file byte counts plus inline output. Serialized MCP results include duplicate text/structured representations and can exceed the tool's text budget. Counts include attempted calls at the cutoff. Query paths and answer depth are model-selected. Neither tool-call reduction nor token/payload savings are established by these runs.

These are diagnostic samples, not three-repeat acceptance measurements. Parts of candidate timing overlapped local verification/benchmark work, so no causal end-to-end speed claim is supported. The promises/financial runs made three retrieval calls but still failed to complete. Final-answer factual accuracy and citation credit are zero for empty responses. Reported agent intervals include client/provider processing, not just inference.

The 30-second median, 60-second completion ceiling, eight-total-call target, direct-grep comparison and original flight acceptance remain open. The merge gate for this iteration is a measured local retrieval improvement with preserved known-evidence coverage, correct citations, bounded work and passing compatibility checks. It is not an end-to-end speed release. Production deployment is separate from this merge.

## Verification

The complete suite passes 199 tests including an isolated real Qdrant ranking regression. Tests cover consensus-only candidates, active/committed checks, corruption/profile/scope exclusion, deduplication, limits, stable ordering, legacy selection and read-only benchmark input. Ruff and formatting pass. Python and TypeScript MCP clients pass fixture and frozen-mailbox interoperability, including stable citations, matched passages, grouped search, byte limits and the previously missed follow-up.

Fresh independent whole-branch review found no blocking issues and independently passed 34 focused tests. Two nonblocking gaps are deferred: the public aggregate benchmark does not summarize per-case regressions (the private detailed capture supports this release's coverage check), and concurrent request failure/cancellation lacks a dedicated regression test. The reviewed failure path returns no partial ranking and retains existing read-only transport timeouts.
