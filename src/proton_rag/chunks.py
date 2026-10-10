"""Deterministic text chunks and retry-safe Qdrant UUIDs."""

from dataclasses import dataclass
import hashlib
import json
import uuid


@dataclass(frozen=True)
class Chunk:
    identity: str
    ordinal: int
    text: str


def split_text(text: str, target: int = 1800, overlap: int = 200) -> list[Chunk]:
    if target <= overlap or overlap < 0:
        raise ValueError("Chunk target must exceed overlap")
    chunks, start = [], 0
    while start < len(text):
        end = min(len(text), start + target)
        if end < len(text):
            boundary = text.rfind("\n\n", start + target // 2, end)
            if boundary >= 0:
                end = boundary + 2
        part = text[start:end]
        ordinal = len(chunks)
        identity = hashlib.sha256(f"{ordinal}:".encode() + part.encode()).hexdigest()
        chunks.append(Chunk(identity, ordinal, part))
        if end == len(text):
            break
        start = end - overlap
    return chunks


def point_id(namespace: str, message_key: str, profile_id: str, chunk_id: str) -> str:
    identity = json.dumps([namespace, message_key, profile_id, chunk_id], separators=(",", ":"))
    return str(uuid.uuid5(uuid.NAMESPACE_URL, identity))
