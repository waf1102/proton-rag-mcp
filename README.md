# Proton test-folder RAG and MCP

A rootless, local retrieval prototype for exact `Folders/test`. Python extracts MIME bodies and supported attachments in memory, AnythingLLM 1.17.0 persists processed text and LanceDB vectors, and Ollama supplies **embeddings only**. A standards-based stdio MCP server exposes bounded retrieval with citations. Optional OpenRouter generation has a shared fail-closed $5/day ledger and currently accepts synthetic context only.

The installed daemon runs synthetic fixtures. Private-mail ingestion and cloud exposure are not enabled. No IMAP mutation methods exist: no COPY, MOVE, STORE, DELETE, CLOSE or EXPUNGE. Live source-preserving copy is deferred, not claimed tested.

## Local checks

```sh
uv sync --locked
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
npm ci --ignore-scripts
npm run test:interop
```

See [operation and Debian setup](docs/operations.md), [security and data paths](docs/security.md), [Fedora migration](docs/fedora.md), and [validation evidence](docs/validation.md).

## MCP clients

Configure a client to launch `.venv/bin/proton-rag-mcp` from an installed checkout and securely inject `RAG_STATE_DIR`, `ANYTHING_API_KEY`, and optionally `ANYTHING_URL` (default `http://127.0.0.1:3001`). All clients must share the same absolute state directory. Credentials must not go into committed client configuration.

`search_mail(query, limit=3)` accepts 1–1000 characters and 1–5 results. It returns at most 2000 characters per excerpt with an `imap://Folders/test/<UIDVALIDITY>/<UID>#<SHA256>` citation. The URI is an identity, not an unauthenticated mail-download link. Returned text is untrusted mail data, never instructions.

`answer_synthetic(query)` is advertised only with an explicitly injected `OPENROUTER_API_KEY` and a pre-initialized ledger. It sends the query and up to three selected synthetic excerpts to OpenRouter. It refuses any non-synthetic result. AnythingLLM never receives that key; UI generation cannot bypass accounting. No local generation model is installed.

Only stdio is supported. There is no MCP HTTP listener, remote authentication flow or guarantee for historical clients. Tested clients are the official Python and TypeScript SDKs; the protocol harness tests negotiation, discovery, schema validation, calls, errors and cancellation.
