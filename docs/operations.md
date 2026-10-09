# Operations

## Configuration

The daemon and MCP server share environment-based settings. Point both at the same catalog and AnythingLLM workspace. Use separate state directories and workspaces for separate accounts or fixture tests.

| Variable | Default / purpose |
| --- | --- |
| `RAG_STATE_DIR` | `~/.local/share/proton-rag/mail`; persistent catalog |
| `ANYTHING_URL` | `http://127.0.0.1:3001`; loopback only |
| `ANYTHING_API_KEY` | Required API credential |
| `ANYTHING_WORKSPACE` | `proton-mail` |
| `IMAP_HOST`, `IMAP_PORT` | `127.0.0.1`, `1143` |
| `IMAP_USER`, `IMAP_PASS` | Bridge credentials; daemon only |
| `IMAP_CA_FILE` | Trusted Bridge CA/certificate; system trust if omitted |
| `RAG_FOLDERS` | JSON array; empty/unset discovers all selectable folders |
| `RAG_EXCLUDED_FOLDERS` | JSON array of exact folder names to exclude |
| `RAG_POLL_SECONDS` | `30` |
| `RAG_EMBEDDING_TIMEOUT` | `300` seconds; read timeout for CPU-bound embedding requests (other index API requests: 60 seconds) |
| `RAG_BATCH_SIZE` | `25`; bounds work between disk checks; embeddings commit per message |
| `RAG_MIN_FREE_BYTES` | `2147483648` (2 GiB); pause ingestion below this free space |
| `RAG_WARN_FREE_BYTES` | `3221225472` (3 GiB); log low-space warnings |
| `RAG_STORAGE_PATHS` | Optional JSON array of host paths on backend storage filesystems; checked alongside the catalog |
| `RAG_MAX_MESSAGE_BYTES` | `33554432`; larger bodies skipped |
| `RAG_PARSER_TIMEOUT` | `15` seconds |
| `RAG_MAX_TEXT_CHARS`, `RAG_MAX_PARTS` | `200000`, `200`; extraction bounds |
| `RAG_SEARCH_DEFAULT`, `RAG_SEARCH_MAX` | `10`, `50` |
| `RAG_EXCERPT_CHARS`, `RAG_ANSWER_SOURCES` | `4000`, `5` |
| `OPENROUTER_API_KEY` | Optional; enables `answer_mail` in MCP |
| `OPENROUTER_MODEL` | `openai/gpt-4.1-mini` |
| `RAG_MAX_OUTPUT_TOKENS` | `1024` |

Folder lists use decoded Unicode names, not IMAP wire encoding. Exclusions take precedence. Numeric settings must be positive; default retrieval counts cannot exceed the configured maximum.

## Background service

