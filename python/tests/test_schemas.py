"""The Python server's advertised schemas must normalize to spec/tools.json."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from hitl_server.server import build_server
from hitl_server.spec import load_tools_spec

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "conformance"))
from schema_norm import diff, normalize  # noqa: E402

SPEC = load_tools_spec()


@pytest.fixture(scope="module")
def advertised() -> dict:
    tools = asyncio.run(build_server().list_tools())
    return {t.name: t for t in tools}


def test_tool_set_matches_spec(advertised: dict) -> None:
    assert sorted(advertised) == sorted(t["name"] for t in SPEC["tools"])


@pytest.mark.parametrize("tool", SPEC["tools"], ids=lambda t: t["name"])
def test_schemas_match_spec(advertised: dict, tool: dict) -> None:
    got = advertised[tool["name"]]
    assert got.description == tool["description"]
    for attr, key in (("inputSchema", "inputSchema"), ("outputSchema", "outputSchema")):
        expected = normalize({**tool[key], "$defs": SPEC["$defs"]})
        assert diff(normalize(getattr(got, attr)), expected) == []
