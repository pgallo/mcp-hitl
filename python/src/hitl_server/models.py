"""Typed models for tool inputs and outputs.

The SDK's high-level server derives the advertised JSON Schemas from these. tests/test_schemas.py and the
conformance suite check them against spec/tools.json, so drift fails the build.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SignalKind = Literal["delay", "sensor_anomaly", "supplier_notice", "customs_hold", "weather"]
ActionType = Literal[
    "notify_team",
    "open_ticket",
    "increase_monitoring",
    "expedite_order",
    "reroute_shipment",
    "hold_shipment",
    "quarantine_stock",
]
Risk = Literal["low", "medium", "high"]
ActionStatus = Literal["pending_approval", "approved", "rejected"]
Band = Literal["low", "medium", "high", "critical"]


class SearchFilters(BaseModel):
    kind: list[SignalKind] | None = None
    site: str | None = None
    min_severity: int | None = Field(default=None, ge=1, le=5)
    since: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class ActionInput(BaseModel):
    type: ActionType
    target: str = Field(min_length=1)
    note: str | None = None


class Signal(BaseModel):
    id: str
    kind: SignalKind
    severity: int = Field(ge=1, le=5)
    site: str
    lane: str | None
    source: str
    source_reliability: int = Field(ge=0, le=100)
    observed_at: str
    title: str
    summary: str
    tags: list[str]


class SignalHit(BaseModel):
    id: str
    title: str
    kind: SignalKind
    severity: int
    site: str
    observed_at: str
    score: int


class SearchResult(BaseModel):
    total: int
    results: list[SignalHit]


class Factors(BaseModel):
    max_severity: int
    mean_severity: float
    distinct_sources: int
    mean_reliability: float
    coherence: float


class Assessment(BaseModel):
    assessment_id: str
    signal_ids: list[str]
    score: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    band: Band
    factors: Factors
    rationale: list[str]
    created_at: str


class PolicyVerdict(BaseModel):
    auto_approve_eligible: bool
    explanation: str


class Decision(BaseModel):
    by: str
    reason: str
    at: str


class Action(BaseModel):
    action_id: str
    assessment_id: str
    type: ActionType
    target: str
    note: str | None
    risk: Risk
    status: ActionStatus
    confidence: float
    proposed_by: str
    created_at: str
    policy: PolicyVerdict
    decision: Decision | None


class ActionList(BaseModel):
    actions: list[Action]


class AuditEntry(BaseModel):
    seq: int
    at: str
    event: str
    actor: str
    subject: str
    data: dict[str, object]
    prev_hash: str
    hash: str


class AuditPage(BaseModel):
    length: int
    head_hash: str
    chain_valid: bool
    entries: list[AuditEntry]
