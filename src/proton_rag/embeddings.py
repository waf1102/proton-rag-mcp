"""Explicit model profiles and bounded local Ollama embedding requests."""

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import httpx
from .index import IndexFailure, local_url


@dataclass(frozen=True)
class EmbeddingProfile:
    model: str
    digest: str
    dimension: int = 768
    context: int = 2048
    document_prefix: str = ""
    query_prefix: str = ""
    normalization: str = "l2"
    provenance: str = "ollama-unprefixed-v1"

    @property
    def identity(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


class ContextExceeded(IndexFailure):
    def __init__(self):
        super().__init__("embedding_context", "embed")


def validate_vector(vector, dimension):
    if not isinstance(vector, (list, tuple)) or len(vector) != dimension:
        raise IndexFailure("embedding_vector", "embed")
    if any(not isinstance(x, (float, int)) or not math.isfinite(x) for x in vector):
        raise IndexFailure("embedding_vector", "embed")
    if sum(x * x for x in vector) <= 0:
        raise IndexFailure("embedding_vector", "embed")
    return list(vector)


def model_digest(url, model, client=None):
    owned = client is None
    client = client or httpx.Client(timeout=10, trust_env=False)
    try:
        response = client.get(local_url(url) + "/api/tags")
        response.raise_for_status()
        name = model if ":" in model else model + ":latest"
        for item in response.json()["models"]:
            if item.get("name") == name or item.get("model") == name:
                digest = item["digest"]
                if isinstance(digest, str) and digest:
                    return digest
        raise IndexFailure("embedding_model_missing", "embed")
    except IndexFailure:
        raise
    except Exception:
        raise IndexFailure("embedding_profile", "embed") from None
    finally:
        if owned:
            client.close()


class Ollama:
    def __init__(self, url, profile, timeout=300, client=None):
        self.url = local_url(url)
        self.profile = profile
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(timeout, connect=10), trust_env=False
        )

    def check_profile(self):
        if model_digest(self.url, self.profile.model, self.client) != self.profile.digest:
            raise IndexFailure("embedding_profile", "embed")

    def embed_documents(self, texts):
        return self._embed(texts, self.profile.document_prefix)

    def _embed(self, texts, prefix):
        self.check_profile()
        if not texts:
            return []
        try:
            response = self.client.post(
                self.url + "/api/embed",
                json={
                    "model": self.profile.model,
                    "input": [prefix + text for text in texts],
                    "truncate": False,
                    "options": {"num_ctx": self.profile.context},
                },
            )
            if response.status_code == 400:
                error = str(response.json().get("error", "")).lower()
                if "context length" in error or "input length" in error:
                    raise ContextExceeded()
            response.raise_for_status()
            vectors = response.json()["embeddings"]
            if len(vectors) != len(texts):
                raise IndexFailure("embedding_response", "embed")
            return [validate_vector(v, self.profile.dimension) for v in vectors]
        except IndexFailure:
            raise
        except httpx.TimeoutException:
            raise IndexFailure("index_timeout", "embed") from None
        except Exception:
            raise IndexFailure("index_response", "embed") from None

    async def embed_query(self, text):
        # Use a worker thread so the stdio MCP event loop stays responsive.
        import asyncio

        return (await asyncio.to_thread(self._embed, [text], self.profile.query_prefix))[0]
