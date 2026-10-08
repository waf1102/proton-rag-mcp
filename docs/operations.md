# Debian operation

## Installed ownership

The runtime is owned by `paperclip` (UID 1001), rootlessly, on Debian 13.7. Existing `rag-network`, Bridge configuration and unrelated services are preserved. Container images are pinned by digest in `deploy/`. Ollama 0.6.8 was selected after the current image download exceeded the VM's 2.9 GiB temporary filesystem; only the `nomic-embed-text` 137M embedding model is installed. Revisit the pinned version through an explicit tested upgrade, not an automatic pull.

The account home is `/home/paperclip`. Agent runs may have a different temporary home: do not infer runtime paths from their `~`. User service access in this environment requires `XDG_RUNTIME_DIR=/run/user/1001` and `DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1001/bus`.

| Unit | Purpose | Exposure | Limits |
|---|---|---|---|
| `proton-rag-ollama.service` | embeddings | host `127.0.0.1:11434` | 1000 MiB, 1 CPU |
| `proton-rag-anything.service` | processed documents, LanceDB, vector search | host `127.0.0.1:3001` | 1200 MiB, 1 CPU |
| `proton-rag-daemon.service` | synthetic fixture sync | no listener | 700 MiB, 1 CPU |

AnythingLLM and Ollama communicate over the existing rootless `rag-network`; container loopback is not used for that connection. The daemon runs on the host. Its IMAP adapter uses host `127.0.0.1:1143` and STARTTLS. Bridge was checked for transport reachability only, never reconfigured or restarted.

## Reproduce installation

1. Use an account with rootless Podman and a working user systemd manager. Install Python >=3.11 and `uv`. Run `uv sync --locked` in the checkout. Set a stable app symlink at `~/.local/share/proton-rag/app` to that checkout; never overwrite another installation silently.
2. Copy the two `.volume` and two `.container` files from `deploy/` into the account's `~/.config/containers/systemd/`. They refer to the existing `rag-network.network`; on a new host create the same named network Quadlet. Do not alter an existing Bridge network.
3. Create mode-0700 configuration/state directories `~/.config/proton-rag` and `~/.local/share/proton-rag`. Create mode-0600 `anything.env` with randomly generated `AUTH_TOKEN` and `JWT_SECRET`; never commit them. The UI password must remain enabled.
4. Reload the user manager and start only `proton-rag-ollama` and `proton-rag-anything`. Their Quadlet `[Install]` sections provide user default-target dependencies. Pull `nomic-embed-text` using `podman exec proton-rag-ollama ollama pull nomic-embed-text`. Expected tested digest: `0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f`.
5. Through AnythingLLM's authenticated API or authenticated UI, generate a dedicated API key. Store it in mode-0600 `daemon.env` as `ANYTHING_API_KEY`; also set `RAG_STATE_DIR` to the account's absolute persistent state directory and `ANYTHING_URL=http://127.0.0.1:3001`. The program never logs it. Run `scripts/bootstrap-workspace.py` with that environment to create only the dedicated `proton-test` workspace.
6. Before starting the daemon, run `scripts/live-synthetic.py` with the same environment. It checks the actual ingestion/search/delete API and creates the synthetic fixture. Do not run it concurrently with the daemon or against a catalog containing private mail.
7. Install `deploy/proton-rag-daemon.service` into `~/.config/systemd/user/`, reload, then `systemctl --user enable --now proton-rag-daemon.service`. This checked-in unit explicitly selects synthetic fixtures. The file lock rejects duplicate daemon processes. Backoff is 2–60 seconds; logs contain only event names, counts and timing.

The installed service points to the task worktree through the stable symlink. Update that symlink only as an authorized deployment after review; this task does not merge or release. Keep the worktree available while the service uses it.

## Private-mail readiness

Private ingestion requires a separate privacy decision and verified storage controls. It is disabled unless `RAG_ENABLE_PRIVATE_INGESTION=1`. Remove the synthetic argument only after that decision, inject `IMAP_USER`, `IMAP_PASS`, and a verified Bridge CA file through the secure runtime, and use a separate catalog/workspace when changing datasets. The production TLS adapter validates chain and hostname; it offers no insecure verification switch. Check the certificate's host SAN before enabling. The discovery handshake used no authentication and was explicitly unverified to record a fingerprint; it is not a trust decision.

The mailbox is hardcoded to exact `Folders/test`, selected read-only with `BODY.PEEK[]`. The adapter performs complete UID inventories, count/UID consistency checks and size checks. It bounds snapshots to 2000 messages/64 MiB and each message to 8 MiB. A changing or failed inventory aborts reconciliation; it cannot turn an IMAP outage into a purge. Unsupported, oversized or empty content has no vectors. Attachments are parsed in a disposable process, with 5 CPU seconds, 8 wall seconds and 512 MiB address-space bounds; DOCX expansion is capped at 16 MiB, PDF at 100 pages and MIME at 100 parts.

## Optional paid generation

Model: `openai/gpt-4.1-mini`. Public pricing observed on 2026-10-08: $0.40/M input, $1.60/M output tokens. Before **each** paid call, the code verifies known current pricing against routing ceilings of $1/M input and $4/M output. It restricts output to 512 tokens, sends no tools/plugins and disables provider fallback. Input reservations count UTF-8 bytes plus 4096 template tokens, then double the combined estimate for margin. No automatic retry is made.

Initialize the ledger once, before the first ever paid request, with `Ledger(<persistent-state>/budget.db, initialize=True)`. Never recreate, delete, relocate or initialize another ledger after spending begins. Production MCP startup fails if the ledger is missing. Every MCP process must use this same persistent ledger. SQLite immediate transactions serialize concurrent requests. Failed/cancelled calls keep reservations, including across UTC midnight; known responses retain conservative daily debits. An unexpected overrun latches generation closed for operator reconciliation. This accounts for application calls, not other applications sharing a provider key.

Do not inject the OpenRouter key into AnythingLLM. No granted key was available in this run, so provider-side key limits, billed usage and paid smoke tests remain unverified. Use a dedicated provider key with a $5/day provider-side limit where supported. Application UTC reset may differ from provider accounting. Never reconcile an uncertain debit downward without provider evidence. The runtime permits only synthetic snippets; enabling private cloud generation requires an explicit implementation change following owner authorization.

## Restart and troubleshooting

Use `systemctl --user status` and content-free daemon journal entries. Restart only the three named RAG units. A dependency restart stops the daemon through `Requires`; start it again after dependencies if your systemd restart ordering leaves it stopped. Readiness is retried rather than assumed from unit activation. Tests restarted the applications, started the daemon, and then queried persistent vectors from both clients successfully.

`sync_failed` never logs exception text or mail. Investigate status/health, disk/memory limits and local connectivity without dumping keys, email, HTTP bodies or entire container environments. Avoid concurrent writers. The single writer assumption is part of the idempotency contract; direct adapter callers must hold the same lock.

Real reboot, login/linger, encrypted unlock and backups were not tested or modified. Boot readiness means user-target configuration was installed and application lifecycle tests passed, not proof of unattended host boot.

An ambiguous raw-text upload is never blindly replayed: recovery first locates the stable title in AnythingLLM. If no matching document is visible, the daemon fails closed pending reconciliation because the collector could still commit a timed-out request. An operator must verify that no request is still running and no document exists before clearing that specific pending intent. Do not reset the whole catalog. This can also require reconciliation after a crash immediately before upload; safety takes precedence over automatic progress in that narrow window.
