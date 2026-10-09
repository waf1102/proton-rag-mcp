# Fedora deployment

Use rootless Podman, Quadlet, Python 3.11+ and uv under a dedicated user with a working systemd user manager. Keep SELinux enforcing.

- Install the repository and follow the Podman setup in the README. Named volumes receive Podman's labels; use deliberate `:Z`/`:z` labels if replacing them with bind mounts.
- Configure Proton Mail Bridge and its keyring for that account. Validate the actual host mapping and certificate hostname before enabling ingestion.
- Place persistent index data, Bridge state, swap and backups on appropriately protected storage. Verify encryption; do not infer it from directory names.
- Configure `mail.env`, the stable app symlink, and the daemon unit as described in Operations. Validate the same state/workspace settings in MCP clients.
- Configure linger and any encrypted-storage unlock dependencies for unattended boot. Test a real reboot before claiming boot readiness.
- Migrate a consistent catalog and AnythingLLM backup together, or rebuild the index from mail. Do not run two daemons against the same catalog.

Fedora-specific deployment and reboot validation must be performed on the target host; passing application checks on Debian does not establish those outcomes.
