"""End-to-end over a real stdio subprocess, using the SDK client."""

from __future__ import annotations

import json
import os
import sys

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_triage_round_trip_over_stdio() -> None:
    env = {**os.environ, "HITL_CLOCK": "fixed:2026-03-02T09:00:00Z"}
    params = StdioServerParameters(command=sys.executable, args=["-m", "hitl_server"], env=env)
    with anyio.fail_after(30):
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            hits = await session.call_tool("search_signals", {"query": "reefer", "filters": {"min_severity": 3}})
            ids = [h["id"] for h in hits.structuredContent["results"]]
            assert ids == ["SIG-001", "SIG-003"]  # equal score, severity breaks the tie

            asm = (await session.call_tool("assess", {"signal_ids": ["SIG-001", "SIG-002", "SIG-003"]})).structuredContent
            act = (
                await session.call_tool(
                    "propose_action",
                    {
                        "assessment_id": asm["assessment_id"],
                        "action": {"type": "quarantine_stock", "target": "bay K3"},
                        "proposed_by": "agent:e2e",
                    },
                )
            ).structuredContent
            assert act["status"] == "pending_approval"

            denied = await session.call_tool("approve_action", {"action_id": act["action_id"], "reviewer": "agent:e2e", "reason": "x" * 30})
            assert denied.isError and denied.content[0].text.startswith("POLICY_DENIED:")

            ok = await session.call_tool(
                "approve_action", {"action_id": act["action_id"], "reviewer": "lee", "reason": "Excursion confirmed by two sources."}
            )
            assert ok.structuredContent["status"] == "approved"

            verify = json.loads((await session.read_resource("audit://verify")).contents[0].text)  # type: ignore[arg-type,union-attr]
            assert verify["valid"] and verify["length"] == 5
