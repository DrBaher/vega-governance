"""
Phase 3 — tests for Spec v4 / Framework v5 deltas.

Maps to BAHER_CODE_FIX_PROMPT PART 2 items 2.1–2.13 and the "Testing"
checklist at the bottom of that prompt.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from artifact_store import ArtifactStore
from cycle_manager import CycleManager
from framework_parser import AGENT_TIER, TIER_SECTIONS, load_framework_view
from models import Artifact
from op_backlog import OPBacklog
from router import EXTERNAL_TARGETS, OP_BOUND_TYPES, ROUTING_TABLE, Router
from sequence_manager import SequenceManager
from state_manager import load_json


# ─── helpers ─────────────────────────────────────────────────────────────────

def _make_router(tmp_path: Path, telegram_bot=None):
    state_dir = tmp_path / "state"; state_dir.mkdir()
    agents_dir = tmp_path / "agents"
    for code in ("SG", "BR", "SE", "TG", "TE", "SYS"):
        (agents_dir / code / "inbox").mkdir(parents=True)
        (agents_dir / code / "outbox").mkdir(parents=True)
    archive_dir = tmp_path / "artifacts" / "archive"; archive_dir.mkdir(parents=True)
    backlog_root = tmp_path / "op_backlog"
    for sub in ("pending", "in_progress", "resolved"):
        (backlog_root / sub).mkdir(parents=True)
    store = ArtifactStore(agents_dir, archive_dir)
    backlog = OPBacklog(backlog_root)
    return Router(store=store, state_dir=state_dir,
                  op_backlog=backlog, telegram_bot=telegram_bot), \
           store, backlog, state_dir


# ─── 2.1 + 2.12 — DE_IN routing + naming ─────────────────────────────────────

def test_de_in_route_exists():
    assert ("DE", "DE_IN") in ROUTING_TABLE
    assert ROUTING_TABLE[("DE", "DE_IN")] == [{"to": "SG"}]


def test_de_out_route_exists():
    assert ("SG", "DE_OUT") in ROUTING_TABLE
    assert ROUTING_TABLE[("SG", "DE_OUT")] == [{"to": "DE"}]


@pytest.mark.asyncio
async def test_de_in_through_router_archives_and_logs(tmp_path):
    router, store, _, state_dir = _make_router(tmp_path)
    seqs = SequenceManager(state_dir)
    artifact_id = await seqs.next_id("DE", "DE_IN")
    a = Artifact(type="DE_IN", sender="DE", content="answer",
                 recipient="SG", id=artifact_id)
    await router.route(a)
    # SG inbox got the artifact
    assert any(p.name.startswith("DE_IN-DE-") for p in
               (tmp_path / "agents" / "SG" / "inbox").glob("*.md"))
    # Archive contains it
    assert any(p.name.startswith("DE_IN-DE-") for p in
               (tmp_path / "artifacts" / "archive").glob("*.md"))
    # routing_log has an entry
    log = load_json(state_dir / "routing_log.json", default=[])
    assert any(e["artifact_id"] == artifact_id for e in log)


# ─── 2.3 — REQ artifact routing ──────────────────────────────────────────────

def test_req_route_exists():
    assert ("OP", "REQ") in ROUTING_TABLE
    assert ROUTING_TABLE[("OP", "REQ")] == [{"to": "SG"}]


@pytest.mark.asyncio
async def test_req_through_router(tmp_path):
    router, _, _, state_dir = _make_router(tmp_path)
    seqs = SequenceManager(state_dir)
    artifact_id = await seqs.next_id("OP", "REQ")
    a = Artifact(type="REQ", sender="OP", content="please clarify scope X",
                 recipient="SG", id=artifact_id)
    await router.route(a)
    log = load_json(state_dir / "routing_log.json", default=[])
    assert any(e["artifact_id"] == artifact_id and "SG" in e["routed_to"]
               for e in log)


# ─── 2.4 — BRQ via router (already existed; lock in the contract) ────────────

def test_brq_route_exists():
    assert ("EXT", "BRQ") in ROUTING_TABLE
    assert ROUTING_TABLE[("EXT", "BRQ")] == [{"to": "BR"}]


# ─── PROP exchange continuation route + cycle ────────────────────────────────

def test_op_prop_continuation_route_exists():
    assert ("OP", "PROP") in ROUTING_TABLE
    assert ROUTING_TABLE[("OP", "PROP")] == [{"to": "SG"}]


@pytest.mark.asyncio
async def test_prop_continuation_routes_through_router(tmp_path):
    """MEDIUM Fix 2 — _on_text-style PROP continuation goes through the
    router; the synthetic id is minted via SequenceManager."""
    router, store, _, state_dir = _make_router(tmp_path)
    seqs = SequenceManager(state_dir)
    artifact_id = await seqs.next_id("OP", "PROP")
    a = Artifact(type="PROP", sender="OP", content="OP says X",
                 references=["PROP-SG-001"], recipient="SG", id=artifact_id)
    await router.route(a)
    log = load_json(state_dir / "routing_log.json", default=[])
    assert any(e["artifact_id"] == artifact_id and "SG" in e["routed_to"]
               for e in log)


# ─── AUTH in routing_log after disposition ───────────────────────────────────

@pytest.mark.asyncio
async def test_auth_routes_through_router(tmp_path):
    """Spec §13 step 4 — AUTH is archived + logged like any other artifact.

    Verified end-to-end by feeding a synthetic AUTH through the router
    directly. The actual disposition flow lives in the bot; the routing
    behavior is what we're locking in here."""
    router, _, _, state_dir = _make_router(tmp_path)
    seqs = SequenceManager(state_dir)
    auth_id = await seqs.next_id("OP", "AUTH")
    auth = Artifact(type="AUTH", sender="OP", content="approved",
                    references=["PROP-SG-001"], recipient="SG", id=auth_id)
    await router.route(auth)
    log = load_json(state_dir / "routing_log.json", default=[])
    assert any(e["artifact_id"] == auth_id and "SG" in e["routed_to"]
               for e in log)


