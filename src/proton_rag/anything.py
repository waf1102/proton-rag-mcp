"""AnythingLLM documented raw-text/vector-search APIs, restricted to dedicated local instance."""

import re
from urllib.parse import urlsplit
import httpx

KEY = re.compile(r"^proton-mail-[a-f0-9]{64}$")


def local_url(url):
    parts = urlsplit(url)
    if parts.scheme != "http" or parts.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("Index must use host loopback HTTP")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("Invalid index URL")
    return url.rstrip("/")


class Anything:
    def __init__(self, url, key, workspace="proton-mail"):
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", workspace):
            raise ValueError("Invalid workspace slug")
        self.url = local_url(url) + "/api/v1"
        self.workspace = workspace
        self.headers = {"Authorization": f"Bearer {key}"}
        self._documents = None

    def request(self, method, path, data=None):
        try:
            with httpx.Client(timeout=60, trust_env=False) as client:
                response = client.request(method, self.url + path, json=data, headers=self.headers)
                response.raise_for_status()
                value = response.json()
                if value.get("success") is False or value.get("error"):
                    raise ValueError("API failure")
                return value
        except Exception:
            raise RuntimeError("Local index request failed") from None

    def find(self, key):
        if not KEY.fullmatch(key):
            raise ValueError("Invalid document identity")
        if self._documents is None:
            tree = self.request("GET", "/documents")["localFiles"]
            documents = {}

            def walk(node, parent=""):
                if node.get("type") == "folder":
                    folder = "" if node["name"] == "documents" else parent + node["name"] + "/"
                    for child in node.get("items", []):
                        walk(child, folder)
                elif KEY.fullmatch(node.get("title", "")):
                    documents.setdefault(node["title"], []).append(parent + node["name"])

            walk(tree)
            self._documents = documents
        return list(self._documents.get(key, []))

    def refresh(self):
        self._documents = None

    def recover(self, key, text, before_upload=None):
        # No server idempotency token exists for raw-text. Never blindly replay an
        # ambiguous upload: a timed-out collector may still commit it later.
        self.refresh()
        if not self.find(key):
            raise RuntimeError("Pending upload requires reconciliation")
        return self.ensure(key, text, before_upload=before_upload)

    def ensure(self, key, text, before_upload=None):
        paths = self.find(key)  # Recover upload committed before crash/lost response.
        if len(paths) > 1:
            self.remove(paths[1:])
            paths = paths[:1]
        if not paths:
            if before_upload:
                before_upload()
            result = self.request(
                "POST",
                "/document/raw-text",
                {
                    "textContent": text,
                    "metadata": {
                        "title": key,
                        "docSource": key,
                        "description": "Untrusted mail data",
                    },
                },
            )
            paths = [d["location"] for d in result["documents"]]
            if len(paths) != 1:
                raise RuntimeError("Unexpected ingestion result")
        if self._documents is not None:
            self._documents[key] = paths
        # AnythingLLM skips existing workspace document mappings on repeated adds.
        self.request(
            "POST", f"/workspace/{self.workspace}/update-embeddings", {"adds": paths, "deletes": []}
        )
        return paths

    def remove(self, paths):
        for path in paths:
            if not re.fullmatch(r"custom-documents/[^/]+\.json", path):
                raise ValueError("Unexpected document storage path")
        if paths:
            self.request(
                "POST",
                f"/workspace/{self.workspace}/update-embeddings",
                {"adds": [], "deletes": paths},
            )
            self.request("DELETE", "/system/remove-documents", {"names": paths})
            if self._documents is not None:
                for key in self._documents:
                    self._documents[key] = [p for p in self._documents[key] if p not in paths]

    async def search(self, query, limit):
        try:
            async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
                response = await client.post(
                    self.url + f"/workspace/{self.workspace}/vector-search",
                    headers=self.headers,
                    json={"query": query, "topN": limit, "scoreThreshold": 0},
                )
                response.raise_for_status()
                return response.json()["results"]
        except Exception:
            raise RuntimeError("Local retrieval unavailable") from None
