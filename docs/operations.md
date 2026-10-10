# Operations

## Configuration

The daemon and MCP server share a SQLite catalog and Qdrant collection. Use separate state directories and collections for separate accounts and fixture tests.

| Variable | Default / purpose |
| --- | --- |
| `RAG_STATE_DIR` | `~/.local/share/proton-rag/mail`; catalog and commit manifests |
| `QDRANT_URL`, `QDRANT_COLLECTION` | `http://127.0.0.1:6333`, `proton-mail` |
| `QDRANT_API_KEY` | Same private key as server `QDRANT__SERVICE__API_KEY` |
| `OLLAMA_URL` | `http://127.0.0.1:11434`; host loopback only |
| `RAG_EMBEDDING_MODEL`, `RAG_EMBEDDING_DIMENSION` | `nomic-embed-text`, `768` |
| `RAG_EMBEDDING_CONTEXT` | `2048`; never silently truncate new embedding input |
| `RAG_CHUNK_CHARS`, `RAG_CHUNK_OVERLAP` | `1800`, `200`; context errors cause further splitting |
| `IMAP_HOST`, `IMAP_PORT` | `127.0.0.1`, `1143` |
| `IMAP_USER`, `IMAP_PASS` | Bridge credentials; daemon only |
| `IMAP_CA_FILE` | Trusted Bridge certificate; system trust if omitted |
| `RAG_FOLDERS`, `RAG_EXCLUDED_FOLDERS` | JSON arrays; unset includes all selectable folders |
| `RAG_POLL_SECONDS`, `RAG_BATCH_SIZE` | `30`, `25` |
| `RAG_EMBEDDING_TIMEOUT` | `300` seconds; connect timeout 10 seconds |
| `RAG_MIN_FREE_BYTES`, `RAG_WARN_FREE_BYTES` | 2 GiB pauses ingestion; 3 GiB warns |
| `RAG_STORAGE_PATHS` | JSON array of additional backend filesystem paths to check |
| `RAG_MAX_MESSAGE_BYTES` | `33554432`; larger MIME bodies skipped |
| `RAG_PARSER_TIMEOUT` | `15` seconds |
| `RAG_MAX_TEXT_CHARS`, `RAG_MAX_PARTS` | `200000`, `200`; extraction bounds |
| `RAG_SEARCH_DEFAULT`, `RAG_SEARCH_MAX` | `10`, `50` |
| `RAG_EXCERPT_CHARS`, `RAG_ANSWER_SOURCES` | `4000`, `5` |
| `OPENROUTER_API_KEY` | Optional; enables `answer_mail` |
| `OPENROUTER_MODEL`, `RAG_MAX_OUTPUT_TOKENS` | `openai/gpt-4.1-mini`, `1024` |

Folder names are exact decoded Unicode names; exclusions take precedence. The model digest, dimension, prefixes, normalization and context are recorded in an immutable profile. A changed model or mismatched collection refuses to run; migrate into a separate target to change profiles.

## Background service

