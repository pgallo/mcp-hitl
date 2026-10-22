"""Approval policy as code. The rules live in spec/policy.json; this module only interprets them.

Three questions are answered here and nowhere else:
  1. May this action be proposed at all, given the evidence confidence?
  2. If proposed, may it skip the human (auto-approve)?
  3. Is this particular human decision acceptable (reason length, separation of duties)?
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .canonical import fmt_num

AUTOMATED_PREFIXES = ("agent:", "policy:")


class WorkspaceError(Exception):
    """Domain error with a stable code that both implementations share."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.detail = message


@dataclass(frozen=True)
class ProposalVerdict:
    risk: str
    allowed: bool
    auto_approve: bool
    explanation: str


class Policy:
    def __init__(self, doc: dict[str, Any]) -> None:
        self.doc = doc
        self.version: str = doc["version"]
        self._types: dict[str, Any] = doc["action_types"]
        self._tiers: dict[str, Any] = doc["risk_tiers"]

    def risk_of(self, action_type: str) -> str:
        if action_type not in self._types:
            raise WorkspaceError("INVALID_ARGUMENT", f"unknown action type {action_type}")
        return str(self._types[action_type]["risk"])

    def evaluate_proposal(self, action_type: str, confidence: float) -> ProposalVerdict:
        risk = self.risk_of(action_type)
        tier = self._tiers[risk]
        c = fmt_num(confidence)
        floor = tier["min_confidence_to_propose"]
        if confidence < floor:
            return ProposalVerdict(
                risk, False, False, f"{risk} risk requires confidence >= {fmt_num(floor)} to propose, assessment has {c}"
            )
        threshold = tier["auto_approve_min_confidence"]
        if threshold is None:
            return ProposalVerdict(risk, True, False, f"{risk} risk: human approval always required")
        if confidence >= threshold:
            return ProposalVerdict(risk, True, True, f"{risk} risk and confidence {c} >= {fmt_num(threshold)}: auto-approved")
        return ProposalVerdict(risk, True, False, f"{risk} risk but confidence {c} < {fmt_num(threshold)}: human approval required")

    def check_decision(self, *, risk: str, reviewer: str, reason: str, proposed_by: str, approving: bool) -> None:
        """Raise WorkspaceError if a human decision does not meet the policy."""
        if reviewer.startswith(AUTOMATED_PREFIXES):
            raise WorkspaceError("POLICY_DENIED", f"reviewer {reviewer} is an automated identity; decisions need a named human")
        if not approving:
            return
        tier = self._tiers[risk]
        min_len = tier["min_reason_length"]
        if len(reason) < min_len:
            raise WorkspaceError("POLICY_DENIED", f"{risk} risk approval needs a reason of at least {min_len} characters")
        if tier["separation_of_duties"] and reviewer == proposed_by:
            raise WorkspaceError("POLICY_DENIED", f"{risk} risk: the proposer cannot approve their own action")
