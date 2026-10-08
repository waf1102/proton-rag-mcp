# Proton RAG MCP

**Search your Proton Mail from an AI assistant, with retrieval running locally.**

Proton RAG MCP turns messages and supported attachments from Proton Mail's `Folders/test` folder into a searchable index. It connects through Proton Mail Bridge, extracts text in Python, stores documents and vectors in AnythingLLM, and uses Ollama for embeddings. An MCP server lets compatible assistants retrieve short excerpts with citations back to the source message.

- **Local search:** mail indexing and retrieval stay on your machine.
- **Read-only mail access:** messages are not moved, deleted, or marked as read.
- **Attachment search:** supported PDF and DOCX text is indexed alongside message bodies.
- **Cited results:** each excerpt includes a source identifier so results can be traced.

> **Current status:** a working prototype with a synthetic sample-mail setup. Private-mail ingestion is disabled by default and needs the Bridge and storage setup described in [Operations](docs/operations.md#private-mail-readiness). Ollama is used for embeddings, not chat responses. Optional paid answers through OpenRouter currently accept synthetic excerpts only.

## Get started

You need a Linux host, Python 3.11 or newer, [uv](https://docs.astral.sh/uv/), and Docker or rootless Podman. Allow roughly 3 GiB of memory for the configured services, plus space for container images, the embedding model, and indexed data. Proton Mail Bridge is needed when you enable real-mail ingestion; it is not needed for the sample below.

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

Open `http://127.0.0.1:3001`, then continue with **Index sample mail** below. The Python daemon and MCP server still run on the host.

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

## Index sample mail

Open AnythingLLM at `http://127.0.0.1:3001`, complete its authenticated setup, and create a dedicated API key in its settings. Use the same key and state directory for the daemon and every MCP client. Keep credentials out of Git and shared client configuration.

In Bash, from the checkout:

```sh
umask 077
export RAG_STATE_DIR="$HOME/.local/share/proton-rag"
mkdir -p "$RAG_STATE_DIR"
export ANYTHING_URL=http://127.0.0.1:3001
read -rsp 'AnythingLLM API key: ' ANYTHING_API_KEY; echo
export ANYTHING_API_KEY
uv run python scripts/bootstrap-workspace.py
uv run python scripts/live-synthetic.py
uv run proton-rag --synthetic-dir "$RAG_STATE_DIR/fixtures" --once
```

This creates the dedicated `proton-test` workspace and indexes sample messages without connecting to your mailbox. To keep the sample index synchronized, run the last command without `--once`. Use a separate catalog/workspace for private mail; do not mix it with the synthetic sample.

## Connect your AI assistant

Add a **stdio MCP server** to a client that supports local server processes:

| Setting | Value |
| --- | --- |
| Command | Absolute path to this checkout's `.venv/bin/proton-rag-mcp` |
| Environment | `RAG_STATE_DIR`, `ANYTHING_API_KEY`, and `ANYTHING_URL` from setup |
| Transport | stdio; no HTTP endpoint is provided |

Ask the assistant to use `search_mail` for a question such as **“When does the cobalt shipment arrive?”** The sample result should mention Tuesday and include an `imap://Folders/test/…` citation. These citations identify messages; they are not download links. Search returns up to five excerpts, each limited to 2,000 characters.

You can also verify the connection directly:

```sh
uv run python scripts/client-python.py
```

Optional `answer_synthetic` generation uses OpenRouter with a shared $5/day application budget. It requires a separately initialized ledger and an explicitly supplied key; see [paid generation setup](docs/operations.md#optional-paid-generation).

## Development and further reading

```sh
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
npm ci --ignore-scripts
npm run test:interop
```

[Operations & troubleshooting](docs/operations.md) · [Security & data storage](docs/security.md) · [Fedora migration](docs/fedora.md) · [Validation evidence](docs/validation.md)
