# Validation

Run repository checks with:

```sh
uv sync --locked
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
npm ci --ignore-scripts
npm run test:interop
docker compose -f deploy/compose.yaml config --no-env-resolution --quiet
```

Coverage includes folder discovery and encoding, read-only fetches, large UID inventories, incremental synchronization, folder identity isolation, upload recovery, parser failures, attachment extraction, duplicate search locations, configurable cloud answers, and MCP interoperability.

For real-service validation, provision a separate workspace/catalog, verify Bridge TLS trust, inventory folders, index a bounded sample, and compare mailbox flags before and after. Exercise both MCP SDK clients and a cited answer. Check restart persistence before enabling continuous indexing.

Do not put email content, credentials, or provider response bodies in published validation evidence. Record counts, timings, pass/fail results and deployment limitations instead. Paid test limits belong to the external test runner, not application configuration.

## Qdrant validation (2026-10-09)

The isolated pinned Qdrant 1.19.2 server passed real dense/BM25 hybrid retrieval, deterministic retry, scoped deletion, paired snapshot download and restore verification into a distinct collection. Synthetic migration checks cover aliases, missing chunk rebuilds, interrupted imports, changed exports and bounded vector memory. A frozen Lance fixture exported 257 unique rows across three batches.

Runtime tests retain read-only IMAP, folder identity, duplicate message sharing, paging and MCP interoperability checks. New tests reject a changed embedding model, incomplete manifests, corrupt dense vectors, mismatched collection ownership and concurrent maintenance. Integration tests run in CI against the pinned server; local runs without `QDRANT_TEST_URL` skip those service tests explicitly.

For a local pinned server, run:

```sh
QDRANT_TEST_URL=http://127.0.0.1:6333 uv run pytest -q
```

Use an isolated test server. Integration tests create and delete uniquely named fixture collections. The live migration report is separate from fixture validation; full mailbox indexing remains an ongoing process. This VM uses rootless Podman; a full Docker or Fedora deployment and reboot have not been exercised here.
