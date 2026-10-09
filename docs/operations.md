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
| `RAG_BATCH_SIZE` | `25`; bounds work between cancellation checks |
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

Each catalog has a persistent document namespace and is bound to its workspace; switching that workspace requires a new catalog. Each folder has its own UIDVALIDITY/UID identity. The daemon fetches new messages with `BODY.PEEK[]`; existing indexed messages are not downloaded each poll. Newest messages are processed first within each folder. It never writes to IMAP. Deletions from the local index occur only after a complete, stable inventory of that folder. Unavailable or disappeared folders keep their indexed data until explicitly reconciled; removing a folder from configuration does not erase its existing index.

Copies in multiple folders retain their locations. Search suppresses duplicate results when Message-ID and content digest match. Missing Message-ID or differing content can produce separate results. Metadata and attachments remain associated with each indexed message.

Only one daemon may write a catalog. Logs record events and counts rather than message contents. A failed connection or incomplete inventory must not become an index purge. Oversized and unsupported content is skipped within configured limits. A failed parser or unresolved upload is reported without starving the remaining messages; deletion reconciliation waits until those failures are resolved. Do not increase parser limits without considering host memory.

A crash after upload dispatch can leave an uncertain upload. Recovery checks for the stable document identity before retrying. If no document is visible, it stops that message for reconciliation rather than duplicating a potentially in-flight upload. Verify the remote request has stopped and inspect the specific pending catalog entry before any repair; never reset the whole catalog as a retry.

## Upgrades and backups

Use a separate workspace and state directory when moving from the old single-folder prototype. Its catalog is rejected with an explicit migration message; retain it for rollback and build a fresh index from the read-only mailbox. Client configurations must switch to the new catalog and workspace together.

For a consistent backup, stop the daemon and AnythingLLM, preserve the catalog and AnythingLLM volume together, then restart the services. Keep the model volume to avoid downloading embeddings again. Backups contain readable mail-derived data and need the same storage protection as the index.

## Optional fixture validation

Fixtures are isolated from production. Use a fresh state directory and `ANYTHING_WORKSPACE=proton-fixtures`; run bootstrap, then `scripts/live-synthetic.py`. That script exercises ingestion/search/deletion and refuses other workspace names. It must not run concurrently with a daemon writing the same catalog.
