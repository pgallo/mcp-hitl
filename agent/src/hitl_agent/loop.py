"""The agent loop: search, group, assess, propose, then pause for a human on anything pending.

The loop talks to the server only through MCP tool calls, so it runs unchanged against
either implementation. It uses a small `ToolCaller` seam so tests can drive it with an
in-process session as well as a stdio subprocess.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from .approver import Approver
from .planner import Planner, Proposal

AGENT_ID = "agent:triage"


class ToolError(Exception):
    def __init__(self, tool: str, text: str) -> None:
        super().__init__(f"{tool}: {text}")
        self.tool = tool
        self.text = text
        self.code = text.split(":", 1)[0] if ":" in text else "ERROR"


class Session(Protocol):
    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any: ...

    async def read_resource(self, uri: Any) -> Any: ...


@dataclass
class Tools:
    session: Session

    async def call(self, name: str, **arguments: Any) -> dict[str, Any]:
        result = await self.session.call_tool(name, arguments)
        if result.isError:
            raise ToolError(name, "".join(getattr(c, "text", "") for c in result.content))
        return dict(result.structuredContent)

    async def resource(self, uri: str) -> Any:
        res = await self.session.read_resource(uri)
        return json.loads(res.contents[0].text)


@dataclass
class Report:
    groups: list[dict[str, Any]] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    denied: list[str] = field(default_factory=list)
    audit: dict[str, Any] = field(default_factory=dict)
    verify: dict[str, Any] = field(default_factory=dict)


Printer = Callable[[str], None]


async def _propose(tools: Tools, assessment_id: str, proposal: Proposal, report: Report, out: Printer) -> dict[str, Any] | None:
    action = {"type": proposal.type, "target": proposal.target, "note": proposal.note}
    try:
        record = await tools.call("propose_action", assessment_id=assessment_id, action=action, proposed_by=AGENT_ID)
    except ToolError as err:
        if err.code != "POLICY_DENIED":
            raise
        out(f"    denied   {proposal.type:<20} {err.text.split(': ', 1)[1]}")
        report.denied.append(f"{proposal.type} -> {proposal.target}")
        if proposal.fallback is not None:
            return await _propose(tools, assessment_id, proposal.fallback, report, out)
        return None
    out(f"    proposed {record['action_id']} {record['type']:<20} {record['status']:<16} ({record['policy']['explanation']})")
    return record


async def run_triage(
    tools: Tools,
    planner: Planner,
    approver: Approver,
    *,
    min_severity: int = 3,
    site: str | None = None,
    out: Printer = print,
) -> Report:
    report = Report()
    filters: dict[str, Any] = {"min_severity": min_severity}
    if site:
        filters["site"] = site
    page = await tools.call("search_signals", query="", filters=filters, limit=50)
    out(f"Found {page['total']} signal(s) with severity >= {min_severity}{' at ' + site if site else ''}")

    signals: dict[str, dict[str, Any]] = {}
    for hit in page["results"]:
        signals[hit["id"]] = await tools.call("get_signal", id=hit["id"])

    pending: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for group in planner.group(page["results"], signals):
        assessment = await tools.call("assess", signal_ids=group.signal_ids)
        out("")
        out(
            f"[{assessment['assessment_id']}] {group.key}: {', '.join(group.signal_ids)}\n"
            f"    score {assessment['score']}  confidence {assessment['confidence']}  band {assessment['band']}"
        )
        for line in assessment["rationale"]:
            out(f"    - {line}")
        report.groups.append({"key": group.key, "assessment": assessment})
        for proposal in planner.propose(group, assessment, signals):
            record = await _propose(tools, assessment["assessment_id"], proposal, report, out)
            if record is None:
                continue
            report.actions.append(record)
            if record["status"] == "pending_approval":
                pending.append((record, assessment))

    if pending:
        out("")
        out(f"{len(pending)} action(s) need a human decision")
    for record, assessment in pending:
        decision = approver.decide(record, assessment)
        if decision.verdict == "skip":
            out(f"    skipped  {record['action_id']} (stays pending)")
            continue
        tool = "approve_action" if decision.verdict == "approve" else "reject_action"
        try:
            updated = await tools.call(tool, action_id=record["action_id"], reviewer=decision.reviewer, reason=decision.reason)
        except ToolError as err:
            out(f"    refused  {record['action_id']} {err.text}")
            continue
        record.update(updated)
        out(f"    {updated['status']:<8} {record['action_id']} by {decision.reviewer}: {decision.reason}")

    report.audit = await tools.call("list_audit", limit=500)
    report.verify = await tools.resource("audit://verify")
    return report


def render_audit(report: Report, out: Printer = print) -> None:
    out("")
    out("Audit trail (hash-chained, append-only)")
    out(f"{'seq':>4}  {'event':<22} {'actor':<16} {'subject':<10} {'prev':<10} hash")
    for e in report.audit["entries"]:
        out(f"{e['seq']:>4}  {e['event']:<22} {e['actor']:<16} {e['subject']:<10} {e['prev_hash'][:8]}.. {e['hash'][:8]}..")
    v = report.verify
    state = "VALID" if v["valid"] else f"BROKEN at seq {v['first_bad_seq']}: {v['problem']}"
    out(f"chain {state}, {v['length']} entries, head {v['head_hash'][:16]}..")
    counts: dict[str, int] = {}
    for a in report.actions:
        counts[a["status"]] = counts.get(a["status"], 0) + 1
    summary = ", ".join(f"{n} {s}" for s, n in sorted(counts.items())) or "none"
    out(f"actions: {summary}; denied by policy at proposal time: {len(report.denied)}")
