"""
Phase 2 — v6/v5 multi-role migration tests.

Covers the BAHER_V6 testing checklist items 8-18 plus the supporting machinery:
  - RoleManager lifecycle: assign(2FA) → invite → activate → permanent token (14)
  - role_events.jsonl audit trail written (15)
  - break-glass recovery resets Admin OP (16)
  - GOV → admin_backlog, auto-GOV → admin_backlog (12, 13)
  - role-scoped MCP: lobby / _PENDING / OP / invalid (8, 9, 10, 11)
  - DE direct / EXT direct via MCP tools route DE_IN→SG / BRQ→BR (17, 18)
  - CycleManager activity/pending/history; framework v6 chain.
"""

from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from artifact_store import ArtifactStore
from backlog import Backlog
from cycle_manager import CycleManager
from models import Artifact
from role_manager import RoleManager
from router import Router
from sequence_manager import SequenceManager
from state_manager import load_json


# ─── Builders ─────────────────────────────────────────────────────────────────

def _make_role_manager(tmp_path) -> RoleManager:
    cfg_dir = tmp_path / "config"
    return RoleManager(
        roles_file=cfg_dir / "roles.json",
        invites_dir=cfg_dir / "invites",
        events_file=tmp_path / "state" / "role_events.jsonl",
        recovery_file=cfg_dir / "recovery.hash",
        invite_expiry=3600, otp_expiry=300,
    )


def _make_router(tmp_path):
    state_dir = tmp_path / "state"; state_dir.mkdir(exist_ok=True)
    agents_dir = tmp_path / "agents"
    for code in ("SG", "SE", "TG", "TE", "BR", "BTA", "SA", "TA", "SYS"):
        (agents_dir / code / "inbox").mkdir(parents=True, exist_ok=True)
        (agents_dir / code / "outbox").mkdir(parents=True, exist_ok=True)
    archive = tmp_path / "artifacts" / "archive"; archive.mkdir(parents=True, exist_ok=True)
    store = ArtifactStore(agents_dir, archive)
    op_backlog = Backlog(tmp_path / "op_backlog")
    admin_backlog = Backlog(tmp_path / "admin_backlog")
    cycles = CycleManager(tmp_path / "cycles", archive)
    router = Router(store=store, state_dir=state_dir, op_backlog=op_backlog,
                    admin_backlog=admin_backlog, telegram_bot=None, cycles=cycles)
    return router, store, op_backlog, admin_backlog, cycles, state_dir


# ─── 14 + 15 — Role lifecycle + audit trail ──────────────────────────────────

@pytest.mark.asyncio
async def test_role_assign_invite_activate_lifecycle(tmp_path):
    rm = _make_role_manager(tmp_path)
    # Admin initiates assign (2FA) — returns an OTP, no role yet.
    otp = rm.initiate_assign("admin-chat", "Hassan", "de-chat-123", "DE", "LOINC")
    assert rm.has_pending_action("admin-chat")
    assert rm.verify_token("anything") is None
    # Confirm with the OTP → executes assign → creates an invite.
    assert await rm.confirm_pending("admin-chat", otp=otp) is True
    invites = list((tmp_path / "config" / "invites").glob("*.json"))
    assert len(invites) == 1
    code = json.loads(invites[0].read_text())["code"]
    # Activate the invite → permanent token; role now resolvable.
    token = rm.complete_activation(code)
    assert token and token.startswith("vega_")
    info = rm.verify_token(token)
    assert info is not None and info["role"] == "DE"
    assert rm.get_role_by_telegram("de-chat-123") == "DE"
    # Invite consumed.
    assert not invites[0].exists()


def test_role_events_jsonl_written(tmp_path):
    rm = _make_role_manager(tmp_path)
    rm.create_invite("EXT", "ext-chat", "Sara")
    events = (tmp_path / "state" / "role_events.jsonl").read_text().splitlines()
    assert events
    rec = json.loads(events[-1])
    assert rec["action"] == "invite_created"
    assert rec["target_role"] == "EXT"
    assert rec["timestamp"].endswith("Z")


