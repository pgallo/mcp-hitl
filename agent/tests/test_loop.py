"""Drive the real loop against the in-process Python server through the MCP protocol."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from hitl_agent.approver import ApproveAllApprover, AutoRejectApprover, TerminalApprover
from hitl_agent.loop import Tools, render_audit, run_triage
from hitl_agent.planner import StubPlanner
from hitl_server.server import build_server
from hitl_server.spec import FixedClock
from hitl_server.workspace import Workspace

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@asynccontextmanager
async def tools() -> AsyncIterator[Tools]:
    server = build_server(Workspace(FixedClock("2026-03-02T09:00:00Z")))
    async with create_connected_server_and_client_session(server._mcp_server) as session:
        yield Tools(session)


async def test_auto_reject_never_approves_consequential_actions() -> None:
    lines: list[str] = []
    async with tools() as t:
        report = await run_triage(t, StubPlanner(), AutoRejectApprover(), out=lines.append)
    approved = [a for a in report.actions if a["status"] == "approved"]
    assert approved and all(a["risk"] == "low" and a["decision"]["by"] == "policy:auto" for a in approved)
    assert all(a["status"] == "rejected" for a in report.actions if a["risk"] != "low")
    assert report.denied == ["hold_shipment -> HUB-ISEN"]
    assert report.verify["valid"] and report.verify["length"] == 30


async def test_approve_all_reasons_satisfy_every_tier() -> None:
    async with tools() as t:
        report = await run_triage(t, StubPlanner(), ApproveAllApprover(), out=lambda _: None)
    assert {a["status"] for a in report.actions} == {"approved"}


async def test_terminal_approver_round_trip() -> None:
    answers: Iterator[str] = iter(["x", "a", "", "lee.supervisor", "", "Excursion confirmed by two sources."] + ["s"] * 20)
    prompts: list[str] = []
    approver = TerminalApprover(input_fn=lambda p: (prompts.append(p), next(answers))[1], print_fn=lambda _: None)
    async with tools() as t:
        report = await run_triage(t, StubPlanner(), approver, site="HUB-KESSEL", out=lambda _: None)
    statuses = {a["type"]: a["status"] for a in report.actions}
    assert statuses == {"notify_team": "approved", "quarantine_stock": "approved"}
    quarantine = next(a for a in report.actions if a["type"] == "quarantine_stock")
    assert quarantine["decision"]["by"] == "lee.supervisor"
    assert prompts.count("    [a]pprove / [r]eject / [s]kip? ") == 2  # invalid answer re-prompted


async def test_policy_refusal_is_reported_not_fatal() -> None:
    class SelfApprover:
        def decide(self, action, assessment):  # type: ignore[no-untyped-def]
            from hitl_agent.approver import Decision

            return Decision("approve", "agent:triage", "approving my own proposal, which is not allowed")

    lines: list[str] = []
    async with tools() as t:
        report = await run_triage(t, StubPlanner(), SelfApprover(), site="HUB-KESSEL", out=lines.append)
    assert any("refused" in line and "POLICY_DENIED" in line for line in lines)
    render_audit(report, out=lines.append)
    assert any(line.startswith("chain VALID") for line in lines)
    assert any("decision.denied" in line for line in lines)
