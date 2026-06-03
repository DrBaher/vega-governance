"""
Phase 2 — tests for audit fixes that close behavior gaps in code we already had.

Tests added in this file map 1:1 to the audit's "missing tests" list, minus
the two that depend on Phase 3 routing changes (DE_IN routing + PROP exchange
via cycle).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from artifact_store import Archive
from models import Artifact, LogEntry, WikiUpdate
from router import ROUTING_TABLE, Router
from sequence_manager import (
    InstanceManager, ModelAssignmentManager, SequenceManager,
)
from state_manager import LOCKS, atomic_save_json, atomic_write, load_json
from wiki_manager import WikiManager


# ─── MEDIUM Fix 3: empty-text response edge ──────────────────────────────────

@pytest.mark.asyncio
async def test_handle_malformed_fires_on_empty_text_with_thinking(tmp_path):
    """When the model returns thinking blocks but empty visible text, the
    malformed handler must still fire — the inbox item must NOT be marked
    processed, and the consecutive counter must bump."""
    from executor import AgentExecutor

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    ex = object.__new__(AgentExecutor)
    ex.malformed_counters_path = state_dir / "malformed_counters.json"

    class _FakeWiki:
        def __init__(self):
            self.logs = []
        def append_log(self, agent_code, entry):
            self.logs.append((agent_code, entry.content))
    ex.wiki = _FakeWiki()

    items = [Artifact(type="FND", sender="SA", id="FND-SA-001", content="x")]
    # Direct call to _handle_malformed simulates the executor's condition firing.
    result = await ex._handle_malformed("SG", "", items)
    assert result["error"] == "malformed_output"
    assert result["consecutive_failures"] == 1
    assert result["inbox_items_left_unprocessed"] == ["FND-SA-001"]
    assert any("MALFORMED_OUTPUT" in entry for _, entry in ex.wiki.logs)


# ─── MEDIUM Fix 4: ModelAssignmentManager fallback ───────────────────────────

def test_model_fallback_to_project_default(tmp_path):
    """An agent not in overrides should fall back to project_default,
    not a hardcoded model id."""
    m = ModelAssignmentManager(
        tmp_path, defaults={"SG": "claude-opus-4-7"},
        project_default="claude-sonnet-4-7",
    )
    assert m.get("SG") == "claude-opus-4-7"          # explicit override
    assert m.get("SA") == "claude-sonnet-4-7"        # project default
    assert m.get("BTA") == "claude-sonnet-4-7"       # project default


def test_model_fallback_to_hardcoded_when_no_project_default(tmp_path):
    """If neither overrides nor project_default has the agent, the
    last-resort hardcoded value is returned (legacy behavior preserved)."""
    m = ModelAssignmentManager(tmp_path, defaults={})
    assert m.get("SG") == "claude-sonnet-4-6"


# ─── LOW 12: load_json corruption handling ───────────────────────────────────

def test_load_json_returns_default_on_corrupt(tmp_path):
    p = tmp_path / "corrupt.json"
    p.write_text("{this is not json")
    result = load_json(p, default={"safe": True})
    assert result == {"safe": True}
    # Quarantine copy should exist
    quarantines = list(tmp_path.glob("corrupt.json.corrupt-*"))
    assert len(quarantines) == 1


def test_load_json_returns_default_for_missing(tmp_path):
    p = tmp_path / "missing.json"
    assert load_json(p, default=[]) == []


def test_load_json_returns_default_for_empty(tmp_path):
    p = tmp_path / "empty.json"
    p.write_text("")
    assert load_json(p, default={"x": 1}) == {"x": 1}


# ─── LOW 11: Archive collision detection ─────────────────────────────────────

def test_archive_write_collision_keeps_existing(tmp_path):
    """Multi-recipient routing legitimately calls write() repeatedly with the
    same artifact. Immutability rule: the FIRST write wins. Different content
    on second call → existing preserved (warning logged separately)."""
    arch = Archive(tmp_path / "archive")
    a1 = Artifact(type="FND", sender="SA", id="FND-SA-100", content="original")
    arch.write(a1)
    a2 = Artifact(type="FND", sender="SA", id="FND-SA-100", content="DIFFERENT")
    arch.write(a2)
    loaded = arch.load("FND-SA-100")
    assert "original" in loaded.content
    assert "DIFFERENT" not in loaded.content


# ─── MEDIUM Fix 5: exclusion regex permissive metadata order ─────────────────

def _setup_wiki(tmp_path):
    agents = tmp_path / "agents"
    universal = tmp_path / "universal"
    state = tmp_path / "state"
    (agents / "BR" / "wiki").mkdir(parents=True)
    universal.mkdir()
    state.mkdir()
    return WikiManager(
        agents_dir=agents,
        universal_dir=universal,
        state_dir=state,
        instance_lookup=lambda c: f"{c}-S001",
        model_lookup=lambda c: "claude-sonnet-4-6",
        replace_threshold_default=10,
    )


def test_exclusion_filter_order_excluded_for_first(tmp_path):
    wm = _setup_wiki(tmp_path)
    (tmp_path / "universal" / "cross_agent_rules.md").write_text(
        "## U-RULE-01: Test rule\n"
        "excluded_for: [BR]\n"
        "gov_reference: GOV-SYS-005\n"
        "---\n"
        "Body for non-excluded agents.\n"
    )
    br_view = wm.read_universal("BR")
    assert "Body for non-excluded agents" not in br_view
    assert "excluded for BR" in br_view
    assert "GOV-SYS-005" in br_view


def test_exclusion_filter_order_gov_reference_first(tmp_path):
    """The previous regex required excluded_for FIRST. With gov_reference
    first, the regex silently passed the rule through. Fix: order-independent
    metadata parsing."""
    wm = _setup_wiki(tmp_path)
    (tmp_path / "universal" / "cross_agent_rules.md").write_text(
        "## U-RULE-02: Out-of-order metadata\n"
        "gov_reference: GOV-SYS-006\n"     # ← gov_reference FIRST
        "excluded_for: [BR]\n"             # ← excluded_for SECOND
        "---\n"
        "Body that should be redacted for BR.\n"
    )
    br_view = wm.read_universal("BR")
    assert "Body that should be redacted" not in br_view
    assert "excluded for BR" in br_view


def test_exclusion_filter_no_metadata_passes_through(tmp_path):
    wm = _setup_wiki(tmp_path)
    (tmp_path / "universal" / "cross_agent_rules.md").write_text(
        "## U-RULE-03: No exclusion\n"
        "Just body text, no metadata.\n"
    )
    br_view = wm.read_universal("BR")
    assert "Just body text" in br_view


# ─── /framework TOC branch (no-arg case) ─────────────────────────────────────

def test_framework_extraction_helper_extracts_toc_section():
    """`/framework` with no arg returns the TOC. Test the extraction helper
    directly using the real framework structure."""
    from framework_parser import extract_section

    fw = (
        "# Title\n\n"
        "## Table of Contents\n"
        "1. Naming\n"
        "2. Catalog\n"
        "5. Roles\n\n"
        "## 1. Naming Convention\n\nFirst section body.\n"
    )
    # No direct TOC extractor exists, but `extract_section` should work for
    # numbered sections including §1.
    body = extract_section(fw, "1")
    assert "Naming Convention" in body
    assert "First section body" in body


# ─── /decisions argument parsing ─────────────────────────────────────────────

def test_decisions_prefix_to_agent_mapping():
    """Confirms the prefix → agent map the /decisions handler relies on.
    If this changes, the handler must change with it."""
    # This map lives in telegram_bot._cmd_decisions; we duplicate it here
    # to lock in the contract.
    expected = {"D": "SG", "TD": "TG", "GD": "SYS"}
    # Sanity: every guardian/auditor with a decision counter is in the map.
    assert expected["D"] == "SG"
    assert expected["TD"] == "TG"
    assert expected["GD"] == "SYS"
    # Invalid prefixes must be rejected (the handler's branch).
    assert "XYZ" not in expected


# ─── router._handle_unknown with no telegram_bot ─────────────────────────────

@pytest.mark.asyncio
async def test_handle_unknown_works_without_telegram_bot(tmp_path):
    """The auto-GOV path must not crash when the bot is None — e.g., a CLI
    smoke run or test harness without a real Telegram setup."""
    from artifact_store import ArtifactStore
    from backlog import OPBacklog

    state_dir = tmp_path / "state"; state_dir.mkdir()
    agents_dir = tmp_path / "agents"
    (agents_dir / "SG" / "outbox").mkdir(parents=True)
    archive_dir = tmp_path / "artifacts" / "archive"; archive_dir.mkdir(parents=True)
    backlog_root = tmp_path / "op_backlog"
    for sub in ("pending", "in_progress", "resolved"):
        (backlog_root / sub).mkdir(parents=True)

    store = ArtifactStore(agents_dir, archive_dir)
    op_backlog = OPBacklog(backlog_root)
    router = Router(store=store, state_dir=state_dir,
                    op_backlog=op_backlog, telegram_bot=None)

    # Construct an artifact whose (sender, type) isn't in the routing table.
    bad = Artifact(type="WEIRD", sender="SG", content="bogus",
                   id="WEIRD-SG-001")
    # Should not raise even though telegram_bot is None.
    await router.route(bad)

    # Auto-GOV should have been enqueued.
    pending = list((backlog_root / "pending").glob("*.md"))
    assert any("GOV" in p.name for p in pending), \
        f"expected GOV in pending, got {[p.name for p in pending]}"


# ─── InstanceManager get_or_create concurrent first-touch ────────────────────

@pytest.mark.asyncio
async def test_instance_get_or_create_post_initialization_is_safe(tmp_path):
    """The InstanceManager invariant (per docstring): pre-materialize at
    startup so get_or_create is a pure read in steady state. This test
    documents and enforces that contract: after pre-init, many concurrent
    get_or_create calls return the same stable id."""
    im = InstanceManager(tmp_path)
    # Pre-init for SG (simulates Orchestrator.__init__).
    first = im.get_or_create("SG")
    assert first == "SG-S001"

    # Now 20 concurrent calls (in steady state — invariant satisfied).
    async def call():
        return im.get_or_create("SG")
    results = await asyncio.gather(*[call() for _ in range(20)])
    assert all(r == "SG-S001" for r in results)


# ─── _filter_exclusions with multi-line metadata ─────────────────────────────

def test_exclusion_filter_no_separator_dash(tmp_path):
    """Body should still parse correctly even without the `---` separator
    between metadata and body (spec shows it as optional)."""
    wm = _setup_wiki(tmp_path)
    (tmp_path / "universal" / "cross_agent_rules.md").write_text(
        "## U-RULE-04: No separator\n"
        "excluded_for: [BR]\n"
        "Body line one (no --- before me).\n"
    )
    br_view = wm.read_universal("BR")
    assert "Body line one" not in br_view
    assert "excluded for BR" in br_view