# ─── 2.1 cycles — DE Q&A opens on DE_OUT, closes on DE_IN ────────────────────

def test_de_qa_cycle_opens_on_de_out(tmp_path):
    cm = CycleManager(tmp_path / "cycles", tmp_path / "archive")
    a = Artifact(type="DE_OUT", sender="SG", content="?", id="DE_OUT-SG-001")
    cm.check_cycle_events(a, recipients=["DE"])
    assert cm.get_active_by_type("de_qa") is not None


def test_de_qa_cycle_closes_on_de_in(tmp_path):
    cm = CycleManager(tmp_path / "cycles", tmp_path / "archive")
    opener = Artifact(type="DE_OUT", sender="SG", content="?",
                      id="DE_OUT-SG-001")
    cm.check_cycle_events(opener, recipients=["DE"])
    assert cm.get_active_by_type("de_qa") is not None
    closer = Artifact(type="DE_IN", sender="DE", content="answer",
                      id="DE_IN-DE-001", references=["DE_OUT-SG-001"])
    cm.check_cycle_events(closer, recipients=["SG"])
    assert cm.get_active_by_type("de_qa") is None


def test_multiple_de_questions_dont_collide(tmp_path):
    """D-ARCH-035: SG non-blocking on DE — multiple DE_OUT can be in flight,
    each tracked by its own cycle id."""
    cm = CycleManager(tmp_path / "cycles", tmp_path / "archive")
    cm.check_cycle_events(
        Artifact(type="DE_OUT", sender="SG", content="q1", id="DE_OUT-SG-001"),
        recipients=["DE"])
    cm.check_cycle_events(
        Artifact(type="DE_OUT", sender="SG", content="q2", id="DE_OUT-SG-002"),
        recipients=["DE"])
    # Two active de_qa cycles
    active_de = [c for c in cm._all_active() if c.type == "de_qa"]
    assert len(active_de) == 2
    # Closing q1 leaves q2 open
    cm.check_cycle_events(
        Artifact(type="DE_IN", sender="DE", content="a1",
                 id="DE_IN-DE-001", references=["DE_OUT-SG-001"]),
        recipients=["SG"])
    active_de = [c for c in cm._all_active() if c.type == "de_qa"]
    assert len(active_de) == 1
    assert active_de[0].opening_artifact == "DE_OUT-SG-002"


# ─── 2.2 cycles — GOV exchange opens on GOV, closes on close_by_artifact ────

def test_gov_exchange_cycle_opens(tmp_path):
    cm = CycleManager(tmp_path / "cycles", tmp_path / "archive")
    gov = Artifact(type="GOV", sender="SYS", content="finding",
                   id="GOV-SYS-001")
    cm.check_cycle_events(gov, recipients=["OP"])
    cycle = cm.get_active_by_type("gov_exchange")
    assert cycle is not None
    assert cycle.opening_artifact == "GOV-SYS-001"


