import asyncio
import os
import json
import sys
import pytest
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

FIXTURE = str(Path(__file__).with_name("fixture_server.py"))


@pytest.mark.parametrize("result_format", ["both", "text"])
async def test_sdk_result_formats_preserve_evidence_and_continuations(result_format):
    env = {"RAG_MCP_RESULT_FORMAT": result_format, "FIXTURE_GENERATION": "1"}
    async with stdio_client(
        StdioServerParameters(command=sys.executable, args=[FIXTURE], env=env)
    ) as (r, w):
        async with ClientSession(r, w) as client:
            await client.initialize()
            # A client that serializes the whole result must see the evidence once
            # in text mode; clients requiring structured results retain the default.
            full = await client.call_tool("read_mail", {"message_id": "fixture", "length": 50000})
            assert not full.isError
            assert full.model_dump_json().count("Confirmation: ABC123") == (
                1 if result_format == "text" else 2
            )
            for name, args in [
                ("search_mail", {"query": "When does cobalt arrive?", "max_bytes": 16000}),
                ("grep_mail", {"pattern": "ABC123"}),
                ("related_mail", {"message_id": "fixture"}),
                ("index_status", {}),
                ("answer_mail", {"query": "When does cobalt arrive?"}),
                ("read_mail_batch", {"message_ids": ["fixture"]}),
            ]:
                result = await client.call_tool(name, args)
                assert not result.isError
                page = json.loads(result.content[0].text)
                assert result.structuredContent == (
                    page if result_format == "both" and name != "answer_mail" else None
                )
                if name == "read_mail_batch":
                    assert page["next_call"]
                    following = await client.call_tool(
                        page["next_call"]["tool"], page["next_call"]["arguments"]
                    )
                    assert not following.isError
                    assert (
                        json.loads(following.content[0].text)["messages"][0]["offset"]
                        == (page["messages"][0]["next_offset"])
                    )
            assert (await client.call_tool("read_mail", {"message_id": "absent"})).isError


async def test_python_sdk_client():
    async with stdio_client(StdioServerParameters(command=sys.executable, args=[FIXTURE])) as (
        r,
        w,
    ):
        async with ClientSession(r, w) as client:
            init = await client.initialize()
            assert init.protocolVersion
            tools = (await client.list_tools()).tools
            assert [t.name for t in tools] == [
                "search_mail",
                "index_status",
                "read_mail",
                "read_mail_batch",
                "related_mail",
                "grep_mail",
            ]
            assert tools[0].annotations.readOnlyHint
            result = await client.call_tool("search_mail", {"query": "When does cobalt arrive?"})
            assert not result.isError
            assert (
                "Tuesday" in result.content[0].text and "imap:///INBOX/" in result.content[0].text
            )
            hit = json.loads(result.content[0].text)["sources"][0]
            evidence = await client.call_tool("grep_mail", {"pattern": "ABC123"})
            assert not evidence.isError
            assert evidence.structuredContent == json.loads(evidence.content[0].text)
            assert "ABC123" in evidence.content[0].text
            assert json.loads(evidence.content[0].text)["sources"][0]["citation"] == hit["citation"]
            related = await client.call_tool("related_mail", {"message_id": hit["message_id"]})
            assert not related.isError
            related_page = json.loads(related.content[0].text)
            assert related.structuredContent == related_page
            assert related_page["sources"][0]["citation"] == hit["citation"]
            assert related_page["sources"][0]["display_truncated"]
            assert related_page["exhaustive"] is False
            larger = await client.call_tool(
                "search_mail", {"query": "When does cobalt arrive?", "max_bytes": 16000}
            )
            assert not larger.isError
            assert (
                json.loads(larger.content[0].text)["sources"][0]["text"]
                == "Cobalt arrives Tuesday."
            )
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
        assert tools[0]["name"] == "search_mail"
        await send(
            "tools/call", {"name": "search_mail", "arguments": {"query": "flight", "limit": 51}}, 20
        )
        invalid_limit = await receive()
        assert invalid_limit["id"] == 20 and invalid_limit["result"]["isError"]
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
    assert {t.name for t in tools} == {
        "search_mail",
        "index_status",
        "read_mail",
        "read_mail_batch",
        "related_mail",
        "grep_mail",
        "answer_mail",
    }
    assert "private email" in next(t.description for t in tools if t.name == "answer_mail")
