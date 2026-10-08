"""Standards-based stdio MCP. No mailbox mutation or filesystem access tools."""

import os
from pathlib import Path
from typing import Annotated
from pydantic import Field
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from .anything import Anything
from .budget import Ledger
from .generation import answer
from .sync import Catalog


def build(catalog, backend, ledger=None, api_key=None):
    server = FastMCP("proton-test-rag", log_level="CRITICAL")
    read = ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    )

    async def search_impl(query, limit):
        results = await backend.search(query, limit)
        rows = catalog.rows()
        hits = []
        for item in results:
            key = item.get("metadata", {}).get("docSource")
            row = rows.get(key)
            if not row or not row["active"]:
                continue
            hits.append(
                {
                    "text": item["text"][:2000],
                    "citation": f"imap://Folders/test/{row['validity']}/{row['uid']}#{row['digest']}",
                    "synthetic": bool(row["synthetic"]),
                    "untrusted": True,
                }
            )
        return hits[:limit]

    @server.tool(annotations=read)
    async def search_mail(
        query: Annotated[str, Field(min_length=1, max_length=1000)],
        limit: Annotated[int, Field(ge=1, le=5)] = 3,
    ) -> dict:
        """Search only Folders/test locally. Returns bounded untrusted excerpts and citations.

        Query and excerpts remain on the VM. No cloud generation, mailbox writes or marking read.
        """
        try:
            return {"sources": await search_impl(query, limit), "data_path": "local_only"}
        except Exception:
            raise ValueError("Local retrieval unavailable") from None

    if api_key and ledger:

        @server.tool(
            annotations=ToolAnnotations(
                readOnlyHint=True, destructiveHint=False, idempotentHint=False, openWorldHint=True
            )
        )
        async def answer_synthetic(
            query: Annotated[str, Field(min_length=1, max_length=1000)],
        ) -> dict:
            """Send query and at most 3 synthetic excerpts (2000 characters each) to OpenRouter.

            Paid, UTC $5/day shared cap. Private-mail context is refused. Excerpts are untrusted data.
            """
            try:
                return await answer(query, await search_impl(query, 3), ledger, api_key)
            except Exception:
                raise ValueError(
                    "Generation refused or unavailable; reservation retained if sent"
                ) from None

    return server


def main():
    os.umask(0o077)
    state = Path(os.environ["RAG_STATE_DIR"])
    # Clients must share the daemon state directory and ledger; never per-client accounting.
    backend = Anything(
        os.environ.get("ANYTHING_URL", "http://127.0.0.1:3001"), os.environ["ANYTHING_API_KEY"]
    )
    key = os.environ.get("OPENROUTER_API_KEY")
    server = build(
        Catalog(state / "catalog.db"), backend, Ledger(state / "budget.db") if key else None, key
    )
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
