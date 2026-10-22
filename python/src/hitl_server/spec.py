"""Locate and load the shared spec directory (signals, policy, prompt template)."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol


def spec_dir() -> Path:
    override = os.environ.get("HITL_SPEC_DIR")
    if override:
        return Path(override)
    # python/src/hitl_server/spec.py -> repo root is three levels above the package.
    return Path(__file__).resolve().parents[3] / "spec"


def load_json(name: str) -> Any:
    return json.loads((spec_dir() / name).read_text(encoding="utf-8"))


def load_signals() -> list[dict[str, Any]]:
    return list(load_json("signals.json")["signals"])


def load_policy() -> dict[str, Any]:
    return dict(load_json("policy.json"))


def load_tools_spec() -> dict[str, Any]:
    return dict(load_json("tools.json"))


def triage_template() -> str:
    return (spec_dir() / "prompts" / "triage.md").read_text(encoding="utf-8")


class Clock(Protocol):
    def now(self) -> str: ...


class SystemClock:
    def now(self) -> str:
        return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class FixedClock:
    """Advances one second per call so runs are reproducible across implementations."""

    def __init__(self, start: str) -> None:
        self._start = datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        self._ticks = 0

    def now(self) -> str:
        value = self._start + timedelta(seconds=self._ticks)
        self._ticks += 1
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def clock_from_env() -> Clock:
    raw = os.environ.get("HITL_CLOCK", "")
    if raw.startswith("fixed:"):
        return FixedClock(raw.removeprefix("fixed:"))
    return SystemClock()
