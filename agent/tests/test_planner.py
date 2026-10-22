from __future__ import annotations

import pytest

from hitl_agent.planner import Group, HostedPlanner, StubPlanner, extract_chat_text, make_planner


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


def test_hosted_planner_falls_back_on_bad_output(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    planner = HostedPlanner(url="http://localhost.invalid/v1/chat/completions", api_key="test", model="test-model")
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
    for name in ("LLM_API_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    assert make_planner("stub").name == "stub"
    with pytest.raises(SystemExit):
        make_planner("hosted")
    # No defaults for URL or model: a key alone is not enough.
    monkeypatch.setenv("LLM_API_KEY", "test")
    with pytest.raises(SystemExit):
        make_planner("hosted")
    monkeypatch.setenv("LLM_API_URL", "http://localhost.invalid/v1/chat/completions")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    assert make_planner("hosted").name == "hosted"


def test_extract_chat_text() -> None:
    assert extract_chat_text({"choices": [{"message": {"content": "hi"}}]}) == "hi"
    assert extract_chat_text({"choices": []}) == ""
    assert extract_chat_text({"unexpected": True}) == ""