def test_gov_exchange_cycle_closes_by_artifact(tmp_path):
    cm = CycleManager(tmp_path / "cycles", tmp_path / "archive")
    gov = Artifact(type="GOV", sender="SYS", content="finding",
                   id="GOV-SYS-001")
    cm.check_cycle_events(gov, recipients=["OP"])
    assert cm.get_active_by_type("gov_exchange") is not None
    cm.close_by_artifact("GOV-SYS-001")
    assert cm.get_active_by_type("gov_exchange") is None


# ─── 2.8 — TCN cycle closes on certificate=build ─────────────────────────────

def test_tcn_cycle_closes_on_certificate_build(tmp_path):
    cm = CycleManager(tmp_path / "cycles", tmp_path / "archive")
    cm.check_cycle_events(
        Artifact(type="TCN", sender="TG", content="x", id="TCN-TG-001"),
        recipients=["TE"])
    assert cm.get_cycle_by_participants("TG", "TE") is not None

    cm.check_cycle_events(
        Artifact(type="VAL", sender="TG", content="ok",
                 id="VAL-TG-001", certificate="full"),
        recipients=["TE"])
    # Full does NOT close
    assert cm.get_cycle_by_participants("TG", "TE") is not None

    cm.check_cycle_events(
        Artifact(type="VAL", sender="TG", content="ok",
                 id="VAL-TG-002", certificate="build"),
        recipients=["TE"])
    assert cm.get_cycle_by_participants("TG", "TE") is None


def test_tcn_cycle_back_compat_with_certificate_second(tmp_path):
    """Legacy alias: pre-v4 artifacts in the archive used certificate=second."""
    cm = CycleManager(tmp_path / "cycles", tmp_path / "archive")
    cm.check_cycle_events(
        Artifact(type="TCN", sender="TG", content="x", id="TCN-TG-001"),
        recipients=["TE"])
    cm.check_cycle_events(
        Artifact(type="VAL", sender="TG", content="ok",
                 id="VAL-TG-001", certificate="second"),
        recipients=["TE"])
    assert cm.get_cycle_by_participants("TG", "TE") is None


# ─── 2.7 — Tiered framework context ──────────────────────────────────────────

FRAMEWORK_FIXTURE = """\
# Framework

## 1 Document Types
Body of section 1.

## 2 Interaction Catalog
Body of section 2.

## 5 Roles
Body of section 5.

## 9 Interaction Flows
Body of section 9.

## 10 Decision Log
Body of section 10.

## 12.1 Framework Summary
Body of summary (loaded for SA/TA/BR/BTA).
"""


def test_guardian_view_includes_required_sections():
    out_sg = load_framework_view(FRAMEWORK_FIXTURE, "SG", addendum_text="Addendum body")
    for snippet in ("section 1", "section 2", "section 9", "section 10",
                    "Addendum body"):
        assert snippet in out_sg, f"missing: {snippet}"
    # Guardians do NOT see section 5 (other agents' role definitions)
    assert "section 5" not in out_sg


def test_sys_view_includes_role_definitions():
    out_sys = load_framework_view(FRAMEWORK_FIXTURE, "SYS")
    for snippet in ("section 1", "section 2", "section 5", "section 10"):
        assert snippet in out_sys, f"missing: {snippet}"
    # SYS does NOT see test model format or interaction flows
    assert "section 9" not in out_sys


def test_summary_view_uses_section_12_1():
    out_sa = load_framework_view(FRAMEWORK_FIXTURE, "SA")
    assert "Framework Summary" in out_sa
    # Summary tier does NOT see detailed sections
    for n in ("section 1", "section 5", "section 9", "section 10"):
        assert n not in out_sa, f"summary unexpectedly contains: {n}"


def test_minimal_view_is_empty():
    out_se = load_framework_view(FRAMEWORK_FIXTURE, "SE")
    out_te = load_framework_view(FRAMEWORK_FIXTURE, "TE")
    assert out_se == ""
    assert out_te == ""


def test_agent_tier_assignments():
    assert AGENT_TIER["SG"] == "guardian"
    assert AGENT_TIER["TG"] == "guardian"
    assert AGENT_TIER["SYS"] == "sys"
    assert AGENT_TIER["SA"] == "summary"
    assert AGENT_TIER["BR"] == "summary"
    assert AGENT_TIER["SE"] == "minimal"
    assert AGENT_TIER["TE"] == "minimal"


