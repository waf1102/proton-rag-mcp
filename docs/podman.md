# Podman setup

Complete [requirements and installation](../README.md#get-started) first, then run these commands from the repository root instead of the Docker Compose step.

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

Wait for AnythingLLM at `http://127.0.0.1:3001`. Check startup with `systemctl --user status proton-rag-anything.service`.

For the background Python service and host-specific setup, see [Operations](operations.md#background-service) and the [Fedora guide](fedora.md).

Continue with [connecting your mailbox](../README.md#3-connect-proton-mail).
