"""pytest entry point for the conformance suite (the same cases run.py executes)."""

from __future__ import annotations

import sys
from pathlib import Path

import anyio
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import (  # noqa: E402
    ROOT,
    Runner,
    StepOutcome,
    check_expectations,
    default_servers,
    divergences,
    error_class,
    load_cases,
    load_spec,
)

CASES = load_cases()
SERVERS = default_servers()
TS_BUILT = (ROOT / "typescript" / "dist" / "index.js").exists()


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_case_on_both_servers(case: dict) -> None:
    if not TS_BUILT:
        pytest.skip("typescript/dist not built; run npm run build in typescript/")
    runner = Runner(load_spec())

    async def go() -> dict[str, list[StepOutcome]]:
        return {name: await runner.run_case(cmd, case) for name, cmd in SERVERS.items()}

    results = anyio.run(go)
    problems = [f"[{n}] {o.label}: {p}" for n, outs in results.items() for o in outs for p in o.problems]
    problems += [f"[diverge] {d}" for d in divergences(results)]
    assert problems == []


# --- the harness itself ---------------------------------------------------------


def _ok(data: object) -> dict:
    return {"kind": "ok", "data": data}


def test_divergence_is_detected_even_when_both_pass_expectations() -> None:
    a = [StepOutcome("s1", _ok({"score": 0.86, "id": "X"}))]
    b = [StepOutcome("s1", _ok({"score": 0.8600001, "id": "X"}))]
    assert divergences({"python": a, "typescript": b}) == ["s1: python vs typescript: $.score: 0.86 != 0.8600001"]


def test_error_wording_may_differ_but_class_may_not() -> None:
    a = [StepOutcome("s", {"kind": "error", "error": "INVALID_INPUT", "text": "Input validation error: x"})]
    b = [StepOutcome("s", {"kind": "error", "error": "INVALID_INPUT", "text": "MCP error -32602: Input validation error: y"})]
    c = [StepOutcome("s", {"kind": "error", "error": "OTHER", "text": "boom"})]
    assert divergences({"a": a, "b": b}) == []
    assert divergences({"a": a, "c": c}) != []


@pytest.mark.parametrize(
    ("text", "cls"),
    [
        ("POLICY_DENIED: nope", "POLICY_DENIED"),
        ("Input validation error: [] should be non-empty", "INVALID_INPUT"),
        ("MCP error -32602: Input validation error: Invalid arguments", "INVALID_INPUT"),
        ("Unknown tool: x", "UNKNOWN_TOOL"),
        ("MCP error -32602: Tool x not found", "UNKNOWN_TOOL"),
        ("something else", "OTHER"),
    ],
)
def test_error_classes(text: str, cls: str) -> None:
    assert error_class(text) == cls


def test_expectation_operators() -> None:
    obs = _ok({"a": {"b": [1, 2, 3]}, "s": "hello world", "n": 0.5, "items": [{"k": 1}, {"k": 1}]})
    assert (
        check_expectations(
            obs,
            {
                "equals": {"a.b.-1": 3},
                "length": {"a.b": 3},
                "contains": {"s": "world"},
                "gte": {"n": 0.5},
                "each": {"items": {"k": 1}},
                "matches": {"s": "^hel"},
            },
        )
        == []
    )
    assert check_expectations(obs, {"equals": {"a.b.9": 1}}) != []
    assert check_expectations(obs, {"error": "NOT_FOUND"}) == ["expected error NOT_FOUND, call succeeded"]
