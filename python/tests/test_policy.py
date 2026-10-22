from __future__ import annotations

import pytest

from hitl_server.policy import Policy, WorkspaceError
from hitl_server.spec import load_policy


@pytest.fixture
def policy() -> Policy:
    return Policy(load_policy())


@pytest.mark.parametrize(
    ("action", "confidence", "allowed", "auto"),
    [
        ("notify_team", 0.75, True, True),  # threshold is inclusive
        ("notify_team", 0.7499, True, False),
        ("open_ticket", 0.0, True, False),
        ("expedite_order", 0.39, False, False),
        ("expedite_order", 0.99, True, False),  # medium never auto-approves
        ("reroute_shipment", 0.49, False, False),
        ("reroute_shipment", 1.0, True, False),  # high never auto-approves
    ],
)
def test_proposal_matrix(policy: Policy, action: str, confidence: float, allowed: bool, auto: bool) -> None:
    verdict = policy.evaluate_proposal(action, confidence)
    assert (verdict.allowed, verdict.auto_approve) == (allowed, auto)


def test_unknown_action_type(policy: Policy) -> None:
    with pytest.raises(WorkspaceError) as err:
        policy.risk_of("launch_rocket")
    assert err.value.code == "INVALID_ARGUMENT"


@pytest.mark.parametrize(
    ("risk", "reviewer", "reason", "proposer", "approving", "fragment"),
    [
        ("low", "agent:bot", "fine", "x", True, "automated identity"),
        ("low", "policy:auto", "fine", "x", False, "automated identity"),
        ("medium", "ana", "too short", "x", True, "at least 10"),
        ("high", "ana", "a" * 19, "x", True, "at least 20"),
        ("high", "ana", "a" * 20, "ana", True, "proposer cannot approve"),
    ],
)
def test_decision_denials(policy: Policy, risk, reviewer, reason, proposer, approving, fragment) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(WorkspaceError) as err:
        policy.check_decision(risk=risk, reviewer=reviewer, reason=reason, proposed_by=proposer, approving=approving)
    assert err.value.code == "POLICY_DENIED"
    assert fragment in err.value.detail


def test_rejection_needs_no_long_reason_and_allows_proposer(policy: Policy) -> None:
    policy.check_decision(risk="high", reviewer="ana", reason="no", proposed_by="ana", approving=False)


def test_policy_is_data_driven() -> None:
    doc = load_policy()
    doc["risk_tiers"]["low"]["auto_approve_min_confidence"] = None
    assert not Policy(doc).evaluate_proposal("notify_team", 1.0).auto_approve
