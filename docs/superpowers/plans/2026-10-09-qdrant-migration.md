# Qdrant Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Subagent-driven execution is an option only when selected by the user. Steps use checkbox syntax for tracking.

**Goal:** Replace AnythingLLM with a smaller local retrieval service while preserving cached mail, stable identities, and incremental indexing progress.

**Architecture:** SQLite remains the mail catalog; one local Qdrant Server stores searchable chunks with dense and native BM25 vectors. Python calls Ollama and Qdrant directly. A resumable importer builds a separate target installation from a frozen, backed-up source and publishes it only after validation.

**Tech Stack:** Existing Python 3.11+, SQLite, httpx, MCP, pytest/Ruff, Ollama; Qdrant Server 1.19.2 pinned by digest. Existing Node LanceDB reader is used only in an isolated export helper.

**Spec:** [Proposed design](../specs/2026-10-09-qdrant-migration-design.md). Both documents are proposals; no execution or deployment occurred during planning.

## Global constraints

- Read-only Bridge access, local retrieval, and existing optional OpenRouter behavior.
- Preserve catalog namespace, canonical IDs, aliases, texts, metadata, memberships, citations, pending states, and observed folder coverage.
- Keep `search_mail`, `read_mail`, `index_status`, and `answer_mail` arguments compatible.
- Qdrant REST at `127.0.0.1:6333`; loopback-only Ollama; `trust_env=False`; private credentials and migration artifacts outside Git.
- Dense name `dense`, 768 dimensions, cosine; sparse name `lexical`, model `qdrant/bm25`, modifier `idf`; hybrid fusion `rrf`.
- Every embedding profile includes actual model digest, dimension, context, prefix policy, normalization policy, and provenance. Never mix incompatible profiles in one collection.
- Preserve imported chunks/vectors when verified; new splitter target 1,800 characters, overlap 200, context 2,048 for the observed profile, `truncate=false`, recursive splitting on context rejection.
- Writes use deterministic UUIDs and `wait=true`; readiness follows acknowledgment of the entire message's chunk manifest.
- Projected free disk space remains above 2 GiB at every migration stage; initial working-space target at least 5 GiB or separate provisioned storage.
- Production data and running services remain untouched until a tested release and concrete migration report/runbook exist.

## Review focus

1. Lost acknowledgments or crashes between Qdrant writes and SQLite manifest commit: restart reconciles safely without visible partial messages.
2. Cached text is empty, a legacy alias owns chunks, or some chunks are absent: preserve catalog records and IDs, canonicalize deliberately, rebuild incomplete groups.
3. The Ollama tag changes while dimensions stay 768: refuse incompatible reuse and keep query/document profiles aligned.
4. Mail arrives, changes folders, or disappears while ingestion is paused: preserve freeze-time data and process changes safely on resume/rollback.
5. Low disk or a maintenance timer interferes: do not start unsafe copying, concurrent writes, or automatic destructive cleanup.

---

### Task 1: Embedding, identity and backend contracts

**Files:** Create `src/proton_rag/index.py`, `src/proton_rag/embeddings.py`, `src/proton_rag/chunks.py`; modify `src/proton_rag/config.py`, `src/proton_rag/health.py`; create `tests/test_embeddings.py`, `tests/test_chunks.py`; extend `tests/test_config.py`.

**Interfaces:** Define `IndexFailure(code: str, operation: str, http_status: int | None = None)` and `PendingIndexError`. Define `EmbeddingProfile` with the fields in Global constraints, `Chunk(identity: str, ordinal: int, text: str)`, `split_text(text: str, target: int = 1800, overlap: int = 200) -> list[Chunk]`, and deterministic point identity `point_id(namespace: str, message_key: str, profile_id: str, chunk_id: str) -> str`. `Ollama.embed_documents(texts: list[str]) -> list[list[float]]` and `await Ollama.embed_query(text: str) -> list[float]` validate the bound profile.

