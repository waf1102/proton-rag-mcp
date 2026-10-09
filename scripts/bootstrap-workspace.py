"""Create the configured AnythingLLM workspace without changing other workspaces."""

import os
from proton_rag.anything import Anything
from proton_rag.config import Settings

settings = Settings.from_env()
backend = Anything(settings.anything_url, os.environ["ANYTHING_API_KEY"], settings.workspace)
workspaces = backend.request("GET", "/workspaces")["workspaces"]
if not any(w["slug"] == settings.workspace for w in workspaces):
    result = backend.request("POST", "/workspace/new", {"name": settings.workspace})
    assert result["workspace"]["slug"] == settings.workspace
print("Configured workspace ready")
