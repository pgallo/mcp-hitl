from __future__ import annotations

import json
from pathlib import Path

import pytest

from hitl_server.policy import WorkspaceError
from hitl_server.scoring import assess_signals, band_for, tokens
from hitl_server.spec import FixedClock, load_signals, spec_dir
from hitl_server.workspace import Workspace


def test_dataset_is_well_formed() -> None:
    signals = load_signals()
    assert len(signals) == 50
    assert len({s["id"] for s in signals}) == 50
    assert all(1 <= s["severity"] <= 5 and 0 <= s["source_reliability"] <= 100 for s in signals)


def test_tokenizer() -> None:
    assert tokens("Reefer R-17: +9C, bay K3") == ["reefer", "r", "17", "9c", "bay", "k3"]


@pytest.mark.parametrize(("score", "band"), [(0.0, "low"), (0.35, "medium"), (0.55, "high"), (0.75, "critical"), (1.0, "critical")])
def test_band_edges(score: float, band: str) -> None:
    assert band_for(score) == band


def test_score_and_confidence_are_independent() -> None:
    by_id = {s["id"]: s for s in load_signals()}
    rumour = assess_signals([by_id["SIG-013"]])
    corroborated = assess_signals([by_id[i] for i in ("SIG-010", "SIG-011", "SIG-012")])
    assert rumour["score"] > corroborated["score"]
    assert rumour["confidence"] < corroborated["confidence"]


def test_adding_an_unrelated_signal_lowers_coherence() -> None:
    by_id = {s["id"]: s for s in load_signals()}
    tight = assess_signals([by_id[i] for i in ("SIG-014", "SIG-015", "SIG-016")])
    loose = assess_signals([by_id[i] for i in ("SIG-014", "SIG-015", "SIG-016", "SIG-004")])
    assert loose["factors"]["coherence"] < tight["factors"]["coherence"]


def test_ids_and_timestamps_are_sequential(ws: Workspace) -> None:
    a1 = ws.assess(["SIG-001"])
    a2 = ws.assess(["SIG-002"])
    assert (a1["assessment_id"], a2["assessment_id"]) == ("ASM-0001", "ASM-0002")
    assert a1["created_at"] < a2["created_at"]


def test_denied_proposal_is_audited_but_creates_nothing(ws: Workspace) -> None:
    asm = ws.assess(["SIG-013"])
    with pytest.raises(WorkspaceError) as err:
        ws.propose_action(asm["assessment_id"], {"type": "hold_shipment", "target": "apron"}, "agent:t")
    assert err.value.code == "POLICY_DENIED"
    assert ws.list_actions()["actions"] == []
    assert ws.list_audit()["entries"][-1]["event"] == "action.denied"


def test_auto_approved_action_cannot_be_decided_again(ws: Workspace) -> None:
    asm = ws.assess(["SIG-001", "SIG-002", "SIG-003"])
    act = ws.propose_action(asm["assessment_id"], {"type": "notify_team", "target": "HUB-KESSEL"}, "agent:t")
    assert act["status"] == "approved"
    with pytest.raises(WorkspaceError) as err:
        ws.reject(act["action_id"], "lee", "changed my mind")
    assert err.value.code == "CONFLICT"


def test_reference_scenario_reproduces_fixture_hash() -> None:
    """The same scenario replayed by the TypeScript unit tests must hit the same head hash."""
    fixture = json.loads((spec_dir() / "fixtures" / "reference_scenario.json").read_text(encoding="utf-8"))
    ws = Workspace(FixedClock(fixture["clock"]))
    dispatch = {"assess": ws.assess, "propose_action": ws.propose_action, "approve_action": ws.approve, "reject_action": ws.reject}
    outcomes = []
    for tool, args in fixture["steps"]:
        try:
            dispatch[tool](**args)
            outcomes.append("ok")
        except WorkspaceError as err:
            outcomes.append(err.code)
    assert outcomes == fixture["outcomes"]
    assert len(ws.audit) == fixture["audit_length"]
    assert ws.audit.head_hash == fixture["head_hash"]


def test_workspace_persists_and_resumes_chain(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    first = Workspace(FixedClock("2026-03-02T09:00:00Z"), audit_path=path)
    first.assess(["SIG-001"])
    second = Workspace(FixedClock("2026-03-02T10:00:00Z"), audit_path=path)
    page = second.list_audit()
    assert page["length"] == 3 and page["chain_valid"]
    assert [e["event"] for e in page["entries"]] == ["workspace.started", "assessment.created", "workspace.started"]
