from __future__ import annotations

from hitl_agent.planner import Group, HostedLLMPlanner, StubPlanner, make_planner


def _signals() -> dict:
    base = {"severity": 4, "source": "telematics", "title": "t", "tags": []}
    return {
        "A": {**base, "id": "A", "kind": "weather", "site": "S1", "lane": "L1"},
        "B": {**base, "id": "B", "kind": "delay", "site": "S2", "lane": "L1"},
        "C": {**base, "id": "C", "kind": "sensor_anomaly", "site": "S1", "lane": None},
    }


def test_groups_by_lane_before_site() -> None:
    sig = _signals()
    groups = StubPlanner().group([{"id": i} for i in ("A", "B", "C")], sig)
    assert groups == [Group("lane L1", ["A", "B"]), Group("site S1", ["C"])]


def test_low_band_only_notifies() -> None:
    proposals = StubPlanner().propose(Group("site S1", ["C"]), {"band": "medium"}, _signals())
    assert [p.type for p in proposals] == ["notify_team"]


def test_high_band_adds_containment_with_fallback() -> None:
    proposals = StubPlanner().propose(Group("site S1", ["C"]), {"band": "critical"}, _signals())
    assert [p.type for p in proposals] == ["notify_team", "quarantine_stock"]
    assert proposals[1].fallback is not None and proposals[1].fallback.type == "increase_monitoring"


def test_llm_planner_falls_back_on_bad_output(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    planner = HostedLLMPlanner(api_key="test")
    sig = _signals()
    group = Group("site S1", ["C"])
    assessment = {"band": "critical", "score": 0.9, "confidence": 0.9, "rationale": []}
    monkeypatch.setattr(planner, "_complete", lambda *a: "not json at all")
    assert [p.type for p in planner.propose(group, assessment, sig)] == ["notify_team", "quarantine_stock"]
    monkeypatch.setattr(planner, "_complete", lambda *a: '{"action": "approve_action", "note": "sneaky"}')
    assert [p.type for p in planner.propose(group, assessment, sig)] == ["notify_team", "quarantine_stock"]
    monkeypatch.setattr(planner, "_complete", lambda *a: 'Sure: {"action": "hold_shipment", "note": "hold it"}')
    assert [p.type for p in planner.propose(group, assessment, sig)] == ["notify_team", "hold_shipment"]


def test_default_planner_needs_no_key(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert make_planner("stub").name == "stub"
