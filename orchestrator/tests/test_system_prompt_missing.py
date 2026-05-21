"""
Spec §5.3 — missing system_prompt.md must NOT silently degrade to a generic stub.

Before the fix, _load_system_prompt returned a one-line generic prompt if the
file was missing, which means agents would execute with under-specified
instructions and operators wouldn't know.

After the fix:
  - _load_system_prompt raises FileNotFoundError
  - execute() catches, logs CRITICAL to wiki, returns error dict
  - main loop pauses the agent and notifies OP
"""

import asyncio
from pathlib import Path

import pytest

from executor import AgentExecutor


def test_load_system_prompt_raises_when_missing(tmp_path):
    """Direct test of _load_system_prompt — raises instead of returning a stub."""
    agents_dir = tmp_path / "agents"
    (agents_dir / "SG").mkdir(parents=True)
    # NOTE: NO system_prompt.md created.

    # Build a bare AgentExecutor — only attributes _load_system_prompt reads.
    ex = object.__new__(AgentExecutor)
    ex.agents_dir = agents_dir

    with pytest.raises(FileNotFoundError) as exc:
        ex._load_system_prompt("SG")
    assert "system_prompt.md missing" in str(exc.value)
    assert "SG" in str(exc.value)
    # Operator-actionable hint about how to fix.
    assert "vega-init" in str(exc.value).lower() or "framework" in str(exc.value).lower()


def test_load_system_prompt_returns_content_when_present(tmp_path):
    agents_dir = tmp_path / "agents"
    (agents_dir / "SG").mkdir(parents=True)
    (agents_dir / "SG" / "system_prompt.md").write_text("You are SG. Be careful.")

    ex = object.__new__(AgentExecutor)
    ex.agents_dir = agents_dir

    content = ex._load_system_prompt("SG")
    assert content == "You are SG. Be careful."
