"""
MEDIUM tier — June 2026 spec-sync items #6/#7/#8/#9/#10 + NEW-2/NEW-5/NEW-6.

  #6        vega_scope read tool (all 4 roles)
  #7 NEW-6  thinking sidecar ({id}.thinking.md) + vega_thinking sidecar-first
  #8 NEW-2  vega_cycles detail mode + CycleManager.list_active/get_messages
  #9        /run + vega_run (replaces /retry)
  #10       TELEGRAM_EXCHANGE_ENABLED gate
  NEW-5     SYS framework view (executor uses _load_framework_view("SYS"))
"""

from __future__ import annotations

from pathlib import Path

import pytest

from artifact_store import ArtifactStore
from cycle_manager import CycleManager
from models import Artifact


# ─── helpers ─────────────────────────────────────────────────────────────────

class _Cfg:
    def __init__(self, tmp_path: Path, exchange=False):
        self.SCOPE_DIR = str(tmp_path / "scope")
        self.ARTIFACTS_DIR = str(tmp_path / "artifacts" / "archive")
        self.AGENTS_DIR = str(tmp_path / "agents")
        self.AGENTS = ["SG", "SE", "SYS"]
        self.TELEGRAM_EXCHANGE_ENABLED = exchange
        self.PROJECT_NAME = "TEST"


def _make_tools(tmp_path: Path, agent_retry=None):
    from mcp_server import MCPTools
    cfg = _Cfg(tmp_path)
    agents_dir = tmp_path / "agents"
    archive_dir = tmp_path / "artifacts" / "archive"
    cycles_dir = tmp_path / "cycles" / "active"
    state_dir = tmp_path / "state"
    for d in (agents_dir, archive_dir, cycles_dir, state_dir):
        d.mkdir(parents=True, exist_ok=True)
    store = ArtifactStore(agents_dir, archive_dir)
    cycles = CycleManager(cycles_dir, tmp_path / "artifacts" / "archive")

    async def _noop(*a, **k):
        return None

    calls: list[str] = []

    async def _retry(code):
        calls.append(code)

    tools = MCPTools(
        config=cfg, op_backlog=None, store=store, cycles=cycles, wiki=None,
        instances=None, models_mgr=None, sequences=None, sys_trigger=_noop,
        agent_retry=agent_retry or _retry, agent_pause=lambda c: None,
        agent_resume=lambda c: None, process_disposition=_noop,
        state_dir=state_dir)
    return tools, store, cycles, cfg, calls


# ─── #6 vega_scope ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_vega_scope_lists_docs(tmp_path):
    tools, _, _, cfg, _ = _make_tools(tmp_path)
    scope = Path(cfg.SCOPE_DIR); scope.mkdir(parents=True)
    (scope / "Scope_v7.2.md").write_text("# scope\nbody\n")
    (scope / "Manifest.md").write_text("# manifest\n")
    listing = await tools.vega_scope()
    names = {d["name"]: d for d in listing}
    assert "Scope_v7.2.md" in names and "Manifest.md" in names
    assert names["Scope_v7.2.md"]["version"] == "v7.2"
    assert names["Manifest.md"]["version"] == "—"
    assert names["Scope_v7.2.md"]["size"] > 0


@pytest.mark.asyncio
async def test_vega_scope_single_doc(tmp_path):
    tools, _, _, cfg, _ = _make_tools(tmp_path)
    scope = Path(cfg.SCOPE_DIR); scope.mkdir(parents=True)
    (scope / "Scope_v7.2.md").write_text("# scope\nthe body\n")
    # by full name, by stem
    for q in ("Scope_v7.2.md", "Scope_v7.2"):
        r = await tools.vega_scope(q)
        assert r["found"] and "the body" in r["content"]
    miss = await tools.vega_scope("nope")
    assert miss["found"] is False


@pytest.mark.asyncio
async def test_vega_scope_rejects_traversal(tmp_path):
    tools, _, _, cfg, _ = _make_tools(tmp_path)
    scope = Path(cfg.SCOPE_DIR); scope.mkdir(parents=True)
    (scope / "Scope_v1.md").write_text("ok")
    for bad in ("../../etc/passwd", "/etc/passwd", "~/secret"):
        r = await tools.vega_scope(bad)
        assert r["found"] is False and r.get("content") is None


@pytest.mark.asyncio
async def test_vega_scope_no_dir(tmp_path):
    tools, _, _, _, _ = _make_tools(tmp_path)   # SCOPE_DIR not created
    assert await tools.vega_scope() == []


def test_vega_scope_available_to_all_roles():
    from mcp_server import ROLE_TOOLS
    for role in ("OP", "ADMIN_OP", "DE", "EXT"):
        assert "vega_scope" in ROLE_TOOLS[role], role


# ─── #7 / NEW-6 thinking sidecar ─────────────────────────────────────────────

def test_archive_writes_thinking_sidecar(tmp_path):
    store = ArtifactStore(tmp_path / "agents", tmp_path / "archive")
    a = Artifact(type="SCN", sender="SG", content="body", id="SCN-SG-001")
    store.archive_artifact(a, thinking_blocks=["first thought", "second thought"])
    sidecar = tmp_path / "archive" / "SCN-SG-001.thinking.md"
    assert sidecar.exists()
    text = sidecar.read_text()
    assert "first thought" in text and "second thought" in text
    assert store.read_thinking("SCN-SG-001") == text


