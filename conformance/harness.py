"""Conformance harness: run JSON-described scenarios against MCP servers over stdio.

Every scenario starts a fresh server process with a fixed clock, so two correct
implementations must produce identical observations step by step, down to timestamps
and audit hashes. The harness checks two things:
  1. each server satisfies the expectations written in the case file;
  2. the servers agree with each other on every observation (divergence check).
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from schema_norm import diff, normalize

ROOT = Path(__file__).resolve().parents[1]
SPEC_DIR = ROOT / "spec"
CASES_DIR = SPEC_DIR / "conformance"
FIXED_CLOCK = "fixed:2026-03-02T09:00:00Z"
DOMAIN_CODES = ("NOT_FOUND", "INVALID_ARGUMENT", "CONFLICT", "POLICY_DENIED")


def default_servers() -> dict[str, list[str]]:
    return {
        "python": [sys.executable, "-m", "hitl_server"],
        "typescript": ["node", str(ROOT / "typescript" / "dist" / "index.js")],
    }


def load_spec() -> dict[str, Any]:
    return json.loads((SPEC_DIR / "tools.json").read_text(encoding="utf-8"))


def load_cases(selected: list[str] | None = None) -> list[dict[str, Any]]:
    cases = []
    for path in sorted(CASES_DIR.glob("*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        case.setdefault("name", path.stem)
        if selected and case["name"] not in selected and path.stem not in selected:
            continue
        cases.append(case)
    return cases


def error_class(text: str) -> str:
    """Map implementation-specific error text onto a shared vocabulary."""
    for code in DOMAIN_CODES:
        if text.startswith(f"{code}:"):
            return code
    if "Input validation error" in text:
        return "INVALID_INPUT"
    if "Unknown tool" in text or re.search(r"Tool \S+ not found", text):
        return "UNKNOWN_TOOL"
    return "OTHER"


# --- expectation language ---------------------------------------------------

_MISSING = object()


def get_path(value: Any, path: str) -> Any:
    if path in ("", "$"):
        return value
    current = value
    for part in path.split("."):
        if isinstance(current, list) and (part.isdigit() or part.startswith("-")):
            idx = int(part)
            if -len(current) <= idx < len(current):
                current = current[idx]
                continue
            return _MISSING
        if isinstance(current, dict) and part in current:
            current = current[part]
            continue
        return _MISSING
    return current


def check_expectations(obs: dict[str, Any], expect: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    wanted_error = expect.get("error")
    if obs["kind"] == "error":
        if wanted_error is None:
            return [f"unexpected error ({obs['error']}): {obs['text'][:200]}"]
        if wanted_error != obs["error"]:
            return [f"expected error {wanted_error}, got {obs['error']}: {obs['text'][:200]}"]
        if "error_contains" in expect and expect["error_contains"] not in obs["text"]:
            problems.append(f"error text does not contain {expect['error_contains']!r}: {obs['text'][:200]}")
        return problems
    if wanted_error is not None:
        return [f"expected error {wanted_error}, call succeeded"]

    data = obs["data"]
    for path, want in expect.get("equals", {}).items():
        got = get_path(data, path)
        if got is _MISSING or diff(got, want):
            problems.append(f"{path}: expected {json.dumps(want)}, got {json.dumps(got, default=str)[:200]}")
    for path, want in expect.get("approx", {}).items():
        got = get_path(data, path)
        if not isinstance(got, (int, float)) or abs(got - want) > 1e-9:
            problems.append(f"{path}: expected approx {want}, got {got!r}")
    for path, n in expect.get("length", {}).items():
        got = get_path(data, path)
        if not isinstance(got, (list, str)) or len(got) != n:
            problems.append(f"{path}: expected length {n}, got {len(got) if isinstance(got, (list, str)) else got!r}")
    for op, cmp in (("gte", lambda a, b: a >= b), ("lte", lambda a, b: a <= b)):
        for path, bound in expect.get(op, {}).items():
            got = get_path(data, path)
            if not isinstance(got, (int, float)) or not cmp(got, bound):
                problems.append(f"{path}: expected {op} {bound}, got {got!r}")
    for path, want in expect.get("contains", {}).items():
        got = get_path(data, path)
        ok = (isinstance(got, str) and isinstance(want, str) and want in got) or (
            isinstance(got, list) and any(not diff(item, want) for item in got)
        )
        if not ok:
            problems.append(f"{path}: expected to contain {json.dumps(want)}, got {json.dumps(got, default=str)[:200]}")
    for path, want in expect.get("not_contains", {}).items():
        got = get_path(data, path)
        if (isinstance(got, str) and want in got) or (isinstance(got, list) and any(not diff(i, want) for i in got)):
            problems.append(f"{path}: expected not to contain {json.dumps(want)}")
    for path, fields in expect.get("each", {}).items():
        got = get_path(data, path)
        if not isinstance(got, list):
            problems.append(f"{path}: expected an array, got {got!r}")
            continue
        for i, item in enumerate(got):
            for fname, fwant in fields.items():
                if get_path(item, fname) != fwant:
                    problems.append(f"{path}.{i}.{fname}: expected {fwant!r}, got {get_path(item, fname)!r}")
    for path, pattern in expect.get("matches", {}).items():
        got = get_path(data, path)
        if not isinstance(got, str) or not re.search(pattern, got):
            problems.append(f"{path}: expected to match /{pattern}/, got {got!r}")
    return problems


# --- running ----------------------------------------------------------------


@dataclass
class StepOutcome:
    label: str
    observation: dict[str, Any]
    problems: list[str] = field(default_factory=list)


def _template(value: Any, saved: dict[str, Any]) -> Any:
    if isinstance(value, str):
        m = re.fullmatch(r"\$\{(\w+)\}", value)
        if m:
            return saved[m.group(1)]
        return re.sub(r"\$\{(\w+)\}", lambda mm: str(saved[mm.group(1)]), value)
    if isinstance(value, list):
        return [_template(v, saved) for v in value]
    if isinstance(value, dict):
        return {k: _template(v, saved) for k, v in value.items()}
    return value


class Runner:
    def __init__(self, spec: dict[str, Any]) -> None:
        self.spec = spec
        self.tool_specs = {t["name"]: t for t in spec["tools"]}
        self.validators = {name: Draft202012Validator({**t["outputSchema"], "$defs": spec["$defs"]}) for name, t in self.tool_specs.items()}

    def expected_schema(self, tool: str, which: str) -> Any:
        return normalize({**self.tool_specs[tool][which], "$defs": self.spec["$defs"]})

    async def _observe(self, session: ClientSession, step: dict[str, Any], saved: dict[str, Any]) -> dict[str, Any]:
        args = _template(step.get("args", {}), saved)
        if "tool" in step:
            result = await session.call_tool(step["tool"], args)
            text = "".join(getattr(c, "text", "") for c in result.content)
            if result.isError:
                return {"kind": "error", "error": error_class(text), "text": text}
            return {"kind": "ok", "data": result.structuredContent}
        if "resource" in step:
            res = await session.read_resource(step["resource"])  # type: ignore[arg-type]
            text = getattr(res.contents[0], "text", "")
            return {"kind": "ok", "data": json.loads(text)}
        if "prompt" in step:
            prompt = await session.get_prompt(step["prompt"], args or None)
            msg = prompt.messages[0]
            return {"kind": "ok", "data": {"role": msg.role, "text": getattr(msg.content, "text", "")}}
        if step.get("list") == "tools":
            tools = (await session.list_tools()).tools
            data: dict[str, Any] = {"names": sorted(t.name for t in tools), "schemas": {}}
            for t in tools:
                data["schemas"][t.name] = {
                    "description": t.description,
                    "input": normalize(t.inputSchema),
                    "output": normalize(t.outputSchema) if t.outputSchema else None,
                }
            return {"kind": "ok", "data": data}
        if step.get("list") == "resources":
            res = (await session.list_resources()).resources
            return {"kind": "ok", "data": {"items": sorted([str(r.uri), r.name, r.mimeType] for r in res)}}
        if step.get("list") == "prompts":
            prompts = (await session.list_prompts()).prompts
            items = [{"name": p.name, "arguments": [[a.name, bool(a.required)] for a in (p.arguments or [])]} for p in prompts]
            return {"kind": "ok", "data": {"items": items}}
        raise ValueError(f"unrecognized step {step}")

    def _contract_checks(self, step: dict[str, Any], obs: dict[str, Any]) -> list[str]:
        """Checks implied by the spec itself, applied to every step."""
        problems: list[str] = []
        if obs["kind"] != "ok":
            return problems
        if "tool" in step:
            for err in self.validators[step["tool"]].iter_errors(obs["data"]):
                problems.append(f"output violates spec schema at {list(err.absolute_path)}: {err.message}")
        if step.get("list") == "tools":
            names = obs["data"]["names"]
            if names != sorted(self.tool_specs):
                problems.append(f"tool set {names} != spec {sorted(self.tool_specs)}")
            for name, got in obs["data"]["schemas"].items():
                if name not in self.tool_specs:
                    continue
                if got["description"] != self.tool_specs[name]["description"]:
                    problems.append(f"{name}: description differs from spec")
                for which, key in (("inputSchema", "input"), ("outputSchema", "output")):
                    for d in diff(got[key], self.expected_schema(name, which), f"{name}.{key}"):
                        problems.append(f"schema drift: {d}")
        return problems

    async def run_case(self, command: list[str], case: dict[str, Any]) -> list[StepOutcome]:
        env = {**os.environ, "HITL_CLOCK": FIXED_CLOCK}
        env.pop("HITL_AUDIT_PATH", None)
        params = StdioServerParameters(command=command[0], args=command[1:], env=env, cwd=str(ROOT))
        outcomes: list[StepOutcome] = []
        saved: dict[str, Any] = {}
        with open(os.devnull, "w") as devnull:
            async with stdio_client(params, errlog=devnull) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    for i, step in enumerate(case["steps"], start=1):
                        what = step.get("tool") or step.get("resource") or step.get("prompt") or step.get("list")
                        label = step.get("label") or f"step {i}: {what}"
                        obs = await self._observe(session, step, saved)
                        expect = _template(step.get("expect", {}), saved)
                        problems = self._contract_checks(step, obs) + check_expectations(obs, expect)
                        if obs["kind"] == "ok":
                            for name, path in step.get("save", {}).items():
                                value = get_path(obs["data"], path)
                                if value is _MISSING:
                                    problems.append(f"cannot save {name}: path {path} missing")
                                else:
                                    saved[name] = value
                        outcomes.append(StepOutcome(label, obs, problems))
        return outcomes


def comparable(obs: dict[str, Any]) -> Any:
    """What two implementations must agree on. Error wording may differ; the class may not."""
    if obs["kind"] == "error":
        return {"error": obs["error"]}
    return obs["data"]


def divergences(results: dict[str, list[StepOutcome]]) -> list[str]:
    names = list(results)
    problems: list[str] = []
    base_name = names[0]
    base = results[base_name]
    for other_name in names[1:]:
        other = results[other_name]
        if len(other) != len(base):
            problems.append(f"{base_name} ran {len(base)} steps, {other_name} ran {len(other)}")
            continue
        for a, b in zip(base, other, strict=True):
            for d in diff(comparable(a.observation), comparable(b.observation)):
                problems.append(f"{a.label}: {base_name} vs {other_name}: {d}")
    return problems
