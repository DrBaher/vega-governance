"""
Wiki manager: replace_section diff logging, threshold tracking, exclusion filtering.
"""

import json
from pathlib import Path

import pytest

from models import LogEntry, WikiUpdate
from wiki_manager import WikiManager


def _setup(tmp_path):
    agents = tmp_path / "agents"
    universal = tmp_path / "universal"
    state = tmp_path / "state"
    for code in ["SG", "SA", "BR"]:
        (agents / code / "wiki").mkdir(parents=True)
    universal.mkdir()
    state.mkdir()
    wm = WikiManager(
        agents_dir=agents,
        universal_dir=universal,
        state_dir=state,
        instance_lookup=lambda c: f"{c}-S001",
        model_lookup=lambda c: "claude-sonnet-4-6",
        replace_threshold_default=3,
    )
    return wm, agents, universal, state


@pytest.mark.asyncio
async def test_append_writes_to_file(tmp_path):
    wm, agents, _, _ = _setup(tmp_path)
    await wm.apply_updates("SG", [WikiUpdate(
        file="process_rules.md", action="append", content="New rule body"
    )])
    rules = (agents / "SG" / "wiki" / "process_rules.md").read_text()
    assert "New rule body" in rules


@pytest.mark.asyncio
async def test_replace_section_diff_logged(tmp_path):
    wm, agents, _, _ = _setup(tmp_path)
    rules_path = agents / "SG" / "wiki" / "process_rules.md"
    rules_path.write_text("## Rule 1\nOriginal body\n\n## Rule 2\nOther\n")
    await wm.apply_updates("SG", [WikiUpdate(
        file="process_rules.md", action="replace_section",
        section="Rule 1", content="Replaced body",
        justification="incident X taught me Y",
    )])
    log = (agents / "SG" / "wiki" / "log.md").read_text()
    assert "WIKI_REPLACE" in log
    assert "incident X" in log
    new_rules = rules_path.read_text()
    assert "Replaced body" in new_rules
    assert "Other" in new_rules  # didn't break Rule 2


@pytest.mark.asyncio
async def test_replace_threshold_triggers(tmp_path):
    wm, agents, _, state = _setup(tmp_path)
    rules_path = agents / "SG" / "wiki" / "process_rules.md"
    rules_path.write_text("## Rule 1\nOriginal\n")
    tripped = False
    for _ in range(3):
        result = await wm.apply_updates("SG", [WikiUpdate(
            file="process_rules.md", action="replace_section",
            section="Rule 1", content="x", justification="y",
        )])
        tripped = result or tripped
    assert tripped is True
    assert wm.any_threshold_exceeded() is True
    wm.reset_replace_counters()
    assert wm.any_threshold_exceeded() is False


def test_exclusion_filter(tmp_path):
    wm, _, universal, _ = _setup(tmp_path)
    (universal / "cross_agent_rules.md").write_text(
        "## U-RULE-01: Test\nexcluded_for: [BR, TE]\ngov_reference: GOV-SYS-005\n---\n"
        "Body for non-excluded agents.\n\n"
        "## U-RULE-02: All\n"
        "Universal body.\n"
    )
    sg_view = wm.read_universal("SG")
    assert "Body for non-excluded agents" in sg_view
    br_view = wm.read_universal("BR")
    assert "Body for non-excluded agents" not in br_view
    assert "excluded for BR" in br_view
    assert "GOV-SYS-005" in br_view


def test_read_all_concatenates(tmp_path):
    wm, agents, _, _ = _setup(tmp_path)
    (agents / "SG" / "wiki" / "process_rules.md").write_text(
        "## PR-01\nFirst rule\n\n## PR-02\nSecond rule\n"
    )
    (agents / "SG" / "wiki" / "reasoning_corrections.md").write_text(
        "## RC-05\nA correction\n"
    )
    content, entries = wm.read_all("SG")
    assert "First rule" in content
    assert "A correction" in content
    assert "PR-01" in entries
    assert "PR-02" in entries
    assert "RC-05" in entries
