"""Normalize JSON Schemas so that the spec and both servers' generated schemas can be compared.

Each generator has its own dialect: the Python side adds titles and defaults and uses $defs,
the TypeScript side adds `additionalProperties: false` and safe-integer bounds, the spec uses
`type: [X, "null"]`. None of those differences change which documents validate in our
use, so they are erased here. What survives (types, enums, required, bounds, patterns,
nullability, nesting) is the contract, and that must match exactly.
"""

from __future__ import annotations

import json
from typing import Any

_DROP = {"title", "default", "$schema", "description", "additionalProperties", "propertyNames", "$comment"}
_JS_SAFE_INT = 9007199254740991


def _resolve(ref: str, defs: dict[str, Any]) -> dict[str, Any]:
    prefix = "#/$defs/"
    if not ref.startswith(prefix):
        raise ValueError(f"unsupported $ref {ref}")
    return defs[ref[len(prefix) :]]


def normalize(schema: Any, defs: dict[str, Any] | None = None) -> Any:
    if isinstance(schema, list):
        return [normalize(s, defs) for s in schema]
    if not isinstance(schema, dict):
        return schema
    defs = {**(defs or {}), **schema.get("$defs", {})}
    if "$ref" in schema:
        merged = {**_resolve(schema["$ref"], defs), **{k: v for k, v in schema.items() if k not in ("$ref", "$defs")}}
        return normalize(merged, defs)

    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key in _DROP or key == "$defs":
            continue
        if key == "properties":
            out[key] = {name: normalize(sub, defs) for name, sub in value.items()}
        elif key in ("items", "not"):
            out[key] = normalize(value, defs)
        elif key in ("anyOf", "oneOf"):
            members: list[Any] = []
            for m in value:
                nm = normalize(m, defs)
                # Flatten nested anyOf produced by "nullable of nullable" constructions.
                members.extend(nm["anyOf"] if isinstance(nm, dict) and set(nm) == {"anyOf"} else [nm])
            unique = {json.dumps(m, sort_keys=True): m for m in members}
            out["anyOf"] = [unique[k] for k in sorted(unique)]
        elif key == "required":
            if value:
                out[key] = sorted(value)
        elif key in ("minimum", "maximum") and abs(value) == _JS_SAFE_INT:
            continue
        else:
            out[key] = value

    t = out.get("type")
    if isinstance(t, list):
        rest = {k: v for k, v in out.items() if k != "type"}
        members = [normalize({"type": x, **(rest if x != "null" else {})}) for x in t]
        unique = {json.dumps(m, sort_keys=True): m for m in members}
        return {"anyOf": [unique[k] for k in sorted(unique)]}
    if "anyOf" in out and len(out["anyOf"]) == 1 and len(out) == 1:
        return out["anyOf"][0]
    return out


def diff(a: Any, b: Any, path: str = "$") -> list[str]:
    """Human-readable structural differences between two normalized values."""
    if isinstance(a, dict) and isinstance(b, dict):
        problems: list[str] = []
        for key in sorted(set(a) | set(b)):
            if key not in a:
                problems.append(f"{path}.{key}: missing on left, right has {json.dumps(b[key])[:120]}")
            elif key not in b:
                problems.append(f"{path}.{key}: missing on right, left has {json.dumps(a[key])[:120]}")
            else:
                problems.extend(diff(a[key], b[key], f"{path}.{key}"))
        return problems
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{path}: length {len(a)} != {len(b)}"]
        problems = []
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            problems.extend(diff(x, y, f"{path}[{i}]"))
        return problems
    if isinstance(a, bool) or isinstance(b, bool):
        return [] if a is b else [f"{path}: {a!r} != {b!r}"]
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return [] if a == b else [f"{path}: {a!r} != {b!r}"]
    return [] if a == b else [f"{path}: {json.dumps(a)[:120]} != {json.dumps(b)[:120]}"]
