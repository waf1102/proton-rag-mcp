"""Second real-service client; no excerpts or credentials in output."""

import asyncio
import json
import os
import sys
import re
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
                "grep_mail",
            ]
            result = await client.call_tool(
                "search_mail",
                {"query": os.environ.get("RAG_INTEROP_QUERY", "When does cobalt arrive?")},
            )
            assert not result.isError
            assert result.structuredContent == json.loads(result.content[0].text)
            if not os.environ.get("RAG_INTEROP_QUERY"):
                assert "Tuesday" in result.content[0].text
            assert "imap:///" in result.content[0].text
            hit = json.loads(result.content[0].text)["sources"][0]
            evidence = await client.call_tool(
                "grep_mail",
                {
                    "pattern": re.escape(hit["text"][:32])
                    if os.environ.get("RAG_INTEROP_QUERY")
                    else "ABC123",
                    "message_ids": [hit["message_id"]],
                    "max_bytes": 4000,
                },
            )
            assert not evidence.isError
            assert evidence.structuredContent == json.loads(evidence.content[0].text)
            assert len(evidence.content[0].text.encode()) <= 4000
            assert json.loads(evidence.content[0].text)["sources"][0]["citation"] == hit["citation"]
            batch = await client.call_tool(
                "read_mail_batch",
                {
                    "requests": [
                        {"message_id": hit["message_id"], "length": 128},
                        {"message_id": "unknown-fixture"},
                    ]
                },
            )
            assert not batch.isError
            assert len(batch.content[0].text.encode()) <= 3900
            messages = json.loads(batch.content[0].text)["messages"]
            assert messages[0]["message_id"] == hit["message_id"]
            assert messages[0]["offset"] == 0
            assert messages[1]["error"]
            alias = await client.call_tool("read_mail_batch", {"message_ids": [hit["message_id"]]})
            assert not alias.isError
            page = json.loads(alias.content[0].text)
            if page["next_call"]:
                following = await client.call_tool(
                    page["next_call"]["tool"], page["next_call"]["arguments"]
                )
                assert not following.isError
                assert (
                    json.loads(following.content[0].text)["messages"][0]["offset"]
                    == page["messages"][0]["next_offset"]
                )
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
