# Proton RAG MCP

**Search your Proton Mail from an AI assistant, with retrieval running locally.**

Proton RAG MCP turns messages and supported attachments across Proton Mail's selectable folders into a searchable index. It connects through Proton Mail Bridge, extracts text in Python, stores documents and vectors in AnythingLLM, and uses Ollama for embeddings. An MCP server lets compatible assistants retrieve short excerpts with citations back to the source message.

- **Local search:** mail indexing and retrieval stay on your machine.
- **Read-only mail access:** messages are not moved, deleted, or marked as read.
- **Attachment search:** supported PDF and DOCX text is indexed alongside message bodies.
- **Cited results:** each excerpt includes a source identifier so results can be traced.

> **How your data is used:** indexing and search run locally. Optional `answer_mail` sends your query and selected mail excerpts to OpenRouter for a cited answer. Ollama supplies embeddings; no local chat model is required.

## Get started

You need a Linux host, Python 3.11 or newer, [uv](https://docs.astral.sh/uv/), and Docker or rootless Podman. Allow roughly 3 GiB of memory for the configured services, plus space for container images, the embedding model, and indexed data. A configured Proton Mail Bridge and its IMAP credentials and trusted certificate are required.

```sh
git clone https://github.com/waf1102/proton-rag-mcp.git
cd proton-rag-mcp
uv sync --locked
```

Choose a container runtime below. Both keep the service ports on host loopback and use persistent named volumes. The Python application runs on the host.

<details>
<summary><strong>Docker setup</strong></summary>

These commands translate the checked-in Podman container settings to Docker. Docker deployment has not been validated by this project; the tested deployment uses Podman.

Create a private configuration file for AnythingLLM:

```sh
umask 077
mkdir -p "$HOME/.config/proton-rag"
if [ ! -e "$HOME/.config/proton-rag/anything.env" ]; then
  printf 'AUTH_TOKEN=%s\nJWT_SECRET=%s\n' "$(openssl rand -hex 32)" "$(openssl rand -hex 32)" \
    > "$HOME/.config/proton-rag/anything.env"
fi
docker network create proton-rag
```

On an existing installation, reuse the configuration file instead of generating new secrets.

```sh
docker run -d --name proton-rag-ollama --restart unless-stopped \
  --network proton-rag --memory=1000m --cpus=1 \
  -p 127.0.0.1:11434:11434 -v proton-rag-ollama:/root/.ollama \
  -e OLLAMA_NUM_PARALLEL=1 -e OLLAMA_MAX_LOADED_MODELS=1 -e OLLAMA_KEEP_ALIVE=2m \
  ollama/ollama@sha256:6697852aa235e8fb4e1e77cfab9bbf70d51d3760cb0d266e9fb43cf2b8e44f66
```

Use the exact Ollama image digest from [`deploy/proton-rag-ollama.container`](deploy/proton-rag-ollama.container) if updating the pinned version.

```sh
docker exec proton-rag-ollama ollama pull nomic-embed-text
docker run -d --name proton-rag-anything --restart unless-stopped \
  --network proton-rag --memory=1200m --cpus=1 \
  -p 127.0.0.1:3001:3001 -v proton-rag-anything:/app/server/storage \
  --env-file "$HOME/.config/proton-rag/anything.env" \
  -e STORAGE_DIR=/app/server/storage -e SERVER_PORT=3001 -e DISABLE_TELEMETRY=true \
  -e LLM_PROVIDER=ollama -e OLLAMA_BASE_PATH=http://proton-rag-ollama:11434 \
  -e OLLAMA_MODEL_PREF=nomic-embed-text -e EMBEDDING_ENGINE=ollama \
  -e EMBEDDING_BASE_PATH=http://proton-rag-ollama:11434 \
  -e EMBEDDING_MODEL_PREF=nomic-embed-text -e EMBEDDING_MODEL_MAX_CHUNK_LENGTH=2048 \
  -e VECTOR_DB=lancedb \
  mintplexlabs/anythingllm@sha256:5ce7b65badd7de94827846d33fe1b38eff71f86875b7ce88d6e56d2371ec2d6b
```

Wait for AnythingLLM at `http://127.0.0.1:3001`. Use `docker logs proton-rag-anything` to check startup. Stop the containers with `docker stop proton-rag-anything proton-rag-ollama`; named volumes retain the index and model.

</details>

<details>
<summary><strong>Docker Compose setup</strong></summary>

Use Docker with the Compose plugin and the checked-in [`deploy/compose.yaml`](deploy/compose.yaml). Choose this **instead of** the standalone Docker commands above; both use the same container names and persistent volumes. Compose configuration is validated, but Docker deployment has not been exercised.

From the checkout, create the private configuration file if it does not already exist:

```sh
umask 077
mkdir -p "$HOME/.config/proton-rag"
if [ ! -e "$HOME/.config/proton-rag/anything.env" ]; then
  printf 'AUTH_TOKEN=%s\nJWT_SECRET=%s\n' "$(openssl rand -hex 32)" "$(openssl rand -hex 32)" \
    > "$HOME/.config/proton-rag/anything.env"
fi
```

Start Ollama, download the embedding model, then start AnythingLLM:

```sh
docker compose -f deploy/compose.yaml up -d --wait ollama
docker compose -f deploy/compose.yaml exec ollama ollama pull nomic-embed-text
docker compose -f deploy/compose.yaml up -d --wait
```

Open `http://127.0.0.1:3001`, then continue with **Connect your mailbox** below. The Python daemon and MCP server still run on the host.

```sh
docker compose -f deploy/compose.yaml ps
docker compose -f deploy/compose.yaml logs -f anythingllm
docker compose -f deploy/compose.yaml down
```

`down` preserves the named volumes. Adding `--volumes` deletes the stored index and embedding model.

</details>

<details>
<summary><strong>Podman setup (tested deployment)</strong></summary>

Use rootless Podman with Quadlet and a working user systemd manager. Create the private AnythingLLM configuration:

```sh
umask 077
mkdir -p "$HOME/.config/proton-rag" "$HOME/.config/containers/systemd"
if [ ! -e "$HOME/.config/proton-rag/anything.env" ]; then
  printf 'AUTH_TOKEN=%s\nJWT_SECRET=%s\n' "$(openssl rand -hex 32)" "$(openssl rand -hex 32)" \
    > "$HOME/.config/proton-rag/anything.env"
fi
cp deploy/*.container deploy/*.volume "$HOME/.config/containers/systemd/"
```

Reuse existing secrets on an installed system. The Quadlets expect `rag-network.network`. On a new host, create it with:

```sh
printf '[Network]\nNetworkName=rag-network\n' \
  > "$HOME/.config/containers/systemd/rag-network.network"
```

Reuse that network file if it already exists, especially when shared with Bridge.

```sh
systemctl --user daemon-reload
systemctl --user start proton-rag-ollama.service
podman exec proton-rag-ollama ollama pull nomic-embed-text
systemctl --user start proton-rag-anything.service
```

Wait for AnythingLLM at `http://127.0.0.1:3001`. Check startup with `systemctl --user status proton-rag-anything.service`. Stop with `systemctl --user stop proton-rag-anything.service proton-rag-ollama.service`; named volumes retain data.

For the background Python service and host-specific setup, see [Operations](docs/operations.md#reproduce-installation) and the [Fedora guide](docs/fedora.md).

</details>

## Connect your mailbox

Open AnythingLLM at `http://127.0.0.1:3001`, complete its authenticated setup, and create an API key in its settings. From the checkout, configure the application using environment variables:

```sh
umask 077
export RAG_STATE_DIR="$HOME/.local/share/proton-rag/mail"
export ANYTHING_URL=http://127.0.0.1:3001
export ANYTHING_WORKSPACE=proton-mail
export IMAP_HOST=127.0.0.1
export IMAP_PORT=1143
export IMAP_CA_FILE=/absolute/path/to/bridge-certificate.pem
read -rp 'Bridge IMAP username: ' IMAP_USER
read -rsp 'Bridge IMAP password: ' IMAP_PASS; echo
read -rsp 'AnythingLLM API key: ' ANYTHING_API_KEY; echo
export IMAP_USER IMAP_PASS ANYTHING_API_KEY
mkdir -p "$RAG_STATE_DIR"
uv run python scripts/bootstrap-workspace.py
uv run proton-rag --once
```

Use the credentials generated by **Bridge**, not your Proton account password. Trust Bridge's certificate explicitly; certificate and hostname checks stay enabled.

All selectable folders are indexed by default. For a smaller scope, set a JSON folder list such as `RAG_FOLDERS='["INBOX", "Folders/Work"]'`, or exclude folders with `RAG_EXCLUDED_FOLDERS='["Trash", "Spam"]'`. Reads do not change mailbox flags. The first import may take time; later runs fetch only newly encountered messages. Interrupted runs resume from the catalog.

Run `uv run proton-rag` for continuous synchronization. For the background service, see [Operations](docs/operations.md).

## Connect your AI assistant

Add a **stdio MCP server** to a client that supports local server processes:

| Setting | Value |
| --- | --- |
| Command | Absolute path to this checkout's `.venv/bin/proton-rag-mcp` |
| Environment | `RAG_STATE_DIR`, `ANYTHING_API_KEY`, `ANYTHING_URL`, and `ANYTHING_WORKSPACE` from setup |
| Optional answers | Supply `OPENROUTER_API_KEY`; choose a model with `OPENROUTER_MODEL` |
| Transport | stdio; no HTTP endpoint is provided |

Ask the assistant to use **`search_mail`** for a question such as “Find my latest invoice.” Results include excerpts, sender, subject, date, attachment names, and folder locations. Citations identify indexed messages; they are not download links.

With an OpenRouter key configured, **`answer_mail`** answers a question using selected excerpts and returns citations. Provider charges apply. The model defaults to `openai/gpt-4.1-mini` and is configurable. Keep credentials in your client's secure environment, outside committed configuration.

Search returns 10 results by default; the configurable maximum is 50. Excerpts default to 4,000 characters, and answers use up to five excerpts with a 1,024-token output limit. See [configuration](docs/operations.md#configuration) for overrides.

## Development and further reading

```sh
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
npm ci --ignore-scripts
npm run test:interop
```

[Operations & troubleshooting](docs/operations.md) · [Security & data storage](docs/security.md) · [Fedora migration](docs/fedora.md) · [Validation evidence](docs/validation.md)
