# Boundaries and persistence

## Mail and data paths

No mailbox mutation API exists. Only exact `Folders/test` can be selected by the ingestion adapter. No INBOX access/copy or live mail test was performed. Live copy remains deferred because source preservation and privacy permission have not been demonstrated. The original owner's daemon file is untouched; its unsafe default fetch/logging behavior is replaced by the new owned service.

MIME bytes and attachments enter an isolated parser in memory. External parsing libraries cannot write to stdout/stderr. Text, PDF, DOCX and CSV are supported with bounds; images, archives, executables and unsupported MIME types are skipped. No OCR, external links, macros, embedded scripts or attachment execution occurs. No raw private `.eml`, PDF or DOCX staging is required. Synthetic fixtures intentionally persist as `.eml` test data.

AnythingLLM's supported `/api/v1/document/raw-text` API persists the processed text as document JSON. It also writes vector caches, LanceDB and SQLite. This is **not** memory-only end-to-end storage. Actual API behavior was validated on installed 1.17.0, rather than inferred from arbitrary JSON chunks. Stable titles/docSource IDs combine UIDVALIDITY, UID and content SHA256. The intent journal precedes upload; repeated ingestion recovers stored documents by that identity. Removing a message hides its citation, removes workspace embeddings, calls `system/remove-documents` to purge source/cache, and removes the mapping. This is logical deletion, not forensic erasure from storage snapshots or old SQLite pages.

| Data | Persistent location |
|---|---|
| Existing Bridge account/session/mail cache | existing `proton-bridge-data` rootless volume; untouched |
| AnythingLLM extracted text | `proton-rag-anything` volume, `/app/server/storage/documents` |
| Embeddings and local index | same volume, `vector-cache`, `lancedb` |
| AnythingLLM metadata/API keys | same volume, `anythingllm.db` and application settings |
| Embedding model | `proton-rag-ollama` volume, `/root/.ollama` |
| Daemon identities and spend reservations | `~/.local/share/proton-rag/catalog.db`, `budget.db` |
| Synthetic fixtures | `~/.local/share/proton-rag/fixtures` |
| Runtime secrets | mode-0600 `~/.config/proton-rag/*.env`; secret proposals registered |
| Diagnostics | user journal, Podman/application logs; daemon excludes mail and credentials |
| Backups | none created or configured by this task; existing host backup policy unverified |

Host block inspection showed ext4 on `/dev/vda1` and a swap partition, with no visible LUKS device. Host/provider encryption is **unverified**, not guaranteed; swap may contain process memory. No storage/unlock changes were authorized. Keep private ingestion disabled pending appropriate controls. Container root is namespaced under a rootless account, not host root.

## Outbound paths

Local ingestion calls AnythingLLM over loopback; AnythingLLM requests local Ollama embeddings over the rootless network. MCP local search invokes only the vector-search route. No generation model is installed and AnythingLLM has no cloud key. Telemetry is disabled for the AnythingLLM instance. Image/model installation required external registries; future upgrades would too.

Optional `answer_synthetic` fetches public model pricing and sends a bounded query plus at most three 2000-character synthetic excerpts to `openrouter.ai`. Provider routing requests `data_collection=deny`; this is a routing constraint, not a verified zero-retention guarantee. The client receives an explicit cloud-tool description. Prompt-injection defenses treat retrieved content as untrusted data and expose no downstream action tools. These controls cannot guarantee the model ignores every malicious instruction; they bound the consequences and external payload.

Clients receive email excerpts by design. Only authorized local clients should receive the AnythingLLM key and filesystem access. Stdio inherits the launching user's privileges and has no additional authentication. Host loopback is not an isolation boundary between hostile local users. Do not expose these ports through a proxy without separately designing authentication.

## Sources used to verify integration

- [AnythingLLM raw-text and document endpoints](https://github.com/Mintplex-Labs/anything-llm/blob/master/server/endpoints/api/document/index.js)
- [AnythingLLM workspace embeddings and vector-search](https://github.com/Mintplex-Labs/anything-llm/blob/master/server/endpoints/api/workspace/index.js)
- [AnythingLLM source/cache/vector purge](https://github.com/Mintplex-Labs/anything-llm/blob/master/server/utils/files/purgeDocument.js)
- [Official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [Ollama embeddings](https://github.com/ollama/ollama/blob/main/docs/capabilities/embeddings.mdx)
- [OpenRouter provider routing](https://openrouter.ai/docs/guides/routing/provider-selection)

The installed-image API tests are the acceptance evidence; mutable upstream source links explain the chosen interfaces.
