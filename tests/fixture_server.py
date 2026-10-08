"""Synthetic MCP peer fixture, intentionally separate from production configuration."""

import asyncio
import os
from pathlib import Path
from proton_rag.mcp_server import build


class Catalog:
    def rows(self):
        return {
            "fixture": {"active": 1, "validity": "1", "uid": "7", "digest": "abc", "synthetic": 1}
        }


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


build(Catalog(), Backend()).run(transport="stdio")
