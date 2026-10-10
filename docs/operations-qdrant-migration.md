# AnythingLLM to Qdrant migration

The compatibility exporter runs only in the old pinned image. The supported runtime uses SQLite, Qdrant and Ollama. Indexing need not be complete before migration: cached message content and folder progress are retained, and unread messages are indexed afterward using read-only IMAP.

## Freeze and preserve the source

1. Check free space for the old volume, frozen export, target database and recovery snapshots. Keep private artifacts outside Git, under mode-0700 directories. Stop/disable the old maintenance timer, coordinate `maintenance.lock`, stop the indexing daemon cleanly, and confirm `daemon.lock` is free.
2. Stop the old backend briefly. Use SQLite's backup API to copy the catalog, and archive the complete old volume while no container is writing it. Preserve private runtime configuration, the old app symlink target/commit, image identities and Ollama model digest. Record checksums. Do not just copy a live SQLite file or export a changing Lance table.
3. Restart the old backend for search if needed, leaving its writer stopped. Extract the frozen volume into a private directory for a read-only export helper. The old volume and catalog remain the rollback pair.
4. Create `/absolute/private/export/profile.json` containing the recorded `EmbeddingProfile`: model, digest, dimension, context, document/query prefixes, normalization and provenance. Verify the old adapter's actual settings. The existing Ollama adapter uses unprefixed text, dimension 768 and context 2048. Merely sharing a model name does not establish compatibility.

Export with the old installed image and frozen storage (adjust the workspace if necessary):

```sh
podman run --rm --user 0 --network none --pull never \
  --volume /absolute/private/frozen-storage:/app/server/storage:ro \
  --volume /absolute/private/export:/export:ro \
  --volume "$PWD/scripts/export-anything.cjs:/export-reader.cjs:ro" \
  --env RAG_EXPORT_WORKSPACE=proton-mail --entrypoint node \
  docker.io/mintplexlabs/anythingllm@sha256:5ce7b65badd7de94827846d33fe1b38eff71f86875b7ce88d6e56d2371ec2d6b \
  /export-reader.cjs > /absolute/private/export/chunks.jsonl
```

Use `umask 077` before redirecting output. This exports every row in bounded batches and includes source version, row count and content checksum. The JSONL contains private chunk text and vectors.

## Build an isolated target

Start the new pinned Qdrant service with its private API key. Load the new settings, using a **different** `RAG_STATE_DIR`; leave the old catalog untouched. Use `QDRANT_COLLECTION=proton-mail`, or a fresh account-specific name. Keep the old embedding model/digest available.

```sh
uv run proton-rag-migrate audit \
  --source-catalog /absolute/private/source/catalog.db \
  --export /absolute/private/export/chunks.jsonl \
  --backend-db /absolute/private/frozen-storage/anythingllm.db

uv run proton-rag-migrate verify-embeddings \
  --source-catalog /absolute/private/source/catalog.db \
  --export /absolute/private/export/chunks.jsonl

uv run proton-rag-migrate import \
  --source-catalog /absolute/private/source/catalog.db \
  --export /absolute/private/export/chunks.jsonl \
  --backend-db /absolute/private/frozen-storage/anythingllm.db \
  --reuse-verification "$RAG_STATE_DIR/embedding-verification.json"
```

The importer clones SQLite, resolves old aliases, verifies vector shape/finite values and source identities, compares legacy document/vector mappings, and preserves cached text, folder entries and stable IDs. It streams vectors from disk. Exact chunk groups are reused only with a compatible sample verification report for that export. Incomplete groups rebuild from cached text. If compatibility fails, omit `--reuse-verification` or add `--rebuild`; this re-embeds cached content without fetching it from Bridge. Source identity or cache failures block the operation.

Resume an interrupted import with the same command and frozen source. Completed manifests are verified and skipped; deterministic IDs make replay safe. A changed export/profile/source requires a fresh target. Empty texts receive a ready zero-chunk manifest; uncached inactive intents remain pending.

## Verify and cut over

```sh
uv run proton-rag-migrate verify \
  --source-catalog /absolute/private/source/catalog.db \
  --export /absolute/private/export/chunks.jsonl

uv run proton-rag-migrate bind \
  --source-catalog /absolute/private/source/catalog.db \
  --export /absolute/private/export/chunks.jsonl
```

Verification compares semantic catalog fingerprints and cached text hashes, checks every expected point, and rejects unexplained collection points. Bind repeats verification before replacing old backend paths in the target and publishing its binding. Old duplicate-cleanup paths are retired only in the target; the frozen source retains them. Backend readiness does not claim complete mailbox coverage.

Switch the stable app symlink, daemon settings and MCP client settings to the new code/catalog/collection together. Start the daemon; reconnect existing MCP clients. Verify `index_status`, search followed by `read_mail`, preserved citations and indexing progress. Create a [paired backup and isolated restore check](operations.md#backups-and-restore-verification). Stop/disable the old backend and maintenance timer after validation; retain the rollback pair, image and private configuration.

## Rollback

Stop the new writer. Preserve its current catalog and Qdrant snapshot before changing anything. Restore the complete frozen old catalog/backend pair and old configuration/app target, then restart the old backend and writer. Normal read-only synchronization catches up on mail received after the freeze. First export/preserve newly cached messages that have since disappeared from Bridge, so rollback cannot discard their only remaining cached copy. Never pair the migrated catalog with the old backend, or the old catalog with Qdrant.

No migration step writes to Proton Mail folders, flags or messages.
