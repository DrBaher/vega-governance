"""
SC-7 — Validated-state snapshots (Spec v5 §7.5).

Covers the snapshot_manager module (write/list/verify/restore), the RoleManager
dual-2FA bookkeeping, and the contract that snapshots are written on AUTH approve
only (not reject/modify) and never block the routing flow.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from models import Artifact
from role_manager import RoleManager
import snapshot_manager as sm


# ─── helpers ─────────────────────────────────────────────────────────────────

class _Cfg:
    """Minimal config stub for snapshot_manager."""
    def __init__(self, tmp_path: Path, enabled=True, remote=""):
        self.SCOPE_DIR = str(tmp_path / "scope")
        self.ARTIFACTS_DIR = str(tmp_path / "artifacts" / "archive")
        self.SNAPSHOT_LOCAL_DIR = str(tmp_path / "vega-snapshots")
        self.SNAPSHOT_ENABLED = enabled
        self.SNAPSHOT_GIT_REMOTE = remote
        self.AGENTS = ["SG", "SA", "SE", "TG", "TA", "TE", "BR", "BTA", "SYS"]


def _seed_scope(cfg: _Cfg) -> Path:
    scope = Path(cfg.SCOPE_DIR)
    scope.mkdir(parents=True, exist_ok=True)
    (scope / "Scope_v7.2.md").write_text("# Scope\nbody v7.2\n")
    (scope / "manifest.md").write_text("# Manifest\n- Scope_v7.2.md\n")
    return scope


def _seed_archive(cfg: _Cfg, *artifact_ids: str) -> None:
    arch = Path(cfg.ARTIFACTS_DIR)
    arch.mkdir(parents=True, exist_ok=True)
    for aid in artifact_ids:
        (arch / f"{aid}.md").write_text(f"# {aid}\nartifact body\n")


def _auth(auth_id="AUTH-OP-001", prop_id="PROP-SG-007") -> Artifact:
    return Artifact(type="AUTH", sender="OP", content="approved",
                    references=[prop_id], recipient="SG", id=auth_id)


# ─── write_snapshot ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_write_snapshot_creates_manifest_and_scope(tmp_path):
    cfg = _Cfg(tmp_path)
    _seed_scope(cfg)
    _seed_archive(cfg, "AUTH-OP-001", "PROP-SG-007")
    snap_id = await sm.write_snapshot(_auth(), cfg)
    assert snap_id and snap_id.startswith("SNAP-")
    snap_dir = Path(cfg.SNAPSHOT_LOCAL_DIR) / snap_id
    # scope copied
    assert (snap_dir / "scope" / "Scope_v7.2.md").exists()
    # triggering AUTH + referenced PROP copied
    assert (snap_dir / "artifacts" / "AUTH-OP-001.md").exists()
    assert (snap_dir / "artifacts" / "PROP-SG-007.md").exists()
    # manifest with hashes + version map
    manifest = json.loads((snap_dir / "manifest.json").read_text())
    assert manifest["trigger"] == "AUTH-OP-001"
    assert manifest["approved_artifact"] == "PROP-SG-007"
    assert manifest["scope_versions"]["Scope_v7.2.md"] == "v7.2"
    assert "scope/Scope_v7.2.md" in manifest["content_hashes"]
    assert manifest["content_hashes"]["scope/Scope_v7.2.md"].startswith("sha256:")


@pytest.mark.asyncio
async def test_write_snapshot_disabled_returns_none(tmp_path):
    cfg = _Cfg(tmp_path, enabled=False)
    _seed_scope(cfg)
    assert await sm.write_snapshot(_auth(), cfg) is None
    assert not Path(cfg.SNAPSHOT_LOCAL_DIR).exists()


@pytest.mark.asyncio
async def test_write_snapshot_never_raises_and_logs_on_failure(tmp_path):
    """A snapshot failure must not block the approve flow (§10.2). Missing
    SCOPE_DIR makes copytree fail; write_snapshot swallows it, returns None,
    and logs snapshot_failed via the role_manager."""
    cfg = _Cfg(tmp_path)
    # deliberately do NOT create SCOPE_DIR
    rm = RoleManager(tmp_path / "config" / "roles.json",
                     tmp_path / "config" / "invites",
                     tmp_path / "state" / "role_events.jsonl",
                     tmp_path / "config" / "recovery.hash")
    out = await sm.write_snapshot(_auth(), cfg, role_manager=rm)
    assert out is None
    events = (tmp_path / "state" / "role_events.jsonl").read_text()
    assert "snapshot_failed" in events


# ─── list_snapshots ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_snapshots_newest_first(tmp_path):
    cfg = _Cfg(tmp_path)
    _seed_scope(cfg)
    _seed_archive(cfg, "AUTH-OP-001", "AUTH-OP-002", "PROP-SG-007")
    s1 = await sm.write_snapshot(_auth("AUTH-OP-001"), cfg)
    s2 = await sm.write_snapshot(_auth("AUTH-OP-002"), cfg)
    listed = [s["snapshot_id"] for s in sm.list_snapshots(cfg)]
    assert set(listed) == {s1, s2}
    # newest-first: AUTH-OP-002's id sorts after AUTH-OP-001's (same ts prefix
    # ordering is by reverse name) — both present, list is reverse-sorted.
    assert listed == sorted(listed, reverse=True)


def test_list_snapshots_empty_when_no_dir(tmp_path):
    cfg = _Cfg(tmp_path)
    assert sm.list_snapshots(cfg) == []


# ─── verify_scope ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_verify_match_then_drift(tmp_path):
    cfg = _Cfg(tmp_path)
    _seed_scope(cfg)
    _seed_archive(cfg, "AUTH-OP-001", "PROP-SG-007")
    snap_id = await sm.write_snapshot(_auth(), cfg)
    # unchanged → match
    res = sm.verify_scope(cfg, snap_id)
    assert res["match"] is True
    assert all(v == "match" for v in res["docs"].values())
    # mutate a scope doc → drift
    (Path(cfg.SCOPE_DIR) / "Scope_v7.2.md").write_text("# Scope\nTAMPERED\n")
    res2 = sm.verify_scope(cfg, snap_id)
    assert res2["match"] is False
    assert res2["docs"]["scope/Scope_v7.2.md"] == "drift"


@pytest.mark.asyncio
async def test_verify_detects_added_doc(tmp_path):
    cfg = _Cfg(tmp_path)
    _seed_scope(cfg)
    _seed_archive(cfg, "AUTH-OP-001", "PROP-SG-007")
    snap_id = await sm.write_snapshot(_auth(), cfg)
    (Path(cfg.SCOPE_DIR) / "New_Doc_v1.md").write_text("# New\n")
    res = sm.verify_scope(cfg, snap_id)
    assert res["match"] is False
    assert res["docs"]["scope/New_Doc_v1.md"] == "added-since-snapshot"


def test_verify_no_snapshot_returns_error(tmp_path):
    cfg = _Cfg(tmp_path)
    assert "error" in sm.verify_scope(cfg)


@pytest.mark.asyncio
async def test_verify_defaults_to_latest(tmp_path):
    cfg = _Cfg(tmp_path)
    _seed_scope(cfg)
    _seed_archive(cfg, "AUTH-OP-001", "PROP-SG-007")
    await sm.write_snapshot(_auth(), cfg)
    res = sm.verify_scope(cfg)        # no snapshot_id → latest
    assert res.get("match") is True


# ─── restore_scope ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_restore_replaces_scope_and_keeps_agents_paused(tmp_path):
    cfg = _Cfg(tmp_path)
    _seed_scope(cfg)
    _seed_archive(cfg, "AUTH-OP-001", "PROP-SG-007")
    snap_id = await sm.write_snapshot(_auth(), cfg)
    # drift the live scope after the snapshot
    (Path(cfg.SCOPE_DIR) / "Scope_v7.2.md").write_text("# Scope\npost-snapshot edit\n")
    (Path(cfg.SCOPE_DIR) / "Extra_v1.md").write_text("# Extra\n")

    rm = RoleManager(tmp_path / "config" / "roles.json",
                     tmp_path / "config" / "invites",
                     tmp_path / "state" / "role_events.jsonl",
                     tmp_path / "config" / "recovery.hash")

    paused: list[str] = []
    sys_calls: list[str] = []

    async def _execute_sys(audit_request=None):
        sys_calls.append(audit_request or "")

    def _pause_all():
        paused.extend(cfg.AGENTS)

    ok = await sm.restore_scope(snap_id, cfg, execute_sys=_execute_sys,
                                telegram_bot=None, role_manager=rm,
                                pause_all=_pause_all)
    assert ok is True
    # scope reverted to snapshot content; post-snapshot additions gone
    assert "body v7.2" in (Path(cfg.SCOPE_DIR) / "Scope_v7.2.md").read_text()
    assert not (Path(cfg.SCOPE_DIR) / "Extra_v1.md").exists()
    # pre-restore backup made
    backups = list(Path(cfg.SNAPSHOT_LOCAL_DIR).glob("pre-restore-*"))
    assert backups and (backups[0] / "scope" / "Extra_v1.md").exists()
    # all agents paused, SYS audit ran, agents NOT auto-resumed
    assert set(paused) == set(cfg.AGENTS)
    assert len(sys_calls) == 1 and "Post-restoration audit" in sys_calls[0]
    # restore logged
    assert "restore_executed" in (tmp_path / "state" / "role_events.jsonl").read_text()


@pytest.mark.asyncio
async def test_restore_missing_snapshot_returns_false(tmp_path):
    cfg = _Cfg(tmp_path)
    _seed_scope(cfg)
    rm = RoleManager(tmp_path / "config" / "roles.json",
                     tmp_path / "config" / "invites",
                     tmp_path / "state" / "role_events.jsonl",
                     tmp_path / "config" / "recovery.hash")

    async def _execute_sys(audit_request=None):
        raise AssertionError("SYS must not run when snapshot is missing")

    ok = await sm.restore_scope("SNAP-nonexistent", cfg, execute_sys=_execute_sys,
                                telegram_bot=None, role_manager=rm,
                                pause_all=lambda: None)
    assert ok is False


# ─── RoleManager dual-2FA bookkeeping ────────────────────────────────────────

def _rm_with_op(tmp_path) -> RoleManager:
    rm = RoleManager(tmp_path / "config" / "roles.json",
                     tmp_path / "config" / "invites",
                     tmp_path / "state" / "role_events.jsonl",
                     tmp_path / "config" / "recovery.hash")
    rm._save_roles({"OP": {"telegram_id": "op-chat", "token_hash": "x"},
                    "ADMIN_OP": {"telegram_id": "admin-chat", "token_hash": "y"}})
    return rm


def test_restore_2fa_happy_path(tmp_path):
    rm = _rm_with_op(tmp_path)
    otp = rm.initiate_restore("admin-chat", "SNAP-1")
    assert rm.has_pending_restore("admin-chat")
    # admin confirm (Telegram APPROVE → otp=None) → OP consent prepared
    result = rm.confirm_restore_admin("admin-chat")
    assert result is not None
    op_id, op_otp = result
    assert op_id == "op-chat" and op_otp
    # admin pending cleared, OP pending now active
    assert not rm.has_pending_restore("admin-chat")
    assert rm.has_pending_restore("op-chat")
    # OP consent → snapshot id for execution
    assert rm.confirm_restore_op("op-chat") == "SNAP-1"
    assert not rm.has_pending_restore("op-chat")


def test_restore_admin_otp_mismatch_via_mcp(tmp_path):
    rm = _rm_with_op(tmp_path)
    rm.initiate_restore("admin-chat", "SNAP-1")
    # wrong OTP (MCP path passes an otp) → rejected, pending preserved
    assert rm.confirm_restore_admin("admin-chat", otp="000000") is None
    assert rm.has_pending_restore("admin-chat")


def test_restore_no_op_on_record_aborts(tmp_path):
    rm = RoleManager(tmp_path / "config" / "roles.json",
                     tmp_path / "config" / "invites",
                     tmp_path / "state" / "role_events.jsonl",
                     tmp_path / "config" / "recovery.hash")
    rm._save_roles({"ADMIN_OP": {"telegram_id": "admin-chat", "token_hash": "y"}})
    rm.initiate_restore("admin-chat", "SNAP-1")
    # no OP to consent → confirm returns None
    assert rm.confirm_restore_admin("admin-chat") is None


def test_restore_op_consent_wrong_phase(tmp_path):
    rm = _rm_with_op(tmp_path)
    rm.initiate_restore("admin-chat", "SNAP-1")
    # OP cannot consent before admin confirms (phase is admin_confirm, keyed to admin)
    assert rm.confirm_restore_op("admin-chat") is None


def test_cancel_restore(tmp_path):
    rm = _rm_with_op(tmp_path)
    rm.initiate_restore("admin-chat", "SNAP-1")
    rm.cancel_restore("admin-chat")
    assert not rm.has_pending_restore("admin-chat")


# ─── contract: snapshot on approve only, wired into process_disposition ──────

def test_process_disposition_snapshots_on_approve_only():
    src = (Path(__file__).resolve().parent.parent / "telegram_bot.py").read_text()
    # write_snapshot guarded by disposition == "approve"
    assert 'if disposition == "approve":' in src
    assert "write_snapshot(auth" in src


def test_sc7_tools_registered():
    from mcp_server import ROLE_TOOLS, ROLE_AWARE_TOOLS, TOOL_REGISTRY
    for t in ("vega_snapshots", "vega_verify", "vega_restore"):
        assert t in TOOL_REGISTRY
    # OP can verify + list, but NOT restore
    assert "vega_verify" in ROLE_TOOLS["OP"]
    assert "vega_snapshots" in ROLE_TOOLS["OP"]
    assert "vega_restore" not in ROLE_TOOLS["OP"]
    # Admin OP can do all three
    for t in ("vega_snapshots", "vega_verify", "vega_restore"):
        assert t in ROLE_TOOLS["ADMIN_OP"]
    # role-aware tools receive role_info
    assert {"vega_verify", "vega_restore"} <= ROLE_AWARE_TOOLS
