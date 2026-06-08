"""
Tier 3 (spec sc4) — /modify redesign.

/modify no longer mints AUTH. AUTH disposition is strictly approve|reject.
/modify (and vega_modify) append a modification directive to the open exchange
cycle, resolve the PROP with a text resolution, and flag SG to produce a revised
PROP in the same cycle.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backlog import make_auth
from models import Artifact
from state_manager import drain_execution_flags
from tests.test_auth_lifecycle import _build_bot


def test_make_auth_disposition_approve_reject_only():
    with pytest.raises(ValueError):
        make_auth("PROP-SG-001", "modify")
    assert make_auth("PROP-SG-001", "approve").disposition == "approve"
    assert make_auth("PROP-SG-001", "reject", "because").disposition == "reject"


@pytest.mark.asyncio
async def test_modify_appends_directive_flags_sg_mints_no_auth(tmp_path):
    bot, state_dir, _agents, archive_dir, op_backlog, cycles = await _build_bot(tmp_path)
    prop = Artifact(type="PROP", sender="SG", id="PROP-SG-007",
                    priority="P2", content="original proposal")
    op_backlog.add(prop)
    cycle = cycles.open_cycle("prop_exchange", prop, ["SG", "OP"], primary_agent="SG")

    msg = await bot.process_modify("PROP-SG-007", "narrow to fixture-only")

    # NO AUTH minted or archived
    assert not list(archive_dir.glob("AUTH-*.md"))
    # directive appended to the cycle
    reloaded = cycles.get_by_id(cycle.id)
    assert any("MODIFICATION DIRECTIVE" in m["content"]
               and "narrow to fixture-only" in m["content"]
               for m in reloaded.messages)
    # PROP resolved with a modify-directed text resolution
    resolved = op_backlog.resolved / "PROP-SG-007.md"
    assert resolved.exists() and "modify-directed" in resolved.read_text()
    # SG flagged for execution against the cycle (cycle stays open)
    assert {"agent": "SG", "cycle_id": cycle.id} in drain_execution_flags(state_dir)
    assert "revised PROP" in msg


@pytest.mark.asyncio
async def test_modify_no_active_cycle(tmp_path):
    bot, *_ = await _build_bot(tmp_path)
    msg = await bot.process_modify("PROP-SG-404", "whatever")
    assert "No active exchange" in msg


def test_vega_modify_wired_to_process_modify():
    src = (Path(__file__).resolve().parent.parent / "mcp_server.py").read_text()
    assert "self.process_modify(artifact_id, instructions)" in src
    # vega_modify must NOT go through the AUTH-minting disposition path anymore
    assert 'process_disposition(artifact_id, "modify"' not in src


def test_modify_help_states_no_auth():
    src = (Path(__file__).resolve().parent.parent / "telegram_bot.py").read_text()
    assert "no AUTH" in src and "revised PROP" in src
