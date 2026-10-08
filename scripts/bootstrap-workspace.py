"""Initialize only the dedicated local workspace; credentials are runtime-injected."""

import os
from proton_rag.anything import Anything

backend = Anything(
    os.environ.get("ANYTHING_URL", "http://127.0.0.1:3001"), os.environ["ANYTHING_API_KEY"]
)
workspaces = backend.request("GET", "/workspaces")["workspaces"]
if not any(w["slug"] == "proton-test" for w in workspaces):
    result = backend.request("POST", "/workspace/new", {"name": "proton-test"})
    assert result["workspace"]["slug"] == "proton-test"
print("Dedicated workspace ready")
