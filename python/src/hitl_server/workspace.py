"""The evidence workspace: state and transitions, independent of the MCP transport.

Every state change goes through `_record`, so there is no code path that mutates an
assessment or action without writing an audit entry. Each write operation reads the
clock exactly once and reuses that instant for the entity and its audit entries; the
TypeScript twin follows the same rule, which is what makes audit hashes comparable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import scoring
from .audit import AuditLog
from .policy import Policy, WorkspaceError
from .spec import Clock, load_policy, load_signals


class Workspace:
    def __init__(
        self,
        clock: Clock,
        signals: list[dict[str, Any]] | None = None,
        policy: dict[str, Any] | None = None,
        audit_path: Path | None = None,
    ) -> None:
        self.clock = clock
        self.signals = signals if signals is not None else load_signals()
        self._by_id = {s["id"]: s for s in self.signals}
        self.policy = Policy(policy if policy is not None else load_policy())
        self.audit = AuditLog(audit_path)
        self.assessments: dict[str, dict[str, Any]] = {}
        self.actions: dict[str, dict[str, Any]] = {}
        self._record(
            self.clock.now(),
            "workspace.started",
            "system",
            "workspace",
            {"policy_version": self.policy.version, "signal_count": len(self.signals)},
        )

    def _record(self, at: str, event: str, actor: str, subject: str, data: dict[str, Any]) -> dict[str, Any]:
        return self.audit.append(at=at, event=event, actor=actor, subject=subject, data=data)

    # --- read side -------------------------------------------------------

    def search(self, query: str = "", filters: dict[str, Any] | None = None, limit: int = 10) -> dict[str, Any]:
        f = filters or {}
        total, results = scoring.search(
            self.signals,
            query,
            f.get("kind"),
            f.get("site"),
            f.get("min_severity"),
            f.get("since"),
            limit,
        )
        return {"total": total, "results": results}

    def get_signal(self, signal_id: str) -> dict[str, Any]:
        if signal_id not in self._by_id:
            raise WorkspaceError("NOT_FOUND", f"signal {signal_id} does not exist")
        return dict(self._by_id[signal_id])

    def list_actions(self, status: str | None = None) -> dict[str, Any]:
        items = [a for a in self.actions.values() if status is None or a["status"] == status]
        return {"actions": items}

    def list_audit(self, since_seq: int = 0, limit: int = 100) -> dict[str, Any]:
        result = self.audit.verify()
        return {
            "length": len(self.audit),
            "head_hash": self.audit.head_hash,
            "chain_valid": result.valid,
            "entries": self.audit.entries(since_seq, limit),
        }

    # --- write side ------------------------------------------------------

    def assess(self, signal_ids: list[str]) -> dict[str, Any]:
        if not signal_ids:
            raise WorkspaceError("INVALID_ARGUMENT", "signal_ids must not be empty")
        if len(set(signal_ids)) != len(signal_ids):
            raise WorkspaceError("INVALID_ARGUMENT", "signal_ids contains duplicates")
        missing = [sid for sid in signal_ids if sid not in self._by_id]
        if missing:
            raise WorkspaceError("NOT_FOUND", f"unknown signal ids: {', '.join(missing)}")
        at = self.clock.now()
        result = scoring.assess_signals([self._by_id[sid] for sid in signal_ids])
        assessment_id = f"ASM-{len(self.assessments) + 1:04d}"
        assessment = {
            "assessment_id": assessment_id,
            "signal_ids": list(signal_ids),
            **result,
            "created_at": at,
        }
        self._record(
            at,
            "assessment.created",
            "workspace",
            assessment_id,
            {
                "signal_ids": list(signal_ids),
                "score": result["score"],
                "confidence": result["confidence"],
                "band": result["band"],
            },
        )
        self.assessments[assessment_id] = assessment
        return assessment

    def propose_action(self, assessment_id: str, action: dict[str, Any], proposed_by: str = "agent:unknown") -> dict[str, Any]:
        assessment = self.assessments.get(assessment_id)
        if assessment is None:
            raise WorkspaceError("NOT_FOUND", f"assessment {assessment_id} does not exist")
        target = action["target"].strip()
        if not target:
            raise WorkspaceError("INVALID_ARGUMENT", "action target must not be blank")
        proposer = proposed_by.strip() or "agent:unknown"
        confidence = assessment["confidence"]
        at = self.clock.now()
        verdict = self.policy.evaluate_proposal(action["type"], confidence)
        if not verdict.allowed:
            self._record(
                at,
                "action.denied",
                proposer,
                assessment_id,
                {"type": action["type"], "risk": verdict.risk, "explanation": verdict.explanation},
            )
            raise WorkspaceError("POLICY_DENIED", verdict.explanation)

        action_id = f"ACT-{len(self.actions) + 1:04d}"
        record: dict[str, Any] = {
            "action_id": action_id,
            "assessment_id": assessment_id,
            "type": action["type"],
            "target": target,
            "note": action.get("note"),
            "risk": verdict.risk,
            "status": "pending_approval",
            "confidence": confidence,
            "proposed_by": proposer,
            "created_at": at,
            "policy": {"auto_approve_eligible": verdict.auto_approve, "explanation": verdict.explanation},
            "decision": None,
        }
        self._record(
            at,
            "action.proposed",
            proposer,
            action_id,
            {
                "assessment_id": assessment_id,
                "type": action["type"],
                "target": target,
                "risk": verdict.risk,
                "confidence": confidence,
            },
        )
        if verdict.auto_approve:
            decision = {"by": "policy:auto", "reason": verdict.explanation, "at": at}
            self._record(at, "action.auto_approved", "policy:auto", action_id, {"reason": verdict.explanation})
            record["status"] = "approved"
            record["decision"] = decision
        self.actions[action_id] = record
        return record

    def _decide(self, action_id: str, reviewer: str, reason: str, approving: bool) -> dict[str, Any]:
        record = self.actions.get(action_id)
        if record is None:
            raise WorkspaceError("NOT_FOUND", f"action {action_id} does not exist")
        reviewer = reviewer.strip()
        reason = reason.strip()
        if not reviewer:
            raise WorkspaceError("INVALID_ARGUMENT", "reviewer must not be blank")
        if not reason:
            raise WorkspaceError("INVALID_ARGUMENT", "reason must not be blank")
        if record["status"] != "pending_approval":
            raise WorkspaceError("CONFLICT", f"action {action_id} is already {record['status']}")
        at = self.clock.now()
        verb = "approve" if approving else "reject"
        try:
            self.policy.check_decision(
                risk=record["risk"],
                reviewer=reviewer,
                reason=reason,
                proposed_by=record["proposed_by"],
                approving=approving,
            )
        except WorkspaceError as err:
            # Refused decisions are evidence too: someone tried, and the policy said no.
            self._record(at, "decision.denied", reviewer, action_id, {"attempted": verb, "explanation": err.detail})
            raise
        status = "approved" if approving else "rejected"
        decision = {"by": reviewer, "reason": reason, "at": at}
        self._record(at, f"action.{status}", reviewer, action_id, {"reason": reason, "risk": record["risk"]})
        record["status"] = status
        record["decision"] = decision
        return record

    def approve(self, action_id: str, reviewer: str, reason: str) -> dict[str, Any]:
        return self._decide(action_id, reviewer, reason, approving=True)

    def reject(self, action_id: str, reviewer: str, reason: str) -> dict[str, Any]:
        return self._decide(action_id, reviewer, reason, approving=False)
