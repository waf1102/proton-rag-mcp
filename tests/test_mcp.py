import asyncio
import os
import json
import sys
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

FIXTURE = str(Path(__file__).with_name("fixture_server.py"))


async def test_python_sdk_client():
    async with stdio_client(StdioServerParameters(command=sys.executable, args=[FIXTURE])) as (
        r,
        w,
    ):
        async with ClientSession(r, w) as client:
            init = await client.initialize()
            assert init.protocolVersion
            tools = (await client.list_tools()).tools
            assert [t.name for t in tools] == ["search_mail", "index_status", "read_mail"]
            assert tools[0].annotations.readOnlyHint
            result = await client.call_tool("search_mail", {"query": "When does cobalt arrive?"})
            assert not result.isError
            assert (
                "Tuesday" in result.content[0].text and "imap:///INBOX/" in result.content[0].text
            )
            hit = json.loads(result.content[0].text)["sources"][0]
            full = await client.call_tool(
                "read_mail", {"message_id": hit["message_id"], "offset": 20000}
            )
            assert not full.isError
            assert "ABC123" in full.content[0].text
            status = await client.call_tool("index_status", {})
            assert not json.loads(status.content[0].text)["coverage_complete"]
            bad = await client.call_tool("search_mail", {"query": "", "limit": 99})
            assert bad.isError
            assert (await client.call_tool("delete_mail", {})).isError
            await client.send_ping()


async def test_raw_protocol_harness_negotiation_errors_cancellation(tmp_path):
    p = await asyncio.create_subprocess_exec(
        sys.executable,
        FIXTURE,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env={**os.environ, "CANCEL_MARKER": str(tmp_path / "cancelled")},
    )

    async def send(method, params=None, ident=None):
        value = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if ident is not None:
            value["id"] = ident
        p.stdin.write(json.dumps(value).encode() + b"\n")
        await p.stdin.drain()

    async def receive():
        return json.loads(await asyncio.wait_for(p.stdout.readline(), 5))

    try:
        await send(
            "initialize",
            {
                "protocolVersion": "2099-01-01",
                "capabilities": {},
                "clientInfo": {"name": "proton-protocol-harness", "version": "1"},
            },
            1,
        )
        reply = await receive()
        assert reply["result"]["protocolVersion"] != "2099-01-01"
        await send("notifications/initialized")
        await send("tools/list", ident=2)
        tools = (await receive())["result"]["tools"]
        assert tools[0]["inputSchema"]["properties"]["limit"]["maximum"] == 50
        await send("tools/call", {"name": "search_mail", "arguments": {"query": "cancel-me"}}, 3)
        await asyncio.sleep(0.05)
        await send("notifications/cancelled", {"requestId": 3, "reason": "fixture"})
        await send("ping", ident=4)
        reply = await receive()
        # SDK cancellation may produce a cancellation error; the connection must remain usable.
        if reply.get("id") == 3:
            reply = await receive()
        assert reply["id"] == 4 and "result" in reply
        assert (tmp_path / "cancelled").read_text() == "cancelled"
        await send("unknown/method", ident=5)
        assert (await receive())["error"]["code"] in (-32601, -32602)
    finally:
        p.stdin.close()
        await asyncio.wait_for(p.wait(), 5)


async def test_real_mail_search_deduplicates_and_preserves_locations(tmp_path):
    from proton_rag.mcp_server import search
    from proton_rag.config import Settings

    from proton_rag.sync import Catalog

    catalog = Catalog(tmp_path / "catalog.db")
    for key, folder in [("a", "INBOX"), ("b", "Folders/旅行")]:
        catalog.intent(
            key,
            folder,
            "1",
            "7",
            "abc",
            False,
            {"message_id": "<same>", "subject": "Receipt", "sender": "sender@example.test"},
        )
        catalog.activate(key, [])

    class Backend:
        async def search(self, query, limit):
            return [{"text": "x" * 5000, "metadata": {"docSource": key}} for key in ["a", "b"]]

    hits = await search(catalog, Backend(), "receipt", 10, Settings())
    assert len(hits) == 1 and len(hits[0]["text"]) == 4000
    assert len(hits[0]["locations"]) == 2
    assert "%E6%97%85" in hits[0]["locations"][1]["citation"]
    assert hits[0]["metadata"]["subject"] == "Receipt"


async def test_answer_tool_available_with_key_without_ledger():
    from proton_rag.mcp_server import build
    from unittest.mock import Mock

    server = build(Mock(), Mock(), api_key="fixture")
    tools = await server.list_tools()
    assert {t.name for t in tools} == {"search_mail", "index_status", "read_mail", "answer_mail"}
    assert "private email" in next(t.description for t in tools if t.name == "answer_mail")
