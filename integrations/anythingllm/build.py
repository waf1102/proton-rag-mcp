#!/usr/bin/env python3
"""Build a reproducible client patch; never modify a running container or its storage."""

import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile

BASE = "docker.io/mintplexlabs/anythingllm@sha256:5ce7b65badd7de94827846d33fe1b38eff71f86875b7ce88d6e56d2371ec2d6b"
FILES = {
    "utils/agents/aibitat/index.js": "dc00a0f50a50226bba4bf0d60f8b8429fd1afe9d48efa61542a531898d631ec3",
    "utils/agents/aibitat/providers/helpers/tooled.js": "18470b8abb07bac5220604a7d5db380fe21538a9d3c53a66f3cf47c7396cd492",
}


def apply(server):
    """Fail closed on upstream drift, before patching either file."""
    for relative, expected in FILES.items():
        if hashlib.sha256((server / relative).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Unsupported AnythingLLM source: {relative}; review a new patch")
    subprocess.run(
        [
            "patch",
            "--batch",
            "--fuzz=0",
            "-p1",
            "-d",
            str(server),
            "-i",
            str(Path(__file__).with_name("hard-tool-limit.patch").resolve()),
        ],
        check=True,
    )


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else "localhost/anythingllm-mail:tool-limit"
    with tempfile.TemporaryDirectory(prefix="anythingllm-limit-") as directory:
        context = Path(directory)
        server = context / "server"
        server.mkdir()
        container = subprocess.check_output(["podman", "create", BASE], text=True).strip()
        try:
            for relative in FILES:
                target = server / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    ["podman", "cp", f"{container}:/app/server/{relative}", str(target)], check=True
                )
        finally:
            subprocess.run(["podman", "rm", container], check=True)
        apply(server)
        (context / "tool-limit.test.cjs").write_bytes(
            Path(__file__).with_name("tool-limit.test.cjs").read_bytes()
        )
        (context / "Containerfile").write_text(
            f"FROM {BASE}\n"
            "COPY --chown=1000:1000 server/ /app/server/\n"
            "COPY tool-limit.test.cjs /opt/mail-client-tests/tool-limit.test.cjs\n"
        )
        subprocess.run(["podman", "build", "--pull=never", "-t", tag, str(context)], check=True)
    subprocess.run(
        [
            "podman",
            "run",
            "--rm",
            "--network=none",
            "--entrypoint=node",
            tag,
            "--test",
            "/opt/mail-client-tests/tool-limit.test.cjs",
        ],
        check=True,
    )


if __name__ == "__main__":
    main()
