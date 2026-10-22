from __future__ import annotations

import hashlib
import json

import pytest

from hitl_server.canonical import canonical_json, fmt_num, round4
from hitl_server.spec import spec_dir


def _vectors() -> list[dict]:
    return json.loads((spec_dir() / "fixtures" / "canonical_vectors.json").read_text(encoding="utf-8"))["vectors"]


@pytest.mark.parametrize("vector", _vectors(), ids=lambda v: v["name"])
def test_shared_vectors(vector: dict) -> None:
    text = canonical_json(vector["value"])
    assert text == vector["canonical"]
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == vector["sha256"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [(1.0, "1"), (1e-7, "1e-7"), (1e-6, "0.000001"), (1e21, "1e+21"), (1e16, "10000000000000000"), (-0.475, "-0.475"), (0.0, "0")],
)
def test_fmt_num_matches_ecmascript_layout(value: float, expected: str) -> None:
    assert fmt_num(value) == expected


def test_round4_rounds_half_up_like_javascript() -> None:
    # 0.00045 * 10000 is exactly 4.5. Python's round() goes to even (0.0004);
    # JavaScript's Math.round and our round4 go up (0.0005).
    assert round(0.00045, 4) == 0.0004
    assert round4(0.00045) == 0.0005
    assert round4(0.89833333) == 0.8983


def test_rejects_non_finite_and_unknown_types() -> None:
    with pytest.raises(ValueError):
        canonical_json(float("nan"))
    with pytest.raises(TypeError):
        canonical_json({1, 2})
