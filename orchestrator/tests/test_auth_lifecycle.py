"""
AUTH lifecycle end-to-end (Spec §7.2 + §13 step 4).

Invariants this test enforces:
1. AUTH gets its ID from SequenceManager — not by string-splicing the PROP ID.
2. AUTH is routed through the router (not delivered directly to SG inbox).
3. AUTH is archived immutably to artifacts/archive/.
4. AUTH appears in routing_log.json.
5. AUTH is delivered to SG's inbox.
6. The PROP it responds to is moved from pending/in_progress → resolved.
7. The prop_exchange cycle closes when AUTH is processed.

If any of these break, the auditable record (Manifesto: "Your artifacts outlive
your session") is incomplete and SYS audit is structurally blind to OP decisions.
"""

import asyncio
from pathlib import Path

import pytest

from artifact_store import ArtifactStore
from cycle_manager import CycleManager
from models import Artifact
from backlog import OPBacklog
from router import ROUTING_TABLE, Router
from sequence_manager import SequenceManager
from state_manager import load_json


# ─── Routing-level test: AUTH from OP gets archived + logged ─────────────────

@pytest.mark.asyncio
async def test_auth_routed_appears_in_archive_and_routing_log(tmp_path):
    """Spec §7.2: AUTH is archived immutably. §13 step 4: AUTH goes through router."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    agents_dir = tmp_path / "agents"
    (agents_dir / "SG" / "inbox").mkdir(parents=True)
    (agents_dir / "SG" / "outbox").mkdir(parents=True)
    archive_dir = tmp_path / "artifacts" / "archive"
    archive_dir.mkdir(parents=True)

    store = ArtifactStore(agents_dir, archive_dir)
    router = Router(store=store, state_dir=state_dir,
                    op_backlog=None, telegram_bot=None)

    auth = Artifact(
        type="AUTH",
        sender="OP",
        recipient="SG",
        content="Approved.",
        id="AUTH-OP-001",
        references=["PROP-SG-001"],
        disposition="approve",
    )
    await router.route(auth)

    # Invariant 3: archived
    assert (archive_dir / "AUTH-OP-001.md").exists(), \
        "AUTH must be archived immutably (Spec §7.2)"

    # Invariant 5: delivered to SG inbox
    assert (agents_dir / "SG" / "inbox" / "AUTH-OP-001.md").exists(), \
        "AUTH must be delivered to SG inbox (Spec §13)"

    # Invariant 4: routing_log has it with the spec §17.2 shape
    routing_log = load_json(state_dir / "routing_log.json", default=[])
    assert isinstance(routing_log, list)
    auth_entries = [e for e in routing_log if e.get("artifact_id") == "AUTH-OP-001"]
    assert len(auth_entries) == 1, \
        "AUTH must appear exactly once in routing_log (Spec §13 step 5)"
    entry = auth_entries[0]
    assert "SG" in entry["routed_to"]
    # Spec §17.2 — cycle_id is a required field (null is acceptable).
    assert "cycle_id" in entry
    # Per the shape, archived must be true.
    assert entry.get("archived") is True


def test_routing_table_has_op_auth_entry():
    """The (OP, AUTH) routing key must exist or AUTH bypasses the router."""
    assert ("OP", "AUTH") in ROUTING_TABLE
    assert [r["to"] for r in ROUTING_TABLE[("OP", "AUTH")]] == ["SG"]


# ─── End-to-end test: TelegramBot.process_disposition ────────────────────────

class _StubConfig:
    """Minimal config the bot reads at construction time."""
    TELEGRAM_BOT_TOKEN = ""
    TELEGRAM_OP_CHAT_ID = ""
    AGENTS = ["SG", "SA", "SE", "TG", "TA", "TE", "BR", "BTA", "SYS"]
    AGENTS_DIR = ""  # set per-test


async def _build_bot(tmp_path):
    """Construct a TelegramBot with real dependencies and a stub config."""
    from sequence_manager import (
        InstanceManager, ModelAssignmentManager,
    )
    from telegram_bot import TelegramBot
    from wiki_manager import WikiManager

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    agents_dir = tmp_path / "agents"
    universal_dir = tmp_path / "universal"
    universal_dir.mkdir()
    archive_dir = tmp_path / "artifacts" / "archive"
    backlog_root = tmp_path / "op_backlog"
    cycles_dir = tmp_path / "cycles" / "active"
    archive_dir.mkdir(parents=True)
    cycles_dir.mkdir(parents=True)
    for code in _StubConfig.AGENTS:
        (agents_dir / code / "inbox").mkdir(parents=True)
        (agents_dir / code / "outbox").mkdir(parents=True)
        (agents_dir / code / "wiki").mkdir(parents=True)

    cfg = _StubConfig()
    cfg.AGENTS_DIR = str(agents_dir)

    store = ArtifactStore(agents_dir, archive_dir)
    cycles = CycleManager(cycles_dir, archive_dir)
    sequences = SequenceManager(state_dir)
    instances = InstanceManager(state_dir)
    models = ModelAssignmentManager(state_dir, defaults={})
    wiki = WikiManager(
        agents_dir=agents_dir, universal_dir=universal_dir, state_dir=state_dir,
        instance_lookup=instances.get_or_create, model_lookup=models.get,
    )
    op_backlog = OPBacklog(backlog_root)

    async def _noop_sys(req=None):
        return {"executed": False}

    bot = TelegramBot(
        config=cfg, op_backlog=op_backlog, store=store, cycles=cycles,
        wiki=wiki, instances=instances, models_mgr=models, sequences=sequences,
        sys_trigger=_noop_sys,
        agent_pause=lambda c: None, agent_resume=lambda c: None,
        state_dir=state_dir,
    )
    bot.router = Router(store=store, state_dir=state_dir,
                        op_backlog=op_backlog, telegram_bot=bot)
    return bot, state_dir, agents_dir, archive_dir, op_backlog, cycles


@pytest.mark.asyncio
async def test_process_disposition_archives_auth_and_routes_to_sg(tmp_path):
    """The /approve happy path — invariants 1–6."""
    bot, state_dir, agents_dir, archive_dir, op_backlog, cycles = \
        await _build_bot(tmp_path)

    # Seed: a PROP in the OP backlog
    prop = Artifact(type="PROP", sender="SG", id="PROP-SG-005",
                    priority="P1", content="Recommend accept", references=[])
    op_backlog.add(prop)
    assert (op_backlog.pending / "PROP-SG-005.md").exists()

    # Open a prop_exchange cycle (as the orchestrator would when the PROP was produced)
    cycles.check_cycle_events(prop, recipients=["OP"])
    assert cycles.get_cycle_by_participants("SG", "OP") is not None

    # Act: OP /approves
    reply = await bot.process_disposition("PROP-SG-005", "approve")

    # Invariant 1: AUTH ID from SequenceManager — first AUTH = AUTH-OP-001
    assert "AUTH-OP-001" in reply, f"Unexpected reply: {reply}"

    # Invariant 3: archived
    assert (archive_dir / "AUTH-OP-001.md").exists()

    # Invariant 4: routing_log
    routing_log = load_json(state_dir / "routing_log.json", default=[])
    assert any(e.get("artifact_id") == "AUTH-OP-001" for e in routing_log)

    # Invariant 5: SG inbox
    assert (agents_dir / "SG" / "inbox" / "AUTH-OP-001.md").exists()

    # Invariant 6: PROP resolved
    assert not (op_backlog.pending / "PROP-SG-005.md").exists()
    assert (op_backlog.resolved / "PROP-SG-005.md").exists()

    # Invariant 7: prop_exchange cycle closed
    assert cycles.get_cycle_by_participants("SG", "OP") is None


@pytest.mark.asyncio
async def test_process_disposition_increments_auth_sequence(tmp_path):
    """Each AUTH gets its own sequence number (Spec §9). Two approvals → 001, 002."""
    bot, _, _, archive_dir, op_backlog, cycles = await _build_bot(tmp_path)

    p1 = Artifact(type="PROP", sender="SG", id="PROP-SG-001",
                  priority="P2", content="x")
    p2 = Artifact(type="PROP", sender="SG", id="PROP-SG-002",
                  priority="P2", content="y")
    op_backlog.add(p1)
    op_backlog.add(p2)
    cycles.check_cycle_events(p1, recipients=["OP"])

    r1 = await bot.process_disposition("PROP-SG-001", "approve")
    r2 = await bot.process_disposition("PROP-SG-002", "modify", "narrow to fixture")

    assert "AUTH-OP-001" in r1
    assert "AUTH-OP-002" in r2
    assert (archive_dir / "AUTH-OP-001.md").exists()
    assert (archive_dir / "AUTH-OP-002.md").exists()


@pytest.mark.asyncio
async def test_process_disposition_no_backlog_returns_error(tmp_path):
    """Approving a nonexistent PROP returns a clear error, doesn't crash."""
    bot, _, _, _, _, _ = await _build_bot(tmp_path)
    reply = await bot.process_disposition("PROP-SG-999", "approve")
    assert "No backlog item" in reply