# ─── 2.6 — MCP bearer-token enforcement ──────────────────────────────────────

@pytest.mark.asyncio
async def test_mcp_rejects_without_token(tmp_path):
    aiohttp_client = pytest.importorskip("aiohttp.test_utils")
    from aiohttp import web
    from mcp_server import MCPServer

    class _Cfg:
        MCP_AUTH_TOKEN = "secret-abc"
        MCP_HOST = "127.0.0.1"
        MCP_PORT = 0
        AGENTS = ["SG"]
        AGENTS_DIR = str(tmp_path)

    srv = MCPServer(config=_Cfg(), tools=None)
    app = web.Application()
    app.router.add_get("/health", srv._health)
    app.router.add_get("/mcp/tools", srv._list_tools)
    app.router.add_post("/mcp/call", srv._call_tool)
    async with aiohttp_client.TestClient(aiohttp_client.TestServer(app)) as client:
        # No token → 401
        r = await client.get("/mcp/tools")
        assert r.status == 401
        # Wrong token → 401
        r = await client.get("/mcp/tools",
                              headers={"Authorization": "Bearer nope"})
        assert r.status == 401
        # Correct token → 200
        r = await client.get("/mcp/tools",
                              headers={"Authorization": "Bearer secret-abc"})
        assert r.status == 200
        body = await r.json()
        assert "tools" in body and len(body["tools"]) >= 15
        # Health is unauthenticated
        r = await client.get("/health")
        assert r.status == 200


@pytest.mark.asyncio
async def test_mcp_unknown_tool_returns_404(tmp_path):
    aiohttp_client = pytest.importorskip("aiohttp.test_utils")
    from aiohttp import web
    from mcp_server import MCPServer

    class _Cfg:
        MCP_AUTH_TOKEN = "tok"
    srv = MCPServer(config=_Cfg(), tools=None)
    app = web.Application()
    app.router.add_post("/mcp/call", srv._call_tool)
    async with aiohttp_client.TestClient(aiohttp_client.TestServer(app)) as client:
        r = await client.post(
            "/mcp/call", json={"name": "vega_doesnt_exist", "arguments": {}},
            headers={"Authorization": "Bearer tok"},
        )
        assert r.status == 404


# ─── 2.11 — Telegram notify failure doesn't break routing ────────────────────

@pytest.mark.asyncio
async def test_notify_failure_doesnt_block_archival(tmp_path):
    """Spec v4 §10.2 — Telegram notify is best-effort. A bot raising during
    notify must not prevent the artifact from being archived + logged."""

    class _BoomBot:
        async def notify(self, artifact):
            raise RuntimeError("simulated telegram outage")
        async def notify_external_relay(self, artifact, target):
            raise RuntimeError("simulated telegram outage")

    router, _, backlog, state_dir = _make_router(tmp_path, telegram_bot=_BoomBot())
    seqs = SequenceManager(state_dir)
    # A PROP from SG is OP-bound → goes through _route_to_op which tries notify.
    prop_id = await seqs.next_id("SG", "PROP")
    prop = Artifact(type="PROP", sender="SG", content="x", id=prop_id,
                    priority="P1")
    # Should NOT raise despite the bot blowing up.
    await router.route(prop)
    # Backlog still received it
    assert backlog.find(prop_id) is not None
    # Archive too
    assert (tmp_path / "artifacts" / "archive" / f"{prop_id}.md").exists()
    # And routing_log
    log = load_json(state_dir / "routing_log.json", default=[])
    assert any(e["artifact_id"] == prop_id for e in log)


# ─── 2.10 — Main loop survives a bad tick ────────────────────────────────────
#
# The tick try/except lives in main.run; testing main.run end-to-end is
# integration territory. Instead, lock in the contract that the wrapper exists
# in source so a future refactor doesn't accidentally remove it.

def test_main_loop_has_per_tick_try_except():
    src = (Path(__file__).resolve().parent.parent / "main.py").read_text()
    # The try/except wraps _tick inside `while not self._stopping:` — match
    # the structural shape rather than exact whitespace.
    assert "while not self._stopping:" in src
    assert "await self._tick()" in src
    assert "except Exception" in src
    # And the error message that surfaces to OP
    assert "tick failed" in src.lower()