- [ ] Write tests: `test_point_ids_are_stable_and_namespace_scoped`, `test_chunks_preserve_unicode_and_long_paragraphs`, `test_embedding_context_rejection_splits_without_truncation`, `test_same_dimension_model_change_is_rejected`, `test_embedding_errors_are_redacted`. Assert full input coverage, stable IDs, exact 768-length finite vectors, no output containing private payloads, and rejection of changed model digest.
- [ ] Run `uv run pytest tests/test_embeddings.py tests/test_chunks.py tests/test_config.py -q`; confirm new tests fail before implementation.
- [ ] Implement the contracts and new configuration: `QDRANT_URL`, `QDRANT_COLLECTION`, `QDRANT_API_KEY`, `OLLAMA_URL`, `RAG_EMBEDDING_MODEL`, plus explicit profile/context/chunk settings. Keep `RAG_STATE_DIR` and existing limits. Capture/validate the actual Ollama digest; allow an explicitly recorded legacy unprefixed profile for reuse.
- [ ] Rerun the focused tests; confirm pass, then commit this independently tested component on the implementation branch.

### Task 2: Qdrant retrieval and durable manifests

**Files:** Create `src/proton_rag/qdrant.py`, `src/proton_rag/index_state.py`; modify `src/proton_rag/catalog.py`; create `tests/test_qdrant.py`, `tests/test_index_state.py`, `tests/test_qdrant_integration.py`.

**Interfaces:** `IndexState` is bound to catalog namespace and collection/profile. It records a message's expected point IDs/checksums, pending/ready phase, and revision. `QdrantIndex.ensure(key: str, text: str, before_upload=None) -> list[str]`, `recover(...) -> list[str]`, `find(key: str) -> list[str]`, `remove(keys: list[str]) -> None`, `refresh() -> None`, and `await search(query: str, limit: int) -> list[dict]` provide the current synchronization contract. A complete backend handle is `[canonical_message_key]`; search results use `text` and `metadata.docSource` compatible with existing MCP assembly. `import_message(key: str, chunks: list[ImportedChunk]) -> list[str]` shares the same manifest publication path. Define `ImportedChunk` here: source row ID, exact text, vector, profile ID, and checksum.

- [ ] Write tests: `test_timeout_replays_same_ids`, `test_manifest_is_not_ready_after_partial_batch`, `test_restart_after_upsert_before_manifest_commit`, `test_empty_text_has_ready_zero_chunk_manifest`, `test_remove_is_scoped_and_idempotent`, `test_uncommitted_or_orphan_hits_are_hidden`, `test_hybrid_query_groups_distinct_messages`.
- [ ] Run `uv run pytest tests/test_qdrant.py tests/test_index_state.py -q`; confirm expected failures.
- [ ] Implement collection validation/bootstrap, direct bounded HTTP requests, durable manifests and UUID upserts, native BM25 generation, hybrid RRF retrieval, and readiness filtering. Do not treat a successful request alone as proof of the full chunk set. Search may use Qdrant grouping or bounded adaptive overfetch to avoid filling all candidates with one message's chunks.
- [ ] Add an isolated integration fixture for the pinned server: test BM25 plus dense retrieval, persistence across restart, exact point-count invariants after lost-response replay, and acknowledgment checks. Use dedicated synthetic collections and an ephemeral host port; never inherit the production state directory or API key. Existing tests may use HTTP mocks; this fixture must exercise the real service.
- [ ] Run focused unit tests and `uv run pytest tests/test_qdrant_integration.py -q`; require an actual passing run against the pinned image, rather than skipped tests. Record server capability and memory settings. Commit only when these checks pass.

### Task 3: Route the daemon and MCP through the new index

**Files:** Modify `src/proton_rag/daemon.py`, `src/proton_rag/sync.py`, `src/proton_rag/mcp_server.py`, `src/proton_rag/catalog.py`; extend `tests/test_sync.py`, `tests/test_dedup.py`, `tests/test_mail_reading.py`, `tests/test_reliability.py`, `tests/test_mcp.py`, `tests/fixture_server.py`; update `scripts/live-synthetic.py`.

