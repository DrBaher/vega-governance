"""
Phase 1 — audit fixes (VEGA_Audit_Phase2_Verification).

  P1-1  /resolve stores the resolution text in the resolved file
  P1-3  apply_universal_update logs the diff (SYS log + universal/log.md)
  NEW-1 routing_log.cycle_id is populated (not always null)
  NEW-2 execute_sys malformed handler fires on thinking-only empty text
  NEW-3 _handle_unknown notify failure doesn't block archival
  NEW-12 INIT-OP-001 is archived at bootstrap

(P1-2 cycle-internal exchange lives in test_phase3_spec_deltas.py:
 test_prop_exchange_turn_is_cycle_internal + test_op_prop_continuation_route_removed.)
"""

from __future__ import annotations

import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from artifact_store import ArtifactStore
from cycle_manager import CycleManager
from models import Artifact, LogEntry, WikiUpdate
from op_backlog import OPBacklog
from router import Router
from sequence_manager import SequenceManager
from state_manager import load_json
from wiki_manager import WikiManager


# ─── P1-1 — /resolve stores resolution text ──────────────────────────────────

def _make_backlog(tmp_path):
    root = tmp_path / "op_backlog"
    for sub in ("pending", "in_progress", "resolved"):
        (root / sub).mkdir(parents=True)
    return OPBacklog(root), root


def test_resolve_stores_resolution_text(tmp_path):
    """Spec v5 §10.1 (P1-1) — resolution text is appended to the resolved file."""
    bl, root = _make_backlog(tmp_path)
    gov = Artifact(type="GOV", sender="SYS", content="finding body", id="GOV-SYS-001")
    bl.add(gov)
    bl.resolve("GOV-SYS-001", resolution="acknowledged; fixed in PROP-SG-009")
    resolved = (root / "resolved" / "GOV-SYS-001.md").read_text()
    assert "---\nResolution: acknowledged; fixed in PROP-SG-009" in resolved


def test_resolve_without_text_appends_nothing(tmp_path):
    bl, root = _make_backlog(tmp_path)
    gov = Artifact(type="GOV", sender="SYS", content="finding body", id="GOV-SYS-002")
    bl.add(gov)
    bl.resolve("GOV-SYS-002")  # no resolution
    resolved = (root / "resolved" / "GOV-SYS-002.md").read_text()
    assert "Resolution:" not in resolved


# ─── P1-3 — UNIVERSAL writes are diff-logged ─────────────────────────────────

def _make_wiki(tmp_path):
    agents = tmp_path / "agents"
    universal = tmp_path / "universal"
    state = tmp_path / "state"
    for code in ("SG", "SYS"):
        (agents / code / "wiki").mkdir(parents=True)
    universal.mkdir()
    state.mkdir()
    wm = WikiManager(
        agents_dir=agents, universal_dir=universal, state_dir=state,
        instance_lookup=lambda c: f"{c}-S001",
        model_lookup=lambda c: "claude-sonnet-4-6",
        replace_threshold_default=3,
    )
    return wm, agents, universal


def test_universal_replace_is_logged(tmp_path):
    """P1-3 — replace_section on UNIVERSAL logs a diff to SYS log AND universal/log.md."""
    wm, agents, universal = _make_wiki(tmp_path)
    (universal / "cross_agent_rules.md").write_text(
        "## U-RULE-01\nOld body\n\n## U-RULE-02\nUntouched\n"
    )
    wm.apply_universal_update(WikiUpdate(
        file="cross_agent_rules.md", action="replace_section",
        section="U-RULE-01", content="New body", justification="incident Z",
    ))
    sys_log = (agents / "SYS" / "wiki" / "log.md").read_text()
    assert "UNIVERSAL_REPLACE" in sys_log
    assert "U-RULE-01" in sys_log
    assert "incident Z" in sys_log
    universal_log = (universal / "log.md").read_text()
    assert "UNIVERSAL_REPLACE" in universal_log
    # And the write actually happened, without breaking the other section.
    body = (universal / "cross_agent_rules.md").read_text()
    assert "New body" in body
    assert "Untouched" in body


def test_universal_append_is_logged(tmp_path):
    wm, agents, universal = _make_wiki(tmp_path)
    wm.apply_universal_update(WikiUpdate(
        file="case_index.md", action="append", content="U-RC-09 new case note",
    ))
    sys_log = (agents / "SYS" / "wiki" / "log.md").read_text()
    assert "UNIVERSAL_APPEND" in sys_log
    assert "U-RC-09 new case note" in (universal / "case_index.md").read_text()


# ─── NEW-1 — routing_log.cycle_id is populated ───────────────────────────────