@pytest.mark.asyncio
async def test_wrong_otp_does_not_execute(tmp_path):
    rm = _make_role_manager(tmp_path)
    rm.initiate_assign("admin-chat", "X", "tg", "OP", "P")
    ok = await rm.confirm_pending("admin-chat", otp="000000")
    assert ok is False
    assert not list((tmp_path / "config" / "invites").glob("*.json"))
    # A 2fa_failed event is recorded.
    events = [json.loads(l) for l in
              (tmp_path / "state" / "role_events.jsonl").read_text().splitlines()]
    assert any(e["action"] == "2fa_failed" for e in events)


# ─── 16 — Break-glass recovery ───────────────────────────────────────────────

def test_break_glass_recovery_resets_admin_op(tmp_path):
    rm = _make_role_manager(tmp_path)
    # Seed an existing Admin OP.
    code = rm.create_invite("ADMIN_OP", "old-admin", "Old")
    old_token = rm.complete_activation(code)
    assert rm.verify_token(old_token)["role"] == "ADMIN_OP"
    # Set a recovery key, then recover.
    RoleManager.set_recovery_key(rm.recovery_file, "break-glass-key")
    assert rm.execute_recovery("wrong-key", "new-admin") is None
    new_code = rm.execute_recovery("break-glass-key", "new-admin")
    assert new_code and new_code.startswith("VEGA-ADMIN_OP-")
    # Old Admin OP token is dead; a fresh invite exists for the new admin.
    assert rm.verify_token(old_token) is None
    events = [json.loads(l) for l in
              (tmp_path / "state" / "role_events.jsonl").read_text().splitlines()]
    assert any(e["action"] == "recovery_executed" and e.get("result") == "success"
               for e in events)


# ─── 12 + 13 — GOV / auto-GOV route to admin_backlog ─────────────────────────

@pytest.mark.asyncio
async def test_gov_routes_to_admin_backlog(tmp_path):
    router, _, op_backlog, admin_backlog, _, _ = _make_router(tmp_path)
    gov = Artifact(type="GOV", sender="SYS", content="audit finding",
                   id="GOV-SYS-001", priority="P1")
    await router.route(gov)
    assert admin_backlog.find("GOV-SYS-001") is not None
    assert op_backlog.find("GOV-SYS-001") is None


@pytest.mark.asyncio
async def test_auto_gov_routes_to_admin_backlog(tmp_path):
    router, _, op_backlog, admin_backlog, _, _ = _make_router(tmp_path)
    # Unknown routing key → auto-GOV.
    orphan = Artifact(type="DOC", sender="SG", content="unexpected",
                      recipient="SG", id="DOC-SG-009")
    await router.route(orphan)
    assert any(p.name.startswith("GOV-SYS-AUTO-")
               for p in (tmp_path / "admin_backlog" / "pending").glob("*.md"))
    assert not list((tmp_path / "op_backlog" / "pending").glob("*.md"))


# ─── 17 + 18 — DE / EXT direct via MCP tools ─────────────────────────────────

def _make_mcptools(tmp_path):
    from mcp_server import MCPTools
    router, store, op_backlog, admin_backlog, cycles, state_dir = _make_router(tmp_path)
    sequences = SequenceManager(state_dir)

    class _Cfg:
        AGENTS = ["SG", "BR", "SYS"]
        AGENTS_DIR = str(tmp_path / "agents")
        STATE_DIR = str(state_dir)
    tools = MCPTools(
        config=_Cfg(), op_backlog=op_backlog, store=store, cycles=cycles,
        wiki=None, instances=None, models_mgr=None, sequences=sequences,
        sys_trigger=None, agent_pause=None, agent_resume=None,
        process_disposition=None, state_dir=state_dir, router=router,
        admin_backlog=admin_backlog,
    )
    return tools, tmp_path


@pytest.mark.asyncio
async def test_vega_history_returns_artifact_body_and_routing(tmp_path):
    """vega_history must return the artifact BODY (from the archive) — not just
    routing_log records — so OP can actually read a PROP/SCN."""
    tools, root = _make_mcptools(tmp_path)
    tools.config.ARTIFACTS_DIR = str(root / "artifacts" / "archive")
    prop = Artifact(type="PROP", sender="SG", content="the full proposal body",
                    recipient="OP", id="PROP-SG-099")
    tools.store.archive_artifact(prop)
    out = await tools.vega_history("PROP-SG-099")
    assert out["found"] is True
    assert "the full proposal body" in out["content"]      # body, not metadata
    assert isinstance(out["routing"], list)                # routing still included
    # Unknown id → found False, no crash.
    missing = await tools.vega_history("PROP-SG-404")
    assert missing["found"] is False and missing["content"] is None


