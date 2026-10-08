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