def _make_router_with_cycles(tmp_path):
    state_dir = tmp_path / "state"; state_dir.mkdir()
    agents_dir = tmp_path / "agents"
    for code in ("SG", "SE", "SYS"):
        (agents_dir / code / "inbox").mkdir(parents=True)
        (agents_dir / code / "outbox").mkdir(parents=True)
    archive_dir = tmp_path / "artifacts" / "archive"; archive_dir.mkdir(parents=True)
    backlog_root = tmp_path / "op_backlog"
    for sub in ("pending", "in_progress", "resolved"):
        (backlog_root / sub).mkdir(parents=True)
    store = ArtifactStore(agents_dir, archive_dir)
    backlog = OPBacklog(backlog_root)
    cycles = CycleManager(tmp_path / "cycles", archive_dir)
    router = Router(store=store, state_dir=state_dir, op_backlog=backlog,
                    telegram_bot=None, cycles=cycles)
    return router, store, backlog, cycles, state_dir


@pytest.mark.asyncio
async def test_routing_log_cycle_id_populated(tmp_path):
    """NEW-1 / Spec §17.2 — an artifact that belongs to an active cycle gets its
    cycle_id stamped in routing_log (not the unconditional null)."""
    router, store, _, cycles, state_dir = _make_router_with_cycles(tmp_path)
    prop = Artifact(type="PROP", sender="SG", content="proposal",
                    recipient="OP", id="PROP-SG-001")
    cycle = cycles.open_cycle("prop_exchange", prop, ["SG", "OP"], primary_agent="SG")
    # AUTH from OP closing the exchange — routes to SG, belongs to the cycle.
    auth = Artifact(type="AUTH", sender="OP", content="approve",
                    references=["PROP-SG-001"], recipient="SG", id="AUTH-OP-001")
    await router.route(auth)
    log = load_json(state_dir / "routing_log.json", default=[])
    entry = next(e for e in log if e["artifact_id"] == "AUTH-OP-001")
    assert entry["cycle_id"] == cycle.id


@pytest.mark.asyncio
async def test_routing_log_cycle_id_null_when_no_cycle(tmp_path):
    """No active cycle → cycle_id stays null (unchanged behavior)."""
    router, _, _, _, state_dir = _make_router_with_cycles(tmp_path)
    req = Artifact(type="REQ", sender="OP", content="adhoc", recipient="SG",
                   id="REQ-OP-001")
    await router.route(req)
    log = load_json(state_dir / "routing_log.json", default=[])
    entry = next(e for e in log if e["artifact_id"] == "REQ-OP-001")
    assert entry["cycle_id"] is None


# ─── NEW-2 — execute_sys malformed on thinking-only empty text ────────────────

@pytest.mark.asyncio
async def test_execute_sys_malformed_on_thinking_only(tmp_path):
    """NEW-2 — a SYS audit returning thinking blocks but empty text (no parseable
    blocks) must hit _handle_malformed, not be silently consumed."""
    from executor import AgentExecutor

    ex = object.__new__(AgentExecutor)
    ex.config = types.SimpleNamespace(AGENTS=["SG", "SYS"], PROMPT_CACHING_ENABLED=False)
    ex._load_system_prompt = lambda code: "sys prompt"
    ex.wiki = MagicMock()
    ex.wiki.read_all.return_value = ("", [])
    ex.wiki.read_universal.return_value = ""
    ex.wiki.read_all_agents.return_value = {}
    ex.wiki.read_all_logs.return_value = {}
    ex.wiki.apply_updates = AsyncMock(return_value=False)
    ex._load_framework = lambda: ""
    ex.store = MagicMock()
    ex.store.get_recent_artifacts.return_value = []
    ex._load_recent_execution_log = lambda since=None: []
    ex.models = MagicMock(); ex.models.get.return_value = "claude-opus-4-7"
    ex.instances = MagicMock(); ex.instances.get_or_create.return_value = "SYS-S001"

    # Fake API response: a thinking block, no text block.
    thinking_block = types.SimpleNamespace(type="thinking", thinking="deep thoughts")
    fake_response = types.SimpleNamespace(content=[thinking_block], usage=None)
    ex._call_with_retry = AsyncMock(return_value=(fake_response, None))

    handled = AsyncMock(return_value={"error": "malformed_output", "agent": "SYS"})
    ex._handle_malformed = handled
    ex._reset_malformed_counter = AsyncMock()

    result = await ex.execute_sys(since=None)

    handled.assert_awaited_once()
    assert result["error"] == "malformed_output"
    ex._reset_malformed_counter.assert_not_awaited()


# ─── NEW-3 — _handle_unknown notify failure doesn't block archival ───────────