@pytest.mark.asyncio
async def test_de_respond_creates_de_in_to_sg(tmp_path):
    tools, root = _make_mcptools(tmp_path)
    res = await tools.vega_de_respond("DE_OUT-SG-001", "the loinc code is 1234-5")
    assert "sent to SG" in res
    inbox = list((root / "agents" / "SG" / "inbox").glob("DE_IN-DE-*.md"))
    assert len(inbox) == 1


@pytest.mark.asyncio
async def test_ext_submit_creates_brq_to_br(tmp_path):
    tools, root = _make_mcptools(tmp_path)
    res = await tools.vega_ext_submit("build passed: 42/42")
    assert "submitted to BR" in res
    inbox = list((root / "agents" / "BR" / "inbox").glob("BRQ-EXT-*.md"))
    assert len(inbox) == 1


# ─── 8 / 9 / 10 / 11 — role-scoped MCP auth ──────────────────────────────────

def _seed_role(rm: RoleManager, role: str, telegram_id: str) -> str:
    code = rm.create_invite(role, telegram_id, f"{role}-person")
    return rm.complete_activation(code)


@pytest.mark.asyncio
async def test_mcp_role_scoped_tool_visibility(tmp_path):
    aiohttp_client = pytest.importorskip("aiohttp.test_utils")
    from aiohttp import web
    from mcp_server import MCPServer

    rm = _make_role_manager(tmp_path)
    op_token = _seed_role(rm, "OP", "op-chat")
    admin_token = _seed_role(rm, "ADMIN_OP", "admin-chat")

    srv = MCPServer(config=types.SimpleNamespace(), tools=None, role_manager=rm)
    app = web.Application()
    app.router.add_get("/mcp/tools", srv._list_tools)
    app.router.add_post("/mcp/call", srv._call_tool)
    async with aiohttp_client.TestClient(aiohttp_client.TestServer(app)) as client:
        # 8 — lobby (no token): only vega_request_access
        r = await client.get("/mcp/tools")
        assert r.status == 200
        names = [t["name"] for t in (await r.json())["tools"]]
        assert names == ["vega_request_access"]

        # 11 — invalid token → 401
        r = await client.get("/mcp/tools", headers={"Authorization": "Bearer bogus"})
        assert r.status == 401

        # 10 — OP token: scope tools, NO admin tools
        r = await client.get("/mcp/tools",
                             headers={"Authorization": f"Bearer {op_token}"})
        names = [t["name"] for t in (await r.json())["tools"]]
        assert "vega_approve" in names
        assert "vega_sys" not in names and "vega_assign_role" not in names
        # Enforcement on call, too: OP cannot call an admin tool → 403
        r = await client.post("/mcp/call",
                              json={"name": "vega_sys", "arguments": {}},
                              headers={"Authorization": f"Bearer {op_token}"})
        assert r.status == 403

        # Admin token: governance + role management visible
        r = await client.get("/mcp/tools",
                             headers={"Authorization": f"Bearer {admin_token}"})
        names = [t["name"] for t in (await r.json())["tools"]]
        assert {"vega_sys", "vega_assign_role", "vega_resolve"} <= set(names)


@pytest.mark.asyncio
async def test_mcp_legacy_token_works_with_role_manager_present(tmp_path):
    """Back-compat: a single-operator deployment's MCP_AUTH_TOKEN still resolves
    to OP even when a RoleManager is wired but no roles are assigned yet."""
    aiohttp_client = pytest.importorskip("aiohttp.test_utils")
    from aiohttp import web
    from mcp_server import MCPServer

    rm = _make_role_manager(tmp_path)   # no roles assigned
    cfg = types.SimpleNamespace(MCP_AUTH_TOKEN="legacy-tok")
    srv = MCPServer(config=cfg, tools=None, role_manager=rm)
    app = web.Application()
    app.router.add_get("/mcp/tools", srv._list_tools)
    async with aiohttp_client.TestClient(aiohttp_client.TestServer(app)) as client:
        r = await client.get("/mcp/tools",
                             headers={"Authorization": "Bearer legacy-tok"})
        assert r.status == 200
        names = [t["name"] for t in (await r.json())["tools"]]
        assert "vega_approve" in names   # OP toolset
        # A non-matching token is still rejected.
        r = await client.get("/mcp/tools",
                             headers={"Authorization": "Bearer nope"})
        assert r.status == 401


