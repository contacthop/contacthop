"""MCP server: tools drive the real app end to end via call_tool, and gateway
backstop rejections surface as tool errors the model can read."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

pytest.importorskip("mcp")

from contacthop.config import Settings
from contacthop.main import create_app
from contacthop.mcp_server import build_server
from contacthop.sdk import ContactHopClient


@pytest.fixture()
async def mcp_env(tmp_path: Path) -> AsyncIterator[Any]:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'mcp.db'}",
        max_messages_per_hour=3,
        _env_file=None,  # type: ignore[call-arg]
    )
    app = create_app(settings)
    await app.state.db.create_all()
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://hop")
    client = ContactHopClient(http_client=http)
    server = build_server(client=client)
    yield server
    await client.close()
    await app.state.db.dispose()


def payload(result: Any) -> Any:
    """Extract the structured payload from a call_tool result across SDK shapes."""
    if isinstance(result, tuple):  # (content, structured) in recent SDKs
        content, structured = result
        if isinstance(structured, dict):
            return structured.get("result", structured)
        if structured is not None:
            return structured
        result = content
    texts = [c.text for c in result if getattr(c, "type", "") == "text"]
    return json.loads(texts[0]) if texts else None


async def test_tool_catalog(mcp_env: Any) -> None:
    tools = {t.name: t for t in await mcp_env.list_tools()}
    expected = {
        "list_contacts", "create_contact", "contact_stats",
        "list_conversations", "start_conversation", "get_context",
        "send_message", "switch_channel", "place_call",
        "remember", "recall", "recall_topic",
    }
    assert expected <= set(tools)
    # descriptions are the agent-facing docs — they must exist and say something
    for name in expected:
        assert tools[name].description and len(tools[name].description) > 30, name


async def test_full_agent_flow_through_tools(mcp_env: Any) -> None:
    contact = payload(await mcp_env.call_tool(
        "create_contact",
        {"display_name": "Ada", "sms_number": "+15554443333", "email_address": "ada@x.com"},
    ))
    conversation = payload(await mcp_env.call_tool(
        "start_conversation",
        {"contact_id": contact["id"], "goal": "schedule the demo"},
    ))

    payload(await mcp_env.call_tool(
        "remember",
        {"contact_id": contact["id"], "fact": "prefers 4pm meetings", "topic": "scheduling"},
    ))
    sent = payload(await mcp_env.call_tool(
        "send_message",
        {"conversation_id": conversation["id"], "body": "Does 4pm Thursday work?"},
    ))
    assert sent["channel"] == "sms"  # policy picked it

    context = payload(await mcp_env.call_tool(
        "get_context", {"conversation_id": conversation["id"]}
    ))
    assert context["goal"] == "schedule the demo"
    assert [m["body"] for m in context["recent_messages"]] == ["Does 4pm Thursday work?"]
    assert [f["text"] for f in context["memory"]] == ["prefers 4pm meetings"]

    shared = payload(await mcp_env.call_tool("recall_topic", {"topic": "scheduling"}))
    assert shared[0]["contact_id"] == contact["id"]


async def test_backstop_rejection_is_readable(mcp_env: Any) -> None:
    contact = payload(await mcp_env.call_tool(
        "create_contact", {"sms_number": "+15550001122"}
    ))
    conversation = payload(await mcp_env.call_tool(
        "start_conversation", {"contact_id": contact["id"], "goal": "rate limit demo"}
    ))
    for i in range(3):
        await mcp_env.call_tool(
            "send_message", {"conversation_id": conversation["id"], "body": f"msg {i}"}
        )

    with pytest.raises(Exception) as excinfo:
        await mcp_env.call_tool(
            "send_message", {"conversation_id": conversation["id"], "body": "one too many"}
        )
    assert "rate limit" in str(excinfo.value)
