"""Planners turn evidence into proposals. The agent loop never trusts them with decisions.

A planner sees search hits and assessments and returns `Proposal`s. It cannot call
approve_action or reject_action: those calls are made only by the loop, and only with
an identity supplied by the human reviewer. Swapping the planner for a language model
therefore changes what gets proposed, never who approves it.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class Group:
    """A set of signals the planner believes describe one situation."""

    key: str
    signal_ids: list[str]


@dataclass(frozen=True)
class Proposal:
    type: str
    target: str
    note: str
    fallback: Proposal | None = field(default=None)


class Planner(Protocol):
    name: str

    def group(self, hits: list[dict[str, Any]], signals: dict[str, dict[str, Any]]) -> list[Group]: ...

    def propose(self, group: Group, assessment: dict[str, Any], signals: dict[str, dict[str, Any]]) -> list[Proposal]: ...


# Rules are ordered; the first match wins. Each maps a situation to the least disruptive
# action that addresses it, plus a containment step that a human must sign off.
_RULES: list[tuple[str, str, str]] = [
    ("sensor_anomaly", "quarantine_stock", "Contain affected stock until quality signs off"),
    ("weather", "reroute_shipment", "Route around the closure"),
    ("customs_hold", "expedite_order", "Pull forward replacement stock"),
    ("supplier_notice", "expedite_order", "Secure alternate supply"),
    ("delay", "hold_shipment", "Hold dependent outbound loads"),
]


class StubPlanner:
    """Deterministic rules. Same input, same plan, so CI can assert on the transcript."""

    name = "stub"

    def group(self, hits: list[dict[str, Any]], signals: dict[str, dict[str, Any]]) -> list[Group]:
        # Signals on a shared lane are one situation even if logged at different sites;
        # otherwise group by site. Order of first appearance is kept for stable output.
        groups: dict[str, list[str]] = {}
        for hit in hits:
            s = signals[hit["id"]]
            key = f"lane {s['lane']}" if s["lane"] else f"site {s['site']}"
            groups.setdefault(key, []).append(s["id"])
        return [Group(k, ids[:10]) for k, ids in groups.items()]

    def propose(self, group: Group, assessment: dict[str, Any], signals: dict[str, dict[str, Any]]) -> list[Proposal]:
        target = group.key.split(" ", 1)[1]
        kinds = [signals[i]["kind"] for i in group.signal_ids]
        dominant = max(set(kinds), key=lambda k: (kinds.count(k), k))
        notify = Proposal("notify_team", target, f"{assessment['band']} situation, {len(group.signal_ids)} signal(s)")
        if assessment["band"] in ("low", "medium"):
            return [notify]
        for kind, action, note in _RULES:
            if kind == dominant:
                watch = Proposal("increase_monitoring", target, "Fallback: watch until corroborated")
                return [notify, Proposal(action, target, note, fallback=watch)]
        return [notify]


class HostedLLMPlanner(StubPlanner):
    """Optional planner backed by a hosted LLM. Grouping stays deterministic; only the
    action choice is delegated, and any malformed answer falls back to the stub rules."""

    name = "llm"
    _ALLOWED = frozenset(
        {"notify_team", "open_ticket", "increase_monitoring", "expedite_order", "reroute_shipment", "hold_shipment", "quarantine_stock"}
    )

    def __init__(self, api_key: str, model: str | None = None) -> None:
        self.api_key = api_key
        self.model = model or os.environ.get("HITL_LLM_MODEL", "claude-haiku-5-5")

    def propose(self, group: Group, assessment: dict[str, Any], signals: dict[str, dict[str, Any]]) -> list[Proposal]:
        baseline = super().propose(group, assessment, signals)
        try:
            text = self._complete(group, assessment, signals)
            match = re.search(r"\{.*\}", text, re.S)
            choice = json.loads(match.group(0)) if match else {}
            action = choice.get("action")
            if action not in self._ALLOWED or action == "notify_team":
                return baseline
            target = group.key.split(" ", 1)[1]
            watch = Proposal("increase_monitoring", target, "Fallback: watch until corroborated")
            return [baseline[0], Proposal(action, target, str(choice.get("note", ""))[:200], fallback=watch)]
        except Exception:  # noqa: BLE001 - the planner is advisory; never let it break the loop
            return baseline

    def _complete(self, group: Group, assessment: dict[str, Any], signals: dict[str, dict[str, Any]]) -> str:
        import httpx  # optional dependency, imported only when this planner is selected

        evidence = [{k: signals[i][k] for k in ("id", "kind", "severity", "title", "source")} for i in group.signal_ids]
        prompt = (
            "You are proposing one operational action for a logistics situation. A human will approve or reject it.\n"
            f"Assessment: {json.dumps({k: assessment[k] for k in ('score', 'confidence', 'band', 'rationale')})}\n"
            f"Evidence: {json.dumps(evidence)}\n"
            f"Choose one action from {sorted(self._ALLOWED)} and answer only with JSON: "
            '{"action": "...", "note": "one sentence"}'
        )
        response = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": self.model, "max_tokens": 200, "messages": [{"role": "user", "content": prompt}]},
            timeout=30,
        )
        response.raise_for_status()
        return "".join(block.get("text", "") for block in response.json().get("content", []))


def make_planner(kind: str) -> Planner:
    if kind == "llm":
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise SystemExit("--planner llm needs ANTHROPIC_API_KEY; the default stub planner needs nothing")
        return HostedLLMPlanner(key)
    return StubPlanner()
