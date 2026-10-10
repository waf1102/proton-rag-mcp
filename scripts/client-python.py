"""Second real-service client; no excerpts or credentials in output."""

import asyncio
import json
import os
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def run():
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        in (
            "PATH",
            "RAG_STATE_DIR",
            "QDRANT_URL",
            "QDRANT_API_KEY",
            "QDRANT_COLLECTION",
            "OLLAMA_URL",
        )
    }
    async with stdio_client(
        StdioServerParameters(command=sys.executable, args=["-m", "proton_rag.mcp_server"], env=env)
    ) as (r, w):
        async with ClientSession(r, w) as client:
            init = await client.initialize()
            tools = await client.list_tools()
            assert [t.name for t in tools.tools] == [
                "search_mail",
                "index_status",
                "read_mail",
                "read_mail_batch",
            ]
            result = await client.call_tool(
                "search_mail",
                {"query": os.environ.get("RAG_INTEROP_QUERY", "When does cobalt arrive?")},
            )
            assert not result.isError
            if not os.environ.get("RAG_INTEROP_QUERY"):
                assert "Tuesday" in result.content[0].text
            assert "imap:///" in result.content[0].text
            print(
                json.dumps(
                    {
                        "client": "Python SDK",
                        "protocol": init.protocolVersion,
                        "transport": "stdio",
                        "live": True,
                        "passed": True,
                    }
                )
            )


asyncio.run(run())
