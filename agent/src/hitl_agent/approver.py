"""Where the human sits. The loop asks an Approver; it never decides on its own."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

Verdict = Literal["approve", "reject", "skip"]


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    reviewer: str
    reason: str


class Approver(Protocol):
    def decide(self, action: dict[str, Any], assessment: dict[str, Any]) -> Decision: ...


class AutoRejectApprover:
    """CI mode: every pending action is rejected, so nothing consequential is ever approved unattended."""

    def __init__(self, reviewer: str = "ci.reviewer") -> None:
        self.reviewer = reviewer

    def decide(self, action: dict[str, Any], assessment: dict[str, Any]) -> Decision:
        return Decision("reject", self.reviewer, "Auto-rejected in non-interactive mode")


class ApproveAllApprover:
    """Demo mode: approves everything. The reason is long enough for every risk tier on purpose."""

    def __init__(self, reviewer: str = "demo.reviewer") -> None:
        self.reviewer = reviewer

    def decide(self, action: dict[str, Any], assessment: dict[str, Any]) -> Decision:
        return Decision("approve", self.reviewer, f"Approved in demo mode after reviewing {assessment['assessment_id']}")


class TerminalApprover:
    """Interactive prompt. Input and output are injectable so the prompt itself is testable."""

    def __init__(
        self,
        reviewer: str | None = None,
        input_fn: Callable[[str], str] = input,
        print_fn: Callable[[str], None] = print,
    ) -> None:
        self._input = input_fn
        self._print = print_fn
        self.reviewer = reviewer

    def decide(self, action: dict[str, Any], assessment: dict[str, Any]) -> Decision:
        p = self._print
        p("")
        p(f"  APPROVAL NEEDED  {action['action_id']}  {action['type']} -> {action['target']}")
        p(f"    risk {action['risk']}, evidence confidence {action['confidence']}, band {assessment['band']}")
        for line in assessment["rationale"]:
            p(f"    - {line}")
        p(f"    policy: {action['policy']['explanation']}")
        while True:
            answer = self._input("    [a]pprove / [r]eject / [s]kip? ").strip().lower()
            if answer in ("a", "r", "s"):
                break
        if answer == "s":
            return Decision("skip", "", "")
        while not self.reviewer:
            self.reviewer = self._input("    reviewer name: ").strip()
        reason = ""
        while not reason:
            reason = self._input("    reason: ").strip()
        return Decision("approve" if answer == "a" else "reject", self.reviewer, reason)