**Interfaces:** Existing public MCP and sync interfaces remain. Replace AnythingLLM-specific exceptions/imports with Task 1 contracts and instantiate `QdrantIndex` from shared settings and the bound catalog. Add `index_backend`, `index_ready_messages`, `index_pending_messages`, and `embedding_profile` to status. Preserve mailbox coverage fields and meanings.

- [ ] Add regression tests: `test_backend_binding_mismatch_refuses_startup`, `test_migrated_active_mail_is_not_refetched`, `test_alias_read_and_folder_citations_survive_backend_change`, `test_index_readiness_is_separate_from_mailbox_coverage`, `test_pending_membership_reconciles_after_resume`.
- [ ] Run the regression files and confirm the new tests fail before routing changes.
- [ ] Connect ingestion, duplicate cleanup, and delayed orphan deletion to canonical handles/manifests. A target catalog may bind to Qdrant only through completed migration or explicit fresh bootstrap. Do not allow old `custom-documents/*.json` paths to be interpreted as new backend handles. Move authoritative metadata assembly ahead of index writes where needed for lexical search.
- [ ] Run `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`, and `npm run test:interop`; confirm tools, paging, citations, local-only behavior, parser safeguards, and deduplication pass. Commit the working application path.

### Task 4: Frozen-source export and resumable migration

**Files:** Create `src/proton_rag/migration.py`, `src/proton_rag/migration_report.py`, `scripts/export-anything.cjs`, `tests/test_index_migration.py`; modify `pyproject.toml` to add `proton-rag-migrate = proton_rag.migration:main`.

**Interfaces:** `audit(source_catalog: Path, export_manifest: Path) -> MigrationReport`, `import_index(source_catalog: Path, export_path: Path, target_state: Path, backend: QdrantIndex, resume: bool = True) -> MigrationReport`, and `verify_target(source_catalog: Path, target_catalog: Path, backend: QdrantIndex) -> MigrationReport`. Reports contain counts/checksums, profile/model identity, discrepancy classifications, and readiness, never mail contents. CLI subcommands: `audit`, `import`, `verify`, `bind`; `bind` is available only after the current verification passes. Export reads a frozen read-only volume with the exact old installed reader, using explicit bounded limits/offsets and a fixed source table version; its default query limit must not silently export only 10 rows.

- [ ] Build small fixtures with alias-owned chunks, identical folder copies, empty text, incompatible vectors, partial chunks, one pending membership, and unexpected source IDs. Tests: `test_export_reads_every_chunk`, `test_migration_preserves_catalog_identity_and_text_hashes`, `test_alias_chunks_map_to_canonical_message`, `test_missing_chunk_group_rebuilds_from_cache`, `test_interrupted_import_resumes_without_duplicate_points`, `test_unexplained_source_blocks_bind`, `test_bind_replaces_paths_only_in_target`.
- [ ] Run `uv run pytest tests/test_index_migration.py -q`; confirm failures before implementing the importer.
- [ ] Implement cloned-catalog migration and per-message checkpoints; validate dimensions/finiteness and provenance, compare expected chunk groups with source catalog paths, and rebuild from cached text when reuse fails. Account for every exported row, including duplicate legacy groups. Copy alias/location/coverage state unchanged. Preserve source catalog, source backend, and pending intent; never instantiate a writer against the live source to run audit.
- [ ] Verify a second import is a no-op for complete groups and that rebuilding does not fetch IMAP. Simulate a changed source manifest after checkpointing and refuse unsafe resume.
- [ ] Run the unit migration suite and a real-Qdrant synthetic migration/restart test. Commit migration tooling and its concrete report format.

### Task 5: Deployment, backups and retirement of AnythingLLM

