import httpx
import pytest

from proton_rag.embeddings import EmbeddingProfile, Ollama, ContextExceeded
from proton_rag.index import IndexFailure


def embedder(handler):
    profile = EmbeddingProfile("nomic-embed-text", "digest-a")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return Ollama("http://127.0.0.1:11434", profile, client=client)


def response(request, *, digest="digest-a", error=None, vector=None):
    if request.url.path == "/api/tags":
        return httpx.Response(
            200, json={"models": [{"name": "nomic-embed-text:latest", "digest": digest}]}
        )
    if error:
        return httpx.Response(400, json={"error": error})
    return httpx.Response(200, json={"embeddings": [vector or [1.0] + [0.0] * 767]})


def test_same_dimension_model_change_is_rejected():
    model = embedder(lambda req: response(req, digest="digest-b"))
    with pytest.raises(IndexFailure, match="embedding_profile"):
        model.embed_documents(["Private text"])


def test_embedding_errors_are_redacted():
    model = embedder(lambda req: response(req, error="Private email and credential"))
    with pytest.raises(IndexFailure) as caught:
        model.embed_documents(["Private text"])
    assert "Private" not in str(caught.value)
    assert caught.value.diagnostic["operation"] == "embed"


def test_embedding_context_rejection_is_explicit():
    def handler(req):
        if req.url.path == "/api/embed":
            import json

            body = json.loads(req.content)
            assert body["truncate"] is False
            assert body["options"]["num_ctx"] == 2048
        return response(req, error="input length exceeds maximum context length")

    with pytest.raises(ContextExceeded):
        embedder(handler).embed_documents(["Long paragraph"])


@pytest.mark.parametrize("vector", [[0.0] * 768, [1.0] * 767, [float("nan")] * 768])
def test_invalid_vectors_are_rejected(vector):
    def handler(req):
        if req.url.path == "/api/tags":
            return response(req)
        # JSON prohibits NaN; exercise validation directly for that case below.
        return httpx.Response(200, json={"embeddings": [vector]})

    if any(x != x for x in vector):
        from proton_rag.embeddings import validate_vector

        with pytest.raises(IndexFailure):
            validate_vector(vector, 768)
    else:
        with pytest.raises(IndexFailure):
            embedder(handler).embed_documents(["text"])


def test_profile_fingerprint_includes_context_and_prefix():
    original = EmbeddingProfile("nomic-embed-text", "digest-a")
    assert (
        original.identity != EmbeddingProfile("nomic-embed-text", "digest-a", context=8192).identity
    )
    assert (
        original.identity
        != EmbeddingProfile("nomic-embed-text", "digest-a", query_prefix="search_query: ").identity
    )


async def test_query_uses_query_prefix_without_document_prefix():
    import json

    def handler(req):
        if req.url.path == "/api/embed":
            assert json.loads(req.content)["input"] == ["search_query: invoice"]
        return response(req)

    profile = EmbeddingProfile(
        "nomic-embed-text",
        "digest-a",
        document_prefix="search_document: ",
        query_prefix="search_query: ",
    )
    model = Ollama(
        "http://127.0.0.1:11434",
        profile,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert len(await model.embed_query("invoice")) == 768