@pytest.mark.asyncio
async def test_handle_unknown_notify_failure_doesnt_block_archival(tmp_path):
    """NEW-3 / Spec §10.2 — a Telegram outage during the auto-GOV notify must not
    prevent the offending artifact from being archived + logged."""

    class _BoomBot:
        async def notify(self, artifact):
            raise RuntimeError("simulated telegram outage")

    router, store, backlog, cycles, state_dir = _make_router_with_cycles(tmp_path)
    router.telegram_bot = _BoomBot()
    # An artifact with no routing-table entry → _handle_unknown / auto-GOV path.
    orphan = Artifact(type="DOC", sender="SG", content="unexpected type",
                      recipient="SG", id="DOC-SG-001")
    # Must NOT raise despite the bot blowing up during the auto-GOV notify.
    await router.route(orphan)
    # Offending artifact archived
    assert (tmp_path / "artifacts" / "archive" / "DOC-SG-001.md").exists()
    # routing_log recorded the violation
    log = load_json(state_dir / "routing_log.json", default=[])
    assert any(e["artifact_id"] == "DOC-SG-001"
               and e["routed_to"] == ["GOV_VIOLATION"] for e in log)
    # The auto-GOV still landed in the backlog
    assert any(p.name.startswith("GOV-SYS-AUTO-")
               for p in (tmp_path / "op_backlog" / "pending").glob("*.md"))


# ─── NEW-12 — INIT-OP-001 is archived ────────────────────────────────────────

def test_init_artifact_is_archived(tmp_path):
    """NEW-12 / Spec §14 — bootstrap archives INIT-OP-001 (router not running yet)
    before delivering it to SG's inbox."""
    import sys
    # main.py does `import config` at module load; tests don't ship config.py, so
    # register a stub before importing main (mirrors test_imports' skip rationale).
    sys.modules.setdefault("config", types.ModuleType("config"))
    import main as main_mod

    init_file = tmp_path / "init.md"
    init_file.write_text("Bootstrap directive: build the LOINC resolver.")
    agents_dir = tmp_path / "agents"
    archive_dir = tmp_path / "artifacts" / "archive"
    (agents_dir / "SG" / "inbox").mkdir(parents=True)
    archive_dir.mkdir(parents=True)
    cfg = types.SimpleNamespace(AGENTS_DIR=agents_dir, ARTIFACTS_DIR=archive_dir)

    main_mod._place_initial_input(cfg, str(init_file))

    assert (archive_dir / "INIT-OP-001.md").exists(), "INIT must be archived"
    assert (agents_dir / "SG" / "inbox" / "INIT-OP-001.md").exists(), "INIT must reach SG inbox"


# ─── P1-2 — execute_cycle_turn appends the assistant reply (no artifact) ──────

@pytest.mark.asyncio
async def test_execute_cycle_turn_appends_assistant_reply(tmp_path):
    """P1-2 — a flagged cycle turn runs the partner agent against the cycle
    history, records the reply as an assistant turn, and returns the
    conversational text for relay — with NO artifact when the agent only chats."""
    from executor import AgentExecutor

    cycles = CycleManager(tmp_path / "cycles", tmp_path / "artifacts" / "archive")
    prop = Artifact(type="PROP", sender="SG", content="proposal",
                    recipient="OP", id="PROP-SG-001")
    cycle = cycles.open_cycle("prop_exchange", prop, ["SG", "OP"], primary_agent="SG")
    cycles.append_turn(cycle, "Why option A over B?", "OP")  # OP exchange turn

    ex = object.__new__(AgentExecutor)
    ex.config = types.SimpleNamespace(PROMPT_CACHING_ENABLED=False,
                                      CYCLE_CONTEXT_WARNING=0.4)
    ex.cycles = cycles
    ex._load_system_prompt = lambda code: "sg prompt"
    ex.wiki = MagicMock()
    ex.wiki.read_all.return_value = ("", [])
    ex.wiki.read_universal.return_value = ""
    ex.wiki.apply_updates = AsyncMock(return_value=False)
    ex._load_scope = lambda code: ""
    ex._load_framework_view = lambda code: ""
    ex.models = MagicMock(); ex.models.get.return_value = "claude-opus-4-7"
    ex.instances = MagicMock(); ex.instances.get_or_create.return_value = "SG-S001"
    text_block = types.SimpleNamespace(type="text", text="Because A is cheaper.")
    fake_response = types.SimpleNamespace(content=[text_block], usage=None)
    ex._call_with_retry = AsyncMock(return_value=(fake_response, None))
    ex._reset_malformed_counter = AsyncMock()
    ex._log_execution = AsyncMock(return_value="exec-1")
    ex._update_artifact_index = AsyncMock()
    ex.store = MagicMock()

    result = await ex.execute_cycle_turn("SG", cycle.id)

    assert result["executed"] is True
    assert result["response_text"] == "Because A is cheaper."
    assert result["artifacts"] == []
    # Cycle now has OP user turn + SG assistant turn.
    reloaded = cycles.get_by_id(cycle.id)
    assert [m["role"] for m in reloaded.messages] == ["user", "assistant"]
    assert reloaded.messages[-1]["content"] == "Because A is cheaper."