@pytest.mark.asyncio
async def test_mcp_pending_invite_sees_only_activate(tmp_path):
    aiohttp_client = pytest.importorskip("aiohttp.test_utils")
    from aiohttp import web
    from mcp_server import MCPServer

    rm = _make_role_manager(tmp_path)
    invite_code = rm.create_invite("DE", "de-chat", "Hassan")  # not yet activated

    srv = MCPServer(config=types.SimpleNamespace(), tools=None, role_manager=rm)
    app = web.Application()
    app.router.add_get("/mcp/tools", srv._list_tools)
    async with aiohttp_client.TestClient(aiohttp_client.TestServer(app)) as client:
        # 9 — an invite code authenticates as _PENDING: only vega_activate_role
        r = await client.get("/mcp/tools",
                             headers={"Authorization": f"Bearer {invite_code}"})
        assert r.status == 200
        names = [t["name"] for t in (await r.json())["tools"]]
        assert names == ["vega_activate_role"]


# ─── CycleManager activity/pending/history + framework v6 chain ──────────────

def test_cycle_activity_and_pending(tmp_path):
    archive = tmp_path / "artifacts" / "archive"
    cycles = CycleManager(tmp_path / "cycles", archive)
    de_out = Artifact(type="DE_OUT", sender="SG", content="q", id="DE_OUT-SG-001")
    cycle = cycles.open_cycle("de_qa", de_out, ["SG", "DE"], primary_agent="SG")
    cycles.append_assistant_turn(cycle, "SG asks the DE a question")  # awaiting DE
    summaries = cycles.get_activity_summary("de_qa")
    assert any(s["id"] == cycle.id and s["status"] == "active" for s in summaries)
    pending = cycles.get_pending_for_role("DE")
    assert any(c.id == cycle.id for c in pending)


def test_scope_scoping_fits_small_window_and_keeps_full_for_large(tmp_path):
    """Per-agent scope scoping: a 200k-window agent gets a manifest + budget-bounded
    docs (relevant first); a 1M-window agent gets the whole corpus."""
    import types as _t
    from executor import AgentExecutor
    scope_dir = tmp_path / "scope"; scope_dir.mkdir()
    (scope_dir / "big_audit.md").write_text("# Audit archive\n" + "x" * 1_400_000)
    (scope_dir / "primary_scope.md").write_text("# Primary scope\n" + "p" * 40_000)
    (scope_dir / "open_items.md").write_text("# Open items\n" + "o" * 20_000)

    ex = object.__new__(AgentExecutor)
    ex.scope_dir = scope_dir
    ex.config = _t.SimpleNamespace(SCOPE_BUDGET_FRACTION=0.5)

    # SE on a 200k window (haiku) — full corpus (~1.46M chars) can't fit.
    ex.models = _t.SimpleNamespace(get=lambda c: "claude-haiku-4-5")
    items = [Artifact(type="SCN", sender="SG", id="SCN-SG-1",
                      content="apply change to primary_scope.md")]
    se_scope = ex._load_scope("SE", items)
    assert "SCOPE MANIFEST" in se_scope
    assert "[loaded] primary_scope.md" in se_scope        # relevant → loaded
    assert "[omitted] big_audit.md" in se_scope            # huge → omitted
    assert "x" * 1000 not in se_scope                      # the 1.4MB audit body is NOT inlined
    assert len(se_scope) < 400_000                         # bounded

    # SG on a 1M window (opus) — whole corpus loads, no manifest gating.
    ex.models = _t.SimpleNamespace(get=lambda c: "claude-opus-4-7")
    sg_scope = ex._load_scope("SG", items)
    assert "SCOPE MANIFEST" not in sg_scope
    assert "x" * 1000 in sg_scope                          # full audit body present


