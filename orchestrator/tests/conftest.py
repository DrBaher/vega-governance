"""Shared pytest fixtures for VEGA orchestrator tests."""

import os
import sys
from pathlib import Path

import pytest

# Make the orchestrator package importable when running pytest from the orchestrator/ dir.
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def tmp_state_dir(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    return state


@pytest.fixture
def tmp_agents_dir(tmp_path):
    agents = tmp_path / "agents"
    for code in ["SG", "SA", "SE", "TG", "TA", "TE", "BR", "BTA", "SYS"]:
        (agents / code / "wiki").mkdir(parents=True)
        (agents / code / "inbox").mkdir(parents=True)
        (agents / code / "outbox").mkdir(parents=True)
    return agents
