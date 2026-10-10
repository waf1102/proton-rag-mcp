# Qdrant migration validation — 2026-10-09

The VM migration uses Qdrant Server 1.19.2, SQLite and the existing local Ollama `nomic-embed-text` model. The runtime release is commit `6e31bbb`; the development branch and draft PR retain the implementation and review history.

## Frozen source and import

| Check | Result |
| --- | --- |
| Cached canonical messages | 6,681 |
| Nonempty / empty cached texts | 6,656 / 25 |
| Folder entries indexed at freeze | 10,999 |
| Observed folder entries expected | 62,970; includes copies and labels |
| Source chunks | 24,909 |
| Chunks owned by historical aliases | 55; canonicalized without discarding them |
| Embedding compatibility samples | 8 passed |
| Imported chunks / rebuilt messages | 24,909 / 0 |
| Ready manifests | 6,681; empty texts have zero chunks |
| Mailbox coverage | Incomplete; unique expected message count remains unknown |

The source was frozen after cleanly stopping the writer and coordinating its locks. The complete old volume, catalog, private configuration, unit definitions, image identity and checksums were preserved outside Git. The old backend was restarted for search while ingestion stayed paused. The exporter read only the frozen volume through the installed old Lance reader. Every nonempty message had affirmative document/vector mappings.

Verification compared semantic fingerprints for canonical metadata, folder entries, cached text/extraction flags, aliases, folder inventory, orphan marks and catalog settings. Every actual target point was checked against its manifest, including dense vector direction within float32 tolerance, payload text hashes, profile and identity. Exact collection totals matched 24,909 points, with no unexplained points. Binding replaces old backend paths only in the target; the frozen source remains intact.

## Test and review evidence

- 124 tests passed with the pinned Qdrant service enabled; Ruff, formatting and Compose configuration passed.
- GitHub checks passed for the implementation branch. Both SDK clients passed stdio interoperability with real local embeddings and a Qdrant restart.
- A 257-row frozen Lance fixture verified bounded export pagination. A 1,000-vector fixture verified bounded import-reader memory.
- Astra reviewed the full branch. Five Important findings were reproduced and fixed: missing completeness mappings, numerical variation during partial replay, restoration of unfinished ingestion, changed-model full rebuilds, and disk-floor enforcement.
- Real snapshot/restore tests preserve prepared messages, partial uploads and interrupted deletions. Other tests cover corrupted vectors, changed exports, ownership/profile mismatches, maintenance locks and low-disk resume.

## Live cutover and recovery

The release MCP server passed four live search cases and 20 citation-to-read checks. Query times were 3.810, 1.028, 0.850 and 0.621 seconds. Full paging reconstructed a 200,247-character cached message; historical aliases resolved correctly. These are observations on this VM, not latency guarantees. Private machine reports stay beside the target catalog and recovery snapshots; they contain counts/checksums rather than mail contents.

A paired private SQLite/Qdrant backup completed and was restored into a fresh directory and separate collection. Full verification passed for 6,681 ready messages, zero pending manifests and 24,909 chunks, with matching semantic fingerprints. The isolated restore collection and synthetic test containers were then removed; the completed backup remains available.

The stable app symlink, private environment, shared MCP launcher and daemon unit were switched together. The daemon remained active with zero restarts. The shared launcher exposed all four configured tools and passed search/citation/read/status checks. All 6,681 original cached texts and extraction flags remained byte-for-byte unchanged. Folder entries advanced from 10,999 to 11,542; the latest progress report showed zero failed messages. Mailbox coverage remains incomplete (62,970 observed folder entries, with unique total unknown), so ingestion continues. Existing MCP client sessions should reconnect to load the new launcher configuration.

The old AnythingLLM service and maintenance timer are retired from active startup; their volume, image, immutable application, private configuration and frozen recovery pair remain available. This VM uses the rootless Podman Quadlet deployment. Qdrant container memory was approximately 117 MB after verification, versus approximately 256 MB for the old container before retirement; these are observed container-memory values, not capacity guarantees. About 92 GiB remained free after recovery validation. No Proton Mail messages, folders or flags were modified.

The old recovery pair is available through `~/.local/share/proton-rag/recovery/current-qdrant-migration`. Keep it through the observation period. An immutable checkout of the old application at commit `b2c3355` and its installed environment are retained alongside the new release, so rollback does not depend on the development checkout staying unchanged. For rollback after new ingestion, first preserve the new catalog and any cached messages no longer present upstream, then restore the complete old catalog/backend/configuration pair. See [the migration procedure](operations-qdrant-migration.md) and [paired recovery workflow](operations.md#backups-and-restore-verification).

## Implementation decisions and tradeoffs

| Decision | Reason and practical cost |
| --- | --- |
| Use the established sibling worktree location | Preserves the main checkout and prior worktrees; the PR worktree remains a separate path. |
| Use this VM's rootless Podman | Reuses its existing services; full Docker/Fedora deployment remains untested. |
| Put recursive context splitting in the index's chunk preparation | Persisted chunks and vectors stay aligned; this integration requires coverage alongside embedding-client tests. |
| Use a shared runtime factory and independent readiness status | Keeps daemon and MCP bindings consistent; adds one focused module. |
| Restore into an absent collection and rewrite only its checked ownership binding | Preserves point identity without a second server; failed verification leaves an isolated target to inspect. |
| Exclude retired duplicate-cleanup paths from semantic fingerprints | Bind retires that old-backend queue only in the target; queue-only differences must be inspected in the retained source. |
| Store normalized float32 vector proofs in manifests | Verifies actual vectors despite cosine normalization; adds roughly 4 KiB per chunk to SQLite. |

No minor review findings were deferred. Proton folder contents and flags are accessed only through the unchanged read-only IMAP adapter.
