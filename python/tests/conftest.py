from __future__ import annotations

import pytest

from hitl_server.spec import FixedClock
from hitl_server.workspace import Workspace

CLOCK_START = "2026-03-02T09:00:00Z"


@pytest.fixture
def ws() -> Workspace:
    return Workspace(FixedClock(CLOCK_START))