1. Install the checkout with `uv sync --locked`. Set `~/.local/share/proton-rag/app` to the chosen checkout using a symlink.
2. Follow [Docker Compose setup](../README.md#2-start-the-local-index) or the [Podman guide](podman.md). Podman uses the checked-in Quadlets; Docker Compose manages containers independently.
3. Store ingestion settings in mode-0600 `~/.config/proton-rag/mail.env`. Put one `NAME=value` assignment on each line, use absolute paths, and protect directories with mode 0700. The daemon does not need an OpenRouter key.
4. Create the configured AnythingLLM workspace using `scripts/bootstrap-workspace.py` with the same settings. Run `proton-rag --once` to verify connectivity and indexing.
5. For Podman, copy `deploy/proton-rag-daemon.service` to `~/.config/systemd/user/`, then run:

```sh
systemctl --user daemon-reload
systemctl --user enable --now proton-rag-daemon.service
systemctl --user status proton-rag-daemon.service
journalctl --user -u proton-rag-daemon.service -n 30
```

The supplied unit depends on the Podman service names. With Docker Compose, remove those `After`/`Requires` dependencies in a user override and start Compose before the daemon. On hosts requiring unattended startup, configure the service account's user manager/linger and storage unlock separately.

## Synchronization and recovery

Each catalog has a persistent document namespace and is bound to its workspace; switching that workspace requires a new catalog. Each folder has its own UIDVALIDITY/UID identity, linked to canonical content identified by its full MIME SHA-256 hash. Identical copies share one parsed body and one embedding, even without Message-ID; messages with the same Message-ID but different content stay distinct. The daemon fetches new messages with `BODY.PEEK[]`; existing indexed messages are not downloaded each poll. Newest messages are processed first within each folder. It never writes to IMAP. Deletions from the local index occur only after a complete, stable inventory of that folder. Unavailable or disappeared folders keep their indexed data until explicitly reconciled; removing a folder from configuration does not erase its existing index.

Copies in multiple folders retain their locations and citations. Search returns each canonical message once with every known membership. A new membership needs one read-only body download to verify the hash, then reuses the parsed text and embedding. Existing memberships are not downloaded again each poll. Removing a label or folder membership leaves the shared message intact. Content without any remaining membership is hidden from search immediately; remote cleanup waits through another complete, stable scope cycle so folder moves can be discovered first. Metadata and attachments remain associated with each indexed message.

Only one daemon may write a catalog. The supplied service allows seven minutes for graceful shutdown so a five-minute embedding request can finish; adjust `TimeoutStopSec` if you raise `RAG_EMBEDDING_TIMEOUT`. Logs record events and counts rather than message contents. A failed connection or incomplete inventory must not become an index purge. Oversized and unsupported content is skipped within configured limits. A failed parser or unresolved upload is reported without starving the remaining messages; deletion reconciliation waits until those failures are resolved. Do not increase parser limits without considering host memory.

A crash after upload dispatch can leave an uncertain upload. Recovery checks for the stable document identity before retrying. If no document is visible, it stops that message for reconciliation rather than duplicating a potentially in-flight upload. Verify the remote request has stopped and inspect the specific pending catalog entry before any repair; never reset the whole catalog as a retry.

## Upgrades and backups

Use a separate workspace and state directory when moving from the old single-folder prototype. Its catalog is rejected with an explicit migration message; retain it for rollback and build a fresh index from the read-only mailbox. Client configurations must switch to the new catalog and workspace together.

For a consistent backup, stop the daemon and AnythingLLM, preserve the catalog and AnythingLLM volume together, then restart the services. Keep the model volume to avoid downloading embeddings again. Backups contain readable mail-derived data and need the same storage protection as the index.

## Optional fixture validation

Fixtures are isolated from production. Use a fresh state directory and `ANYTHING_WORKSPACE=proton-fixtures`; run bootstrap, then `scripts/live-synthetic.py`. That script exercises ingestion/search/deletion and refuses other workspace names. It must not run concurrently with a daemon writing the same catalog.

## Full-message reading and coverage

`search_mail` returns excerpts and a stable `message_id`. `read_mail(message_id, offset=0, length=20000)` opens the complete stored extracted text, including supported attachment text. Follow `next_offset` until null. Each page reports total characters and extraction limitations separately; paging does not discard the remaining text. Raw MIME and binary attachments are not exposed. `text_truncated` and `skipped_parts` identify parser bounds or unsupported parts; null means the old index did not record those details.

The catalog now stores extracted text alongside message metadata. New imports populate it automatically. On existing installations, the daemon backfills missing text from read-only Bridge fetches without re-embedding existing documents. Until backfill completes, reading an uncached message returns an explicit error. Keep the catalog and AnythingLLM volume together in backups; both contain private mail-derived text.

`index_status` reports the last observed inventories of all configured folders, indexed entries, cached text availability, pending entries, and the dates of indexed mail. Counts include folder copies, not unique emails. Search responses include this coverage report. A folder is complete only after stable reconciliation and all its entries are indexed. Coverage is a snapshot and can become stale; it does not turn relevance search into an exhaustive count. Missing results must not be interpreted as proof of absence while indexing is incomplete.


## Index health

Ask your assistant to call `index_status`. Its `runtime` object reports the last observed state (`running`, `degraded`, `paused`, or `maintenance`), free space, the last batch's progress, and a redacted `last_error` with an operation and error code. `updated_at` is an observation timestamp, not proof that the service is still running. Errors retain their timestamps after recovery; compare them with current progress. Use `systemctl --user status proton-rag-daemon.service` to check the live process.

Ingestion checks free space before each cycle and each batch. Below 2 GiB it pauses and retries automatically; search and cached message reading remain available. The pause does not delete indexed mail. Set `RAG_STORAGE_PATHS` if the backend volume is on a different filesystem from the catalog. Use existing host paths, for example `RAG_STORAGE_PATHS='["/mnt/mail-index"]'`. Warning space must be at least the pause threshold.

Logs distinguish timeouts (`index_timeout`), transport failures (`index_connection`), HTTP status failures (`index_http`), API failures (`index_api`), changed mailbox inventories (`mailbox_changed`), parser failures, and uncertain uploads (`upload_reconciliation`). They omit server response bodies, credentials, email content, and folder names. A changed inventory retains the index and retries; it is not a mailbox purge.

## Database maintenance

LanceDB retains old table versions after each embedded document. On the pinned AnythingLLM version, batching HTTP requests still writes one revision per document. Periodic maintenance compacts the current table and prunes versions older than one hour through LanceDB's supported API. The current table is preserved; unrelated workspaces are left alone.

Maintenance needs a background daemon managed by the supplied systemd user service and a dedicated named AnythingLLM volume. It briefly stops ingestion and AnythingLLM, so vector search is unavailable during that window. Bridge and Ollama stay running. Before changing the database it backs up the catalog and the complete AnythingLLM volume in a private directory. After maintenance it verifies the vector row count and restarts the services. Two completed recovery snapshots are retained; backups contain private mail-derived data and need disk space too.

From the repository root, with `mail.env` loaded as in the README:

```bash
uv run proton-rag-maintain \
  --backup-dir "$HOME/.local/share/proton-rag/backups"
```

For systemd-managed Podman/Quadlet installations, use:

```bash
uv run proton-rag-maintain --engine podman \
  --backend-service proton-rag-anything.service \
  --backup-dir "$HOME/.local/share/proton-rag/backups"
```

Use a dedicated backup directory outside `RAG_STATE_DIR`. `--retain-hours 24` keeps more history if preferred (default: 1). The command reuses the backend's installed image and volume, without downloading a new image. It refuses to optimize if writers cannot be stopped or the backup fails. A failure retains the backup and reports a safe error type; inspect service status before retrying.

To schedule maintenance, create `~/.config/proton-rag/maintenance.env`:

```bash
RAG_CONTAINER_ENGINE=docker
RAG_MAINTENANCE_OPTIONS=""
```

For Podman replace those values with:

```bash
RAG_CONTAINER_ENGINE=podman
RAG_MAINTENANCE_OPTIONS="--backend-service proton-rag-anything.service"
```

Then install the timer and service from the repository root:

```bash
chmod 600 "$HOME/.config/proton-rag/maintenance.env"
cp deploy/proton-rag-maintenance.service deploy/proton-rag-maintenance.timer \
  "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable --now proton-rag-maintenance.timer
```

The timer runs about every two hours after the preceding run finishes. View its results with `journalctl --user -u proton-rag-maintenance.service -n 20`. The background daemon guide's stable `app` symlink is required. Never manually delete LanceDB's `_versions`, data, or transaction files while services are running.


## Shared message content and catalog upgrades

`index_status` now reports `unique_messages_indexed` separately from `folder_entries_indexed`. The latter includes each folder and label appearance; `shared_folder_entries` is their difference. Folder progress still counts memberships so coverage can be checked for each folder. `unique_messages_expected` is null while content remains unread, and becomes the observed unique total when scope coverage is complete. `pending_duplicate_cleanup` counts obsolete remote copies awaiting removal; uncertain uploads without any current membership appear as `unresolved_orphan_uploads` and retain their recovery record.

Existing folder-aware catalogs migrate atomically to shared content on startup. An embedded copy is retained, bodies are consolidated, and redundant remote objects are removed in bounded, retryable batches without re-embedding the retained copy. Old message IDs and current membership citations resolve through aliases. Pending uploads preserve their dispatched identity; a missing server object does not authorize a blind retry.

Before upgrading, stop the indexer and make a consistent catalog/backend backup as described above. The migration also keeps a private `catalog.pre-dedup.db` beside the catalog for recovery. If an older indexer is still running, a new MCP process refuses migration rather than changing its schema underneath it. Restart the indexer on the upgraded code, then restart MCP servers in connected clients. Keep the pre-upgrade backend volume snapshot with the old catalog if rolling back to an earlier application version.
