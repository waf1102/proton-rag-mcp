# Validation evidence — FLO-38

Validated on the assigned Debian VM, 2026-10-08 UTC. All message data used for testing was synthetic. No INBOX/mailbox reads, copy, writes or real-mail cloud calls occurred.

## Software and transport

- Python 3.13.5; MCP Python SDK 1.30.0; TypeScript SDK 1.32.1; httpx 0.28.1; pypdf 6.19.0; python-docx 1.2.0.
- AnythingLLM 1.17.0, image digest in its Quadlet. Ollama 0.6.8, pinned digest in its Quadlet; `nomic-embed-text` model digest in operations guide. No generation model installed.
- MCP stdio negotiation selected `2025-11-25`. Official Python and TypeScript clients both discovered schemas, searched the real local index and received a cited Tuesday fixture result. Invalid arguments returned tool errors. A raw JSON-RPC pytest harness checks unsupported-version negotiation, discovery, schema limits, unknown method errors and cancellation. The synthetic backend writes a test marker on cancellation, proving it actually stopped.
- SDK limitation: unknown method returns JSON-RPC `-32602` (SDK request-union validation), not `-32601`. The harness accepts and records this implementation behavior. No claim of interoperability with every historical client or HTTP transport.

## Automated checks

Commands: `uv sync --locked`, `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`, `npm ci --ignore-scripts`, `npm run test:interop`.

Result: **37 tests passed**, Ruff lint/format passed, and TypeScript fixture interoperability passed.

Coverage includes exact-folder rejection before network execution; read-only selection and BODY.PEEK; partial/changed inventories; unsupported and malformed attachments; real text/CSV/DOCX/PDF extraction; HTML script exclusion; size bounds; upload retry/crash recovery; UIDVALIDITY changes; deletion and disconnected/partial inventory safety; ambiguous-upload refusal; parser-failure preservation; dataset isolation; missing/invalid budget state; concurrent reservations; midnight rollover; unknown usage and retries; pricing unknown/exhausted budget; private cloud-context rejection; outbound request limits; and MCP client/protocol checks.

## Actual local API and service evidence

`uv run python scripts/live-synthetic.py` used the installed AnythingLLM/Ollama APIs:

- Two synthetic mail messages ingested in **2.789 s**.
- Correct synthetic source retrieved in **0.123 s** on the warm query.
- Repeating ingestion with a reopened catalog retained exactly the same document mappings.
- Removing the zinc fixture removed its stored document and its vector-search result, preserving cobalt.
- Daemon started from stopped under a named user service; log showed `sync_ok` for one message.
- Restarted only AnythingLLM/Ollama application services, restarted daemon, then reran both SDK clients successfully against persistent vectors. No shared-VM reboot or unrelated service restart.
- Duplicate daemon protection uses a nonblocking flock on the shared state directory. Real Bridge outage was not induced; snapshot/fetch failure fixtures cover outage behavior without touching Bridge.

### Measured resources (small synthetic dataset)

After a post-restart query: AnythingLLM **345.5 MB**, loaded Ollama **416.5 MB** (Podman memory usage); daemon user cgroup approximately **18.9 MB**. Limits: 1200/1000/700 MiB respectively and 1 CPU each. These are point-in-time measurements, not capacity estimates.

Processed documents **12 KiB**, LanceDB **60 KiB**, vector cache **16 KiB**, AnythingLLM SQLite **344 KiB**, embedding model **274,302,450 bytes**. Container image storage is additional. The repository's original 13,000-mail/boot-time estimates remain unverified.

Both application host listeners were verified at **127.0.0.1** only (3001 and 11434); existing Bridge remains 127.0.0.1:1143. Rootless ownership is UID 1001. Discovery-only STARTTLS handshake reached TLS 1.3 without authentication. Certificate fingerprint recorded during discovery: `726bf839766dcf9f3ca757c4c9171e5b210c0ce2615478168ddc2c3a9f5622dc`; trust/hostname verification for live use remains unvalidated.

## Delivery blocker

The authorized gateway still rejected branch push with HTTP 403 on the 09:33 UTC continuation. The saved origin is SSH, but run-injected `url.https://github.com/.insteadof` rules rewrite SSH to HTTPS. The installed Paperclip GitHub connection independently rejected branch creation with `403 Resource not accessible by integration`. Remote branch listing contains only main. No PR, alternate identity, merge or release. Gateway write access remains the delivery blocker.

## Explicit gaps

- **Paid OpenRouter smoke test:** passed on the 09:33 UTC continuation using the granted secret API without exposing or persisting the key. One synthetic cobalt query returned Tuesday and the correct synthetic citation using `openai/gpt-4.1-mini`. Provider-reported usage cost: **$0.000064**; shared persistent ledger conservative debit: **$0.013234**, below $5/day. Live model pricing passed the implementation ceilings ($1/M input, $4/M output). The key metadata returned `limit`, `limit_reset`, and `limit_remaining` as null: no provider-side key cap is configured. Application accounting does not cover other users of this key; invoice reconciliation remains unverified.
- **Live mailbox/usefulness/copy tests:** deferred under the privacy restriction. Copy functionality is deliberately absent; source preservation is not claimed proven. Private ingestion remains disabled.
- **Encryption:** ext4 root and swap observed, no visible LUKS layer. Host/provider encryption, backup and secure-erasure behavior are not verified. No host storage changes made.
- **Branch protections:** gateway fetch worked; reading `branches/main/protection` returned HTTP 403. Required remote check policy cannot be asserted. The local implementation includes a synthetic CI job; local checks are reported independently.
- **Boot/unlock:** application lifecycle and user-unit configuration validated; actual reboot, linger/unlock behavior and Fedora deployment untested.
- **Ambiguous uploads:** no blind retry when no stable-title match can be found; this narrow crash/timeout window requires reconciliation rather than duplicate creation.
- **Credential handling incident:** the legacy daemon's embedded Bridge credential appeared in inspection tool output due to incomplete redaction. It is excluded from repository/evidence. A secure-secret proposal was registered; Bridge rotation/configuration was not performed because it is outside this task's authority. The owner explicitly directed continuation without treating this incident as a blocker; Bridge setup remains unchanged.

This is reviewable implementation evidence, not a claim that the outstanding private-data, provider or host controls are complete. No merge or release was performed.
