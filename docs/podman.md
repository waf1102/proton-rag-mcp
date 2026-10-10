# Podman setup

Use rootless Podman with Quadlet and a working user systemd manager. Complete the [installation](../README.md#get-started), then create the private `qdrant.env` file from the README and install the Quadlets:

```sh
mkdir -p "$HOME/.config/containers/systemd"
cp deploy/*.container deploy/*.volume "$HOME/.config/containers/systemd/"
```

The Quadlets expect `rag-network.network`. On a new host, create it with:

```sh
printf '[Network]\nNetworkName=rag-network\n' \
  > "$HOME/.config/containers/systemd/rag-network.network"
```

Reuse an existing network file, especially if shared with Bridge.

```sh
systemctl --user daemon-reload
systemctl --user start proton-rag-ollama.service proton-rag-qdrant.service
podman exec proton-rag-ollama ollama pull nomic-embed-text
```

Check startup with `systemctl --user status proton-rag-qdrant.service`. The server listens on loopback port 6333 and requires the key in `qdrant.env`; the daemon and MCP client use that same value as `QDRANT_API_KEY`.

Continue with [connecting your mailbox](../README.md#3-connect-proton-mail). For unattended operation, see [Operations](operations.md#background-service) and [Fedora deployment](fedora.md). Existing installations must follow [Migration](operations-qdrant-migration.md) before starting a new writer against old catalog data.
