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

## Recorded validation (2026-10-08)

The production implementation passed 41 unit/integration tests, Ruff checks, and the TypeScript MCP interoperability test. Live validation on the Podman host verified TLS, inventoried 12 Bridge folders, indexed read-only samples from all 11 non-empty folders, and confirmed mailbox flags were unchanged. Both Python and TypeScript SDK clients retrieved cited results. Two OpenRouter answer checks passed with citations matching retrieved sources.

The deployed MCP launcher exposes search and answer tools and successfully retrieves indexed real mail. Ollama and AnythingLLM restart checks passed. Full initial indexing runs in the background; the bounded validation does not claim the entire mailbox has already been indexed. Docker Compose configuration was validated, but full Docker and Fedora deployments were not exercised on this host.

A live daemon interruption exposed AnythingLLM’s `.txt` title suffix; recovery now accepts that collector format. The interrupted upload was recovered without re-uploading, and the updated daemon stopped and restarted cleanly.
