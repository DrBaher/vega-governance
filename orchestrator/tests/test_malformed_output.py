"""
Spec §15.2 — malformed agent output handling.

If a response lacks ARTIFACT/WIKI_UPDATE/LOG_ENTRY blocks:
  1. Raw response logged to agent's log.md
  2. Inbox items NOT marked processed (next tick retries)
  3. Per-agent consecutive counter increments
  4. At 3+ consecutive, OP is notified (we test this via the returned dict;
     main loop handles the actual notification + pause)
A successful parse resets the counter.
"""

import asyncio
import json
from pathlib import Path

import pytest

from executor import _parse_blocks


def test_parse_blocks_returns_empty_for_unstructured_prose():
    """The parser should return nothing for plain text without block headers."""
    parsed = _parse_blocks("Sure, I have analyzed the request and concluded...")
    assert parsed["ARTIFACT"] == []
    assert parsed["WIKI_UPDATE"] == []
    assert parsed["LOG_ENTRY"] == []


def test_parse_blocks_returns_content_for_well_formed():
    """At least one block → not malformed."""
    parsed = _parse_blocks(
        "Reasoning...\n\n### LOG_ENTRY\n---\nSession start"
    )
    assert len(parsed["LOG_ENTRY"]) == 1


@pytest.mark.asyncio
async def test_malformed_counter_bumps_and_resets(tmp_path):
    """The counter file behaves correctly without needing a full executor."""
    from executor import AgentExecutor

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    ex = object.__new__(AgentExecutor)
    ex.malformed_counters_path = state_dir / "malformed_counters.json"
    ex.wiki = None  # not used in counter methods

    assert await ex._bump_malformed_counter("SG") == 1
    assert await ex._bump_malformed_counter("SG") == 2
    assert await ex._bump_malformed_counter("SG") == 3
    # Independent per agent
    assert await ex._bump_malformed_counter("SA") == 1
    # Reset
    await ex._reset_malformed_counter("SG")
    counters = json.loads((state_dir / "malformed_counters.json").read_text())
    assert counters["SG"] == 0
    assert counters["SA"] == 1   # untouched


@pytest.mark.asyncio
async def test_handle_malformed_works_for_sys_with_empty_inbox(tmp_path):
    """Spec §15.2 also applies to SYS. SYS has no inbox items to leave
    unprocessed, but the counter + raw-log invariants still hold."""
    from executor import AgentExecutor

    state_dir = tmp_path / "state"
    state_dir.mkdir()

    class _FakeWiki:
        def __init__(self):
            self.logs = []
        def append_log(self, agent_code, entry):
            self.logs.append((agent_code, entry.content))

    ex = object.__new__(AgentExecutor)
    ex.malformed_counters_path = state_dir / "malformed_counters.json"
    ex.wiki = _FakeWiki()

    # Three consecutive SYS malformed responses
    r1 = await ex._handle_malformed("SYS", "audited, no findings", [])
    r2 = await ex._handle_malformed("SYS", "audited again, still nothing", [])
    r3 = await ex._handle_malformed("SYS", "third strike", [])

    assert r1["consecutive_failures"] == 1
    assert r2["consecutive_failures"] == 2
    assert r3["consecutive_failures"] == 3
    assert r3["inbox_items_left_unprocessed"] == []   # SYS has no inbox items
    # Three MALFORMED_OUTPUT log entries in SYS's wiki
    assert sum(1 for c, _ in ex.wiki.logs if c == "SYS") == 3


@pytest.mark.asyncio
async def test_handle_malformed_returns_error_dict_and_leaves_inbox(tmp_path):
    """_handle_malformed produces the right shape for main loop to act on."""
    from executor import AgentExecutor
    from models import Artifact, LogEntry

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    agents_dir = tmp_path / "agents"
    (agents_dir / "SG" / "wiki").mkdir(parents=True)

    class _FakeWiki:
        def __init__(self):
            self.logs = []
        def append_log(self, agent_code, entry):
            self.logs.append((agent_code, entry.content))

    ex = object.__new__(AgentExecutor)
    ex.malformed_counters_path = state_dir / "malformed_counters.json"
    ex.wiki = _FakeWiki()

    items = [Artifact(type="FND", sender="SA", id="FND-SA-001", content="x")]
    result = await ex._handle_malformed("SG", "some prose with no blocks", items)

    assert result["executed"] is False
    assert result["error"] == "malformed_output"
    assert result["agent"] == "SG"
    assert result["consecutive_failures"] == 1
    assert result["inbox_items_left_unprocessed"] == ["FND-SA-001"]
    # Raw response logged
    assert any("MALFORMED_OUTPUT" in entry for _, entry in ex.wiki.logs)
