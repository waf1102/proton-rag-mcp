"""Loopback Qdrant search with deterministic writes and durable message commits."""

import asyncio
import base64
from dataclasses import dataclass
import json
import re
import math
import struct
import httpx
from .chunks import split_text, point_id
from .embeddings import ContextExceeded, validate_vector
from .index import KEY, IndexFailure, local_url
from .index_state import text_hash


@dataclass(frozen=True)
class ImportedChunk:
    identity: str
    text: str
    vector: list[float]


class QdrantIndex:
    def __init__(self, settings, catalog, state, embedder, *, key="", client=None):
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", settings.workspace):
            raise ValueError("Invalid collection name")
        if settings.workspace != state.collection or embedder.profile != state.profile:
            raise ValueError("Index binding mismatch")
        self.settings, self.catalog, self.state, self.embedder = settings, catalog, state, embedder
        self.workspace = settings.workspace
        self.url = local_url(settings.qdrant_url) + "/collections/" + self.workspace
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(settings.embedding_timeout, connect=10),
            trust_env=False,
            headers={"api-key": key} if key else {},
        )

    def request(self, method, path, data=None, *, operation="index"):
        try:
            response = self.client.request(method, self.url + path, json=data)
            response.raise_for_status()
            value = response.json()
            if value.get("status") not in (None, "ok"):
                raise IndexFailure("index_response", operation)
            return value.get("result")
        except IndexFailure:
            raise
        except httpx.TimeoutException:
            raise IndexFailure("index_timeout", operation) from None
        except httpx.HTTPStatusError as error:
            raise IndexFailure("index_http", operation, error.response.status_code) from None
        except httpx.TransportError:
            raise IndexFailure("index_connection", operation) from None
        except Exception:
            raise IndexFailure("index_response", operation) from None

    def bootstrap(self, *, create=True):
        try:
            result = self.request("GET", "")
        except IndexFailure as error:
            if error.diagnostic.get("http_status") != 404:
                raise
            if not create:
                raise ValueError("Configured Qdrant collection is missing") from None
            self.request(
                "PUT",
                "",
                {
                    "vectors": {
                        "dense": {
                            "size": self.state.profile.dimension,
                            "distance": "Cosine",
                            "on_disk": True,
                        }
                    },
                    "sparse_vectors": {"lexical": {"modifier": "idf"}},
                    "on_disk_payload": True,
                    "hnsw_config": {"on_disk": True},
                    "optimizers_config": {"max_optimization_threads": 1},
                    "metadata": {"proton_rag_binding": self.state.binding},
                },
            )
            result = self.request("GET", "")
        params = result["config"]["params"]
        dense = params.get("vectors", {}).get("dense", {})
        if dense.get("size") != self.state.profile.dimension or dense.get("distance") != "Cosine":
            raise ValueError("Collection vector profile mismatch")
        if "lexical" not in params.get("sparse_vectors", {}):
            raise ValueError("Collection lexical profile mismatch")
        # Collection ownership is independent of vector shape; never adopt another account's index.
        metadata = result.get("config", {}).get("metadata", params.get("metadata", {})) or {}
        if metadata.get("proton_rag_binding") != self.state.binding:
            raise ValueError("Collection binding mismatch")
        if create:
            self.request(
                "PUT", "/index?wait=true", {"field_name": "message_id", "field_schema": "keyword"}
            )

    @staticmethod
    def _filter(key):
        if not KEY.fullmatch(key):
            raise ValueError("Invalid document identity")
        return {"must": [{"key": "message_id", "match": {"value": key}}]}

    def count(self, key):
        return self.request("POST", "/points/count", {"filter": self._filter(key), "exact": True})[
            "count"
        ]

    def verify_message(self, key):
        manifest = self.state.entry(key)
        if not manifest or self.count(key) != len(manifest["points"]):
            raise IndexFailure("index_incomplete", "verify")
        expected = manifest["points"]
        for offset in range(0, len(expected), 64):
            batch = expected[offset : offset + 64]
            result = self.request(
                "POST",
                "/points",
                {"ids": [p["id"] for p in batch], "with_payload": True, "with_vector": True},
            )
            records = {str(p["id"]): p for p in result}
            for wanted in batch:
                point = records.get(wanted["id"])
                if not point:
                    raise IndexFailure("index_incomplete", "verify")
                payload = point.get("payload", {})
                if (
                    payload.get("message_id") != key
                    or payload.get("profile") != self.state.profile.identity
                    or payload.get("checksum") != wanted["checksum"]
                    or text_hash(payload.get("text", "")) != wanted["text_hash"]
                ):
                    raise IndexFailure("index_incomplete", "verify")
                dense = validate_vector(
                    point.get("vector", {}).get("dense"), self.state.profile.dimension
                )
                norm = math.hypot(*dense)
                expected_dense = struct.unpack(
                    "<" + "f" * len(dense), base64.b64decode(wanted["dense_proof"])
                )
                if any(
                    abs(actual / norm - expected) > 1e-6
                    for actual, expected in zip(dense, expected_dense, strict=True)
                ):
                    raise IndexFailure("index_incomplete", "verify")
        return True

    def _write(self, key, text, chunks, before_upload=None):
        self._filter(key)
        if self.state.ready(key):
            old = self.state.entry(key)
            if old["text_hash"] != text_hash(text):
                raise ValueError("Message content differs from committed index")
            return [key]
        points, manifest = [], []
        row = self.catalog.message(key=key)
        metadata = json.loads(row["metadata"]) if row else {}
        for ordinal, chunk in enumerate(chunks):
            vector = validate_vector(chunk.vector, self.state.profile.dimension)
            norm = math.hypot(*vector)
            identity = point_id(
                self.catalog.namespace, key, self.state.profile.identity, chunk.identity
            )
            checksum = text_hash(
                json.dumps([chunk.text, vector, self.state.profile.identity], separators=(",", ":"))
            )
            manifest.append(
                {
                    "id": identity,
                    "checksum": checksum,
                    "text_hash": text_hash(chunk.text),
                    "dense_proof": base64.b64encode(
                        struct.pack("<" + "f" * len(vector), *(v / norm for v in vector))
                    ).decode(),
                }
            )
            lexical = (
                "\n".join(str(metadata.get(f, "")) for f in ["subject", "from"]) + "\n" + chunk.text
            )
            points.append(
                {
                    "id": identity,
                    "vector": {
                        "dense": vector,
                        "lexical": {"text": lexical, "model": "qdrant/bm25"},
                    },
                    "payload": {
                        "message_id": key,
                        "profile": self.state.profile.identity,
                        "schema": 1,
                        "text": chunk.text,
                        "ordinal": ordinal,
                        "checksum": checksum,
                    },
                }
            )
        self.state.prepare(key, text, manifest)
        if before_upload:
            before_upload()
        for offset in range(0, len(points), 32):
            result = self.request(
                "PUT",
                "/points?wait=true",
                {"points": points[offset : offset + 32]},
                operation="upsert",
            )
            if result.get("status") != "completed":
                raise IndexFailure("index_unacknowledged", "upsert")
        self.verify_message(key)
        self.state.finish(key)
        return [key]

    def _embed(self, chunk):
        try:
            return [
                ImportedChunk(
                    chunk.identity, chunk.text, self.embedder.embed_documents([chunk.text])[0]
                )
            ]
        except ContextExceeded:
            if len(chunk.text) <= 1:
                raise
            middle = len(chunk.text) // 2
            from .chunks import Chunk

            return self._embed(
                Chunk(chunk.identity + "/0", chunk.ordinal, chunk.text[:middle])
            ) + self._embed(Chunk(chunk.identity + "/1", chunk.ordinal, chunk.text[middle:]))

    def ensure(self, key, text, before_upload=None):
        if self.state.ready(key):
            return self._write(key, text, [], before_upload)
        chunks = []
        for chunk in split_text(text, self.settings.chunk_chars, self.settings.chunk_overlap):
            chunks.extend(self._embed(chunk))
        return self._write(key, text, chunks, before_upload)

    def recover(self, key, text, before_upload=None):
        # Pending writes can be proven complete without doing expensive embedding again.
        entry = self.state.entry(key)
        if entry:
            if entry["text_hash"] != text_hash(text):
                raise ValueError("Message content differs from committed index")
            try:
                self.verify_message(key)
                self.state.finish(key)
                return [key]
            except IndexFailure as error:
                if error.diagnostic["code"] != "index_incomplete":
                    raise
        return self.ensure(key, text, before_upload)

    def import_message(self, key, text, chunks):
        return self._write(key, text, chunks)

    def find(self, key):
        return [key] if self.state.entry(key) else []

    def remove(self, keys):
        for key in keys:
            result = self.request(
                "POST",
                "/points/delete?wait=true",
                {"filter": self._filter(key)},
                operation="remove",
            )
            if result.get("status") != "completed":
                raise IndexFailure("index_unacknowledged", "remove")
            self.state.forget(key)

    def refresh(self):
        self.embedder.check_profile()

    async def search(self, query, limit):
        vector = await self.embedder.embed_query(query)
        active = {row["message_key"] for row in self.catalog.rows().values() if row["active"]}
        # Groups prevent a message with many chunks from crowding out other messages.
        data = {
            "prefetch": [
                {"query": vector, "using": "dense", "limit": max(100, limit * 10)},
                {
                    "query": {"text": query, "model": "qdrant/bm25"},
                    "using": "lexical",
                    "limit": max(100, limit * 10),
                },
            ],
            "query": {"fusion": "rrf"},
            "group_by": "message_id",
            "group_size": 1,
            "limit": min(limit * 2, 100),
            "with_payload": True,
        }
        result = await asyncio.to_thread(
            self.request, "POST", "/points/query/groups", data, operation="search"
        )
        hits = []
        for group in result["groups"]:
            key = group["id"]
            if key not in active or not self.state.ready(key):
                continue
            entry = self.state.entry(key)
            ids = {p["id"]: p for p in entry["points"]}
            for point in group["hits"]:
                payload = point["payload"]
                wanted = ids.get(str(point["id"]))
                if (
                    wanted
                    and wanted["checksum"] == payload.get("checksum")
                    and payload.get("profile") == self.state.profile.identity
                ):
                    hits.append({"text": payload["text"], "metadata": {"docSource": key}})
                    break
            if len(hits) >= limit:
                break
        return hits