Install with `uv sync --locked`, and point `~/.local/share/proton-rag/app` at the chosen checkout. Start [Compose](../README.md#2-start-the-local-index) or the [Podman services](podman.md). Store ingestion configuration in mode-0600 `~/.config/proton-rag/mail.env`; keep its parent private. The daemon needs no OpenRouter credential.

For Podman, copy `deploy/proton-rag-daemon.service` to `~/.config/systemd/user/`:

```sh
systemctl --user daemon-reload
systemctl --user enable --now proton-rag-daemon.service
systemctl --user status proton-rag-daemon.service
journalctl --user -u proton-rag-daemon.service -n 30
```

For Docker, remove the Podman `After`/`Requires` dependencies in a user override and start Compose first. Configure linger and encrypted-storage unlock separately for unattended boot. Fresh catalogs initialize automatically; nonempty unbound catalogs require [migration](operations-qdrant-migration.md).

## Synchronization and recovery

Only one daemon may write a catalog. Read-only IMAP selection and `BODY.PEEK[]` preserve mailbox flags, folders and content. Folder entries retain UIDVALIDITY/UID identity; identical full MIME hashes share one cached message and index. Existing folder entries are not downloaded again. A newly observed copy needs a read-only fetch to establish its content hash, then reuses the cache and vectors.

Each message has a durable manifest of deterministic chunk IDs. Upserts wait for Qdrant acknowledgement, verify the complete chunk set, and then mark the manifest ready. A lost response or partial batch can be replayed without duplicate points. Search combines dense and local BM25 ranking, groups by message, and accepts only committed points with active catalog memberships.

Failed or unstable folder inventories cannot purge indexed mail. Removing a configured folder leaves its existing content until reconciliation. Content loses search visibility after its last active membership disappears; physical index cleanup waits another complete stable cycle so moves can be discovered. Logical cleanup does not erase backups or old storage pages.

The daemon gets seven minutes for graceful shutdown. Increase `TimeoutStopSec` if increasing embedding timeouts. Logs contain counts and safe error codes, without mail bodies, server response bodies or credentials. Parser failures remain pending without starving unrelated messages.

## Full-message reading and coverage

`search_mail` returns a stable `message_id`, metadata, known folder locations and citations. `read_mail` serves the stored extracted body and supported attachment text; follow `next_offset` until null. Parser truncation and skipped parts are reported separately. No raw MIME or binary attachment is exposed.

`index_status` separates unique messages, folder appearances, cached text, pending ingestion and per-folder coverage. It also reports `index_backend`, `index_ready_messages`, `index_pending_messages` and `embedding_profile`. Backend readiness describes imported data; it does not mean the mailbox is fully indexed. `unique_messages_expected` remains unknown until unread content has been observed. Search hit counts are relevance limits, not exhaustive mailbox counts.

## Index health

The `runtime` status records observed state, free space, recent progress and a redacted last error. Its timestamp is not proof the process is currently running; check systemd too. Errors retain timestamps after recovery. Below the disk pause threshold ingestion waits and retries without deleting mail; cached reading and search remain available. Configure `RAG_STORAGE_PATHS` if Qdrant lives on a different filesystem.

Qdrant performs its own segment optimization; no offline table-history compaction timer is required. Never delete its storage files while running.

## Backups and restore verification

With your private configuration loaded, create a paired recovery snapshot:

```sh
uv run proton-rag-backup backup \
  --backup-dir "$HOME/.local/share/proton-rag/backups"
```

The command checks and pauses the managed daemon if active, obtains writer and maintenance locks, backs up SQLite using its backup API, and downloads a Qdrant collection snapshot. It records SHA-256 checksums and the profile/binding in a private manifest. `.complete` is written only after verification. The writer resumes even if backup fails; an initially stopped writer stays stopped. Only this command's temporary server snapshot is removed after download. Private local backups are retained; manage retention after testing recovery. Use a directory outside active state, and allow room for a snapshot on both filesystems.

Verify recovery into a fresh directory and distinct, absent collection:

```sh
uv run proton-rag-backup restore-check --backup-dir /absolute/path/to/completed-backup \
  --target-state /absolute/path/to/fresh-restore-state --collection restore-check-20261009
```

This checks both files, restores with snapshot priority, checks original ownership, assigns the isolated target binding, and verifies every cached message's complete points. It never overwrites an existing collection or state directory. Inspect the report before deleting the test collection/directory. Restore using the pinned Qdrant minor version; retain the Ollama model volume and private runtime configuration separately. [Qdrant snapshot semantics](https://qdrant.tech/documentation/operations/snapshots/).

For application upgrades, stop ingestion, create a verified pair, change the app symlink and restart the writer and MCP clients. Preserve the pre-upgrade pair for rollback. Do not run two versions against the same catalog.

## Optional fixture validation

Use a fresh `RAG_STATE_DIR` and `QDRANT_COLLECTION=proton-fixtures`, then run `uv run python scripts/live-synthetic.py`. It uses local embeddings, checks ingestion/search/deletion, and refuses mixed private/synthetic catalogs. No IMAP or paid API calls occur. Do not run a daemon concurrently against that fixture state.
