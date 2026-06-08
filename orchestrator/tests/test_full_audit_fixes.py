"""
Full-audit fixes (June 2026) — Francisco's seven-phase audit.

MEDIUM:
  MED-1  main loop handles asyncio.gather exception results (log + notify Admin OP)
  MED-2  restore_scope is atomic (staging + rename) — covered in test_sc7_snapshots
  MED-3  initiate_restore clears stale OP-side phase-2 entry
LOW:
  LOW-2  tick-failure notification targets Admin OP
  LOW-4  dead thinking_blocks param removed from Archive.write / archive_artifact
  LOW-5  UNIVERSAL boundary GOV carries a heuristic-humility note
(LOW-1 read_log and LOW-3 framework_parser v6 were already done in the LOW tier.)
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from role_manager import RoleManager

MAIN = Path(__file__).resolve().parent.parent / "main.py"
EXECUTOR = Path(__file__).resolve().parent.parent / "executor.py"
ARTIFACT_STORE = Path(__file__).resolve().parent.parent / "artifact_store.py"


def _rm_with_op(tmp_path) -> RoleManager:
    rm = RoleManager(tmp_path / "config" / "roles.json",
                     tmp_path / "config" / "invites",
                     tmp_path / "state" / "role_events.jsonl",
                     tmp_path / "config" / "recovery.hash")
    rm._save_roles({"OP": {"telegram_id": "op-chat", "token_hash": "x"},
                    "ADMIN_OP": {"telegram_id": "admin-chat", "token_hash": "y"}})
    return rm


# ─── MED-1 gather exception handling ─────────────────────────────────────────

def test_gather_exception_branch_present():
    src = MAIN.read_text()
    # per-task agent attribution + an explicit BaseException branch that notifies
    assert "task_agents" in src
    assert "isinstance(r, BaseException)" in src
    assert 'role="ADMIN_OP"' in src  # exception notification targets Admin OP


# ─── MED-3 stale OP phase-2 cleanup ──────────────────────────────────────────

def test_reinitiate_restore_clears_stale_op_consent(tmp_path):
    rm = _rm_with_op(tmp_path)
    # Restore A initiated + admin-confirmed → OP consent pending for A
    rm.initiate_restore("admin-chat", "SNAP-A")
    op_id, _ = rm.confirm_restore_admin("admin-chat")
    assert rm.has_pending_restore(op_id)              # phase-2 for SNAP-A live
    # Admin OP re-initiates with a DIFFERENT snapshot before OP consented
    rm.initiate_restore("admin-chat", "SNAP-B")
    # The stale SNAP-A phase-2 entry must be gone — OP can't consent to A now
    assert rm.confirm_restore_op(op_id) is None
    assert not rm.has_pending_restore(op_id)


def test_reinitiate_same_admin_overwrites_phase1(tmp_path):
    rm = _rm_with_op(tmp_path)
    rm.initiate_restore("admin-chat", "SNAP-A")
    rm.initiate_restore("admin-chat", "SNAP-B")
    # Admin confirm now yields a consent flow for SNAP-B
    op_id, _ = rm.confirm_restore_admin("admin-chat")
    assert rm.confirm_restore_op(op_id) == "SNAP-B"


# ─── LOW-2 tick-failure → Admin OP ───────────────────────────────────────────

def test_tick_failure_targets_admin_op():
    src = MAIN.read_text()
    # locate the tick-failed send and confirm it carries role="ADMIN_OP"
    idx = src.index("Tick failed")
    window = src[idx:idx + 300]
    assert 'role="ADMIN_OP"' in window


# ─── LOW-4 dead thinking_blocks param removed ────────────────────────────────

def test_archive_write_has_no_thinking_blocks_param():
    from artifact_store import Archive, ArtifactStore
    assert "thinking_blocks" not in inspect.signature(Archive.write).parameters
    assert "thinking_blocks" not in inspect.signature(
        ArtifactStore.archive_artifact).parameters
    # the real sidecar path still exists
    assert hasattr(ArtifactStore, "write_thinking")
    assert hasattr(ArtifactStore, "read_thinking")


# ─── LOW-5 GOV heuristic-humility note ───────────────────────────────────────

def test_universal_gov_has_heuristic_note():
    src = EXECUTOR.read_text()
    # the blocked-UNIVERSAL GOV message tells Admin OP not to rely on the
    # validator alone (it's a regex heuristic).
    assert "Heuristic check" in src
    assert "do not rely on this validator alone" in src