**Files:** Modify `deploy/compose.yaml`, `deploy/proton-rag-daemon.service`, `.github/workflows/checks.yml`, `pyproject.toml`, `README.md`, `docs/operations.md`, `docs/podman.md`, `docs/fedora.md`, `docs/security.md`, `docs/validation.md`; create `deploy/proton-rag-qdrant.container`, `deploy/proton-rag-qdrant.volume`, `src/proton_rag/backup.py`, `tests/test_backup.py`. Remove `src/proton_rag/anything.py`, `src/proton_rag/lance-maintenance.cjs`, obsolete AnythingLLM deployment files, obsolete bootstrap script, and AnythingLLM-only tests after Task 4's export path is independent. Replace the old maintenance command/timer with a clearly documented backup workflow; existing installed timers are disabled during live cutover.

**Interfaces:** A backup saves a matching catalog/Qdrant snapshot and binding/profile manifest under the writer/maintenance lock, and resumes the writer safely on failure. Provide a restore-verification command that uses isolated target paths and collection names. Migration compatibility tools may refer to AnythingLLM; the supported daemon/MCP path and setup must not.

- [ ] Add tests `test_backup_pairs_catalog_and_backend`, `test_backup_failure_resumes_writer`, `test_low_disk_refuses_copy_before_mutation`, `test_restore_uses_isolated_target`, `test_maintenance_lock_blocks_migration`. Run `uv run pytest tests/test_backup.py tests/test_reliability.py -q` and confirm new tests fail.
- [ ] Implement the paired backup/restore flow. Pin Qdrant 1.19.2's verified digest, bind REST to loopback, configure local authentication, keep gRPC unpublished, and document storage settings using the chosen release's API. Bootstrap automatically validates the collection without a setup UI.
- [ ] Rewrite setup/client examples to use Qdrant credentials and the shared catalog binding, preserve Ollama/OpenRouter guidance, and document migration/rollback commands plus MCP reconnect. Remove unsupported old maintenance instructions and the misleading requirement to keep AnythingLLM running.
- [ ] Validate Compose/Quadlet configuration, add the isolated pinned-Qdrant integration run to CI, run the full unit/interop suite and real service integration checks, and scan supported runtime/docs for obsolete dependency paths. Measure representative resource usage and search latency before claiming savings. Commit the supported deployment.

### Task 6: Review, release and controlled live cutover

**Files:** Add `docs/operations-qdrant-migration.md` with exact commands from the implemented CLI, source/target path roles, quiesce/restart order, validation gates, and rollback procedure. Keep actual private snapshots and machine-specific reports outside Git.

- [ ] Review the completed branch against every spec completion criterion. Focus on cross-store crashes, alias preservation, model changes, maintenance races, and rollback after new mail. Sol can implement; recommend a focused Astra review if the user selects that model, without assuming authorization to spawn agents.
- [ ] Open a draft PR in `waf1102/proton-rag-mcp` and immediately register its URL with T3 `link_pull_request`. Confirm CI and linked-PR state before finishing PR work. Do not merge/deploy merely because planning was requested.
- [ ] Produce a concrete live preflight report with updated key/row counts, the exact service/image/model IDs, backup path, disk budget, and target memory settings. Storage headroom and a recoverable frozen source are prerequisites for production import.
- [ ] When implementation/cutover is authorized, disable the conflicting timer, quiesce writers, capture the complete old catalog/backend/configuration pair, restart old search only, and import into the separate Qdrant/target catalog. Validate all counts/checksums/manifests plus search/read/citation cases.
- [ ] Switch the stable code and shared daemon/MCP configuration together; restart/reconnect. Verify ingestion advances while migrated messages are neither fetched nor embedded again. Keep incomplete mailbox coverage explicit.
- [ ] Perform an isolated recovery rehearsal. For a live rollback after new ingestion, preserve new cached content before restoring old state; normal IMAP replay alone cannot recover messages already deleted upstream. Retain the complete old recovery pair through the observation period and remove old services/timers from active startup.

## Execution recommendation

Implement sequentially with GPT-6.1-Sol at high reasoning in one isolated worktree. The adapter, manifest, importer, and deployment tasks share state contracts, so a single implementer avoids handoff overhead. Use Astra for a focused final migration/recovery review if desired. Planning does not require changing models, waiting for mailbox completion, or starting a Paperclip task.