def test_thinking_sidecar_immutable_first_write_wins(tmp_path):
    store = ArtifactStore(tmp_path / "agents", tmp_path / "archive")
    store.write_thinking("X-1", ["original"])
    store.write_thinking("X-1", ["overwrite attempt"])
    assert "original" in store.read_thinking("X-1")
    assert "overwrite" not in store.read_thinking("X-1")


def test_write_thinking_empty_is_noop(tmp_path):
    store = ArtifactStore(tmp_path / "agents", tmp_path / "archive")
    assert store.write_thinking("X-2", []) is None
    assert store.read_thinking("X-2") is None


@pytest.mark.asyncio
async def test_vega_thinking_prefers_sidecar(tmp_path):
    tools, store, _, _, _ = _make_tools(tmp_path)
    store.write_thinking("SCN-SG-009", ["sidecar reasoning here"])
    out = await tools.vega_thinking("SCN-SG-009")
    assert "sidecar reasoning here" in out


@pytest.mark.asyncio
async def test_vega_thinking_falls_back_to_execution_log(tmp_path):
    tools, _, _, _, _ = _make_tools(tmp_path)
    from state_manager import atomic_save_json
    sd = tools.state_dir
    atomic_save_json(sd / "artifact_index.json", {"A-1": {"execution_id": "E-1"}})
    atomic_save_json(sd / "execution_log.json",
                     [{"execution_id": "E-1", "thinking_blocks": ["from log"]}])
    out = await tools.vega_thinking("A-1")
    assert "from log" in out


# ─── #8 / NEW-2 vega_cycles detail + list_active ─────────────────────────────

def test_cycle_manager_list_active(tmp_path):
    cm = CycleManager(tmp_path / "cycles", tmp_path / "archive")
    cm.open_cycle("prop_exchange",
                  Artifact(type="PROP", sender="SG", content="x", id="PROP-SG-001"),
                  ["SG", "OP"], primary_agent="SG")
    assert cm.list_active() == ["prop_exchange-PROP-SG-001"]


def test_cycle_manager_get_messages(tmp_path):
    cm = CycleManager(tmp_path / "cycles", tmp_path / "archive")
    prop = Artifact(type="PROP", sender="SG", content="x", id="PROP-SG-001")
    cycle = cm.open_cycle("prop_exchange", prop, ["SG", "OP"], primary_agent="SG")
    cm.append_turn(cycle, "a question", "OP")
    msgs = cm.get_messages("PROP-SG-001")
    assert msgs and "a question" in msgs[-1]["content"]
    # no such cycle
    assert cm.get_messages("PROP-SG-999") is None


@pytest.mark.asyncio
async def test_vega_cycles_list_and_detail(tmp_path):
    tools, _, cycles, _, _ = _make_tools(tmp_path)
    prop = Artifact(type="PROP", sender="SG", content="x", id="PROP-SG-001")
    cycle = cycles.open_cycle("prop_exchange", prop, ["SG", "OP"], primary_agent="SG")
    cycles.append_turn(cycle, "SG reply text", "SG")
    # list mode
    assert await tools.vega_cycles() == ["prop_exchange-PROP-SG-001"]
    # detail mode
    detail = await tools.vega_cycles("PROP-SG-001")
    assert detail["found"] is True
    assert any("SG reply text" in m["content"] for m in detail["messages"])
    # unknown id
    miss = await tools.vega_cycles("PROP-SG-404")
    assert miss["found"] is False and miss["messages"] == []


# ─── #9 /run + vega_run ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_vega_run_calls_retry(tmp_path):
    ran: list[str] = []

    async def _retry(code):
        ran.append(code)

    tools, _, _, _, _ = _make_tools(tmp_path, agent_retry=_retry)
    msg = await tools.vega_run("sg")
    assert ran == ["SG"]
    assert "SG" in msg


def test_vega_run_admin_only_and_registered():
    from mcp_server import ROLE_TOOLS, TOOL_REGISTRY
    assert "vega_run" in TOOL_REGISTRY
    assert "vega_run" in ROLE_TOOLS["ADMIN_OP"]
    assert "vega_run" not in ROLE_TOOLS["OP"]


def test_telegram_run_handler_registered():
    src = (Path(__file__).resolve().parent.parent / "telegram_bot.py").read_text()
    assert 'CommandHandler("run", self._cmd_run)' in src
    assert "async def _cmd_run" in src


# ─── #10 TELEGRAM_EXCHANGE_ENABLED gate ──────────────────────────────────────

def test_exchange_gate_present_in_on_text():
    src = (Path(__file__).resolve().parent.parent / "telegram_bot.py").read_text()
    assert 'getattr(self.config, "TELEGRAM_EXCHANGE_ENABLED"' in src
    # the gate must sit AFTER the APPROVE handling but BEFORE the forward calls
    gate = src.index('TELEGRAM_EXCHANGE_ENABLED", False)')
    fwd = src.index("partner=\"SYS\", role=\"ADMIN_OP\"")
    assert gate < fwd


def test_config_template_has_exchange_flag():
    tpl = (Path(__file__).resolve().parents[2] / "templates" / "config.py.tpl").read_text()
    assert "TELEGRAM_EXCHANGE_ENABLED = False" in tpl


# ─── NEW-5 SYS framework view ────────────────────────────────────────────────

def test_sys_uses_tiered_framework_view():
    src = (Path(__file__).resolve().parent.parent / "executor.py").read_text()
    # execute_sys must use the SYS-tier view, not the full framework dump
    assert 'self._load_framework_view("SYS")' in src