def test_executor_resolves_referenced_archive_artifacts(tmp_path):
    """An inbox item's referenced archived artifacts are pulled into context
    (the archive-access gap fix) — scoped to the referenced ids, not the whole
    archive, and self/inbox references are skipped."""
    from executor import AgentExecutor
    router, store, _, _, _, _ = _make_router(tmp_path)
    # Seed the archive with a PROP that an AUTH will reference.
    prop = Artifact(type="PROP", sender="SG", content="the proposal body to act on",
                    id="PROP-SG-042")
    store.archive_artifact(prop)
    auth = Artifact(type="AUTH", sender="OP", content="approve",
                    references=["PROP-SG-042", "PROP-SG-042", "Open Chantiers §C1"],
                    id="AUTH-OP-042")

    ex = object.__new__(AgentExecutor)
    ex.store = store
    out = ex._format_referenced_artifacts([auth])
    assert "REFERENCED: PROP-SG-042" in out
    assert "the proposal body to act on" in out
    assert out.count("REFERENCED: PROP-SG-042") == 1   # deduped
    assert "Open Chantiers" not in out                 # non-archived ref skipped silently
    # An item referencing only itself / inbox-present ids yields nothing.
    assert ex._format_referenced_artifacts([prop]) == ""


@pytest.mark.asyncio
async def test_vega_about_gives_role_glossary_to_every_role(tmp_path):
    """vega_about returns the authoritative agent/human role glossary + project,
    and is visible to every authenticated role (so clients don't confabulate)."""
    from mcp_server import ROLE_TOOLS, AGENT_ROLES
    tools, _ = _make_mcptools(tmp_path)
    about = await tools.vega_about(role_info={"role": "DE"})
    assert about["your_role"] == "DE"
    assert "Scope Editor" in about["agent_roles"]["SE"]       # not "scale/source"
    assert set(("OP", "ADMIN_OP", "DE", "EXT")) <= set(about["human_roles"])
    # Available to all four authenticated roles.
    for role in ("OP", "ADMIN_OP", "DE", "EXT"):
        assert "vega_about" in ROLE_TOOLS[role]
    # ...but not the lobby.
    assert "vega_about" not in ROLE_TOOLS[None]


def test_framework_chain_prefers_v6(tmp_path):
    from executor import _first_existing, FRAMEWORK_FILENAMES
    fw = tmp_path / "framework"; fw.mkdir()
    (fw / "VEGA_Architecture_Framework_v4.md").write_text("v4")
    (fw / "VEGA_Architecture_Framework_v6.md").write_text("v6")
    chosen = _first_existing(fw, FRAMEWORK_FILENAMES)
    assert chosen.name == "VEGA_Architecture_Framework_v6.md"


def test_initialize_project_creates_role_dirs(tmp_path):
    import sys
    sys.modules.setdefault("config", types.ModuleType("config"))
    import main as main_mod

    cfg = types.SimpleNamespace(
        BASE_DIR=tmp_path,
        AGENTS_DIR=str(tmp_path / "agents"),
        UNIVERSAL_DIR=str(tmp_path / "universal"),
        SCOPE_DIR=str(tmp_path / "scope"),
        ARTIFACTS_DIR=str(tmp_path / "artifacts" / "archive"),
        CYCLES_DIR=str(tmp_path / "cycles" / "active"),
        OP_BACKLOG_DIR=str(tmp_path / "op_backlog"),
        STATE_DIR=str(tmp_path / "state"),
        TEST_MODELS_FULL_DIR=str(tmp_path / "test_models" / "full"),
        TEST_MODELS_BUILD_DIR=str(tmp_path / "test_models" / "build"),
        AGENTS=["SG", "SYS"],
    )
    # Drive only the directory-creation portion (step 1) of initialize_project.
    main_mod.config = cfg
    # Re-run the directory scaffolding via the public entry (skips prompt gen by
    # pointing FRAMEWORK at a missing dir — generation warns and continues).
    cfg.FRAMEWORK_DIR = str(tmp_path / "framework")
    try:
        main_mod.initialize_project()
    except Exception:
        pass  # later steps (system prompts) may warn without a framework; dirs are made first
    assert (tmp_path / "admin_backlog" / "pending").is_dir()
    assert (tmp_path / "config" / "invites").is_dir()
    assert (tmp_path / "state" / "role_events.jsonl").exists()
