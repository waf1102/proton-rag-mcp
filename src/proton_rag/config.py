"""Shared runtime settings for ingestion and MCP clients."""

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    state_dir: Path = Path.home() / ".local/share/proton-rag/mail"
    anything_url: str = "http://127.0.0.1:3001"
    workspace: str = "proton-mail"
    imap_host: str = "127.0.0.1"
    imap_port: int = 1143
    folders: tuple[str, ...] = ()
    excluded_folders: tuple[str, ...] = ()
    poll_seconds: int = 30
    batch_size: int = 25
    max_message_bytes: int = 32 * 1024 * 1024
    parser_timeout: int = 15
    max_text_chars: int = 200_000
    max_parts: int = 200
    search_default: int = 10
    search_max: int = 50
    excerpt_chars: int = 4000
    answer_sources: int = 5
    max_output: int = 1024
    model: str = "openai/gpt-4.1-mini"

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        values = {}
        names = {
            "state_dir": "RAG_STATE_DIR",
            "anything_url": "ANYTHING_URL",
            "workspace": "ANYTHING_WORKSPACE",
            "imap_host": "IMAP_HOST",
            "imap_port": "IMAP_PORT",
            "folders": "RAG_FOLDERS",
            "excluded_folders": "RAG_EXCLUDED_FOLDERS",
            "model": "OPENROUTER_MODEL",
            "max_output": "RAG_MAX_OUTPUT_TOKENS",
        }
        defaults = cls()
        for field in cls.__dataclass_fields__:
            key = names.get(field, "RAG_" + field.upper())
            if key not in env:
                continue
            raw = env[key]
            default = getattr(defaults, field)
            if isinstance(default, tuple):
                value = json.loads(raw)
                if not isinstance(value, list) or any(
                    not isinstance(x, str) or not x for x in value
                ):
                    raise ValueError(f"{key} must be a JSON array of folder names")
                values[field] = tuple(value)
            elif isinstance(default, int):
                values[field] = int(raw)
                if values[field] <= 0:
                    raise ValueError(f"{key} must be positive")
            elif isinstance(default, Path):
                values[field] = Path(raw).expanduser().absolute()
            else:
                if not raw.strip():
                    raise ValueError(f"{key} must not be empty")
                values[field] = raw
        result = cls(**values)
        if result.search_default > result.search_max or result.answer_sources > result.search_max:
            raise ValueError("Default retrieval limits must not exceed RAG_SEARCH_MAX")
        if result.imap_port > 65535:
            raise ValueError("Invalid IMAP port")
        return result
