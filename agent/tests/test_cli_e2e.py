"""End-to-end: the CLI against both real servers over stdio. The TypeScript case needs a build."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TS_ENTRY = ROOT / "typescript" / "dist" / "index.js"


def _run(server: str, *extra: str) -> str:
    cmd = [sys.executable, "-m", "hitl_agent", "--server", server, "--fixed-clock", "2026-03-02T09:00:00Z", *extra]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60, cwd=ROOT)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _head(out: str) -> str:
    m = re.search(r"chain VALID, (\d+) entries, head ([0-9a-f]+)\.\.", out)
    assert m, out
    return m.group(2)


@pytest.mark.parametrize("server", ["python", "typescript"])
def test_cli_auto_reject(server: str) -> None:
    if server == "typescript" and not TS_ENTRY.exists():
        pytest.skip("typescript/dist not built")
    out = _run(server, "--auto-reject")
    assert "7 action(s) need a human decision" in out
    assert "actions: 4 approved, 7 rejected; denied by policy at proposal time: 1" in out


def test_cli_transcripts_identical_across_servers() -> None:
    if not TS_ENTRY.exists():
        pytest.skip("typescript/dist not built")
    py = _run("python", "--auto-reject")
    ts = _run("typescript", "--auto-reject")
    strip = lambda s: s.split("\n", 1)[1]  # first line names the server command  # noqa: E731
    assert strip(py) == strip(ts)
    assert _head(py) == _head(ts)


def test_cli_refuses_interactive_mode_without_tty() -> None:
    proc = subprocess.run([sys.executable, "-m", "hitl_agent"], capture_output=True, text=True, stdin=subprocess.DEVNULL, cwd=ROOT)
    assert proc.returncode != 0
    assert "--auto-reject or --approve-all" in proc.stderr
