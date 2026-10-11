"""Synthetic MCP peer fixture, intentionally separate from production configuration."""

import asyncio
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from proton_rag.sync import Catalog
from proton_rag.mcp_server import build
from proton_rag.config import Settings
from proton_rag import mcp_server


class Backend:
    async def search(self, query, limit):
        if query == "cancel-me":
            try:
                await asyncio.sleep(60)
            except BaseException:
                if os.environ.get("CANCEL_MARKER"):
                    Path(os.environ["CANCEL_MARKER"]).write_text("cancelled")
                raise
        return [{"text": "Cobalt arrives Tuesday.", "metadata": {"docSource": "fixture"}}]


async def generated_fixture(query, hits, api_key, settings):
    return {"answer": "Cobalt arrives Tuesday.", "sources": hits}


with TemporaryDirectory() as state:
    catalog = Catalog(Path(state) / "catalog.db")
    catalog.intent(
        "fixture",
        "INBOX",
        "1",
        "7",
        "abc",
        True,
        {
            "subject": "Cobalt arrival",
            "sender": "peer@example.test",
            "recipients": "me@example.test",
        },
    )
    catalog.activate("fixture", [])
    catalog.store_text(
        "fixture",
        "Cobalt arrives Tuesday.\n" + "Long itinerary section.\n" * 1000 + "Confirmation: ABC123",
        [],
        False,
    )
    if os.environ.get("FIXTURE_GENERATION"):
        mcp_server.answer = generated_fixture
    build(
        catalog,
        Backend(),
        api_key="fixture" if os.environ.get("FIXTURE_GENERATION") else None,
        settings=Settings.from_env(),
    ).run(transport="stdio")
