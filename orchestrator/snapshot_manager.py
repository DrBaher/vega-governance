"""
Validated-state snapshots (Spec v5 §7.5, SC-7).

On each scope approval (AUTH approve) the orchestrator exports an immutable
snapshot — scope/ + the triggering SCN/AUTH/SUM + a SHA-256 manifest — to durable
storage OUTSIDE the orchestrator tree. Snapshots enable: (a) verification (live
scope hashes vs the last snapshot) and (b) restoration to a prior validated state
via dual 2FA (Admin OP technical authority + OP scope authority).

These are standalone functions (NOT methods on RoleManager) per the spec — the
RoleManager owns only the 2FA bookkeeping; file operations live here.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from state_manager import atomic_write


def _utcnow_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _compute_content_hashes(snap_dir: Path) -> dict[str, str]:
    """SHA-256 of each scope doc, keyed by its path relative to the snapshot."""
    out: dict[str, str] = {}
    scope = snap_dir / "scope"
    if scope.exists():
        for p in sorted(scope.rglob("*")):
            if p.is_file():
                out[str(p.relative_to(snap_dir))] = _sha256(p)
    return out


_VERSION_RE = __import__("re").compile(r"_v(\d+(?:[._]\d+)*)\.md$")


def _extract_scope_versions(scope_dir: Path) -> dict[str, str]:
    """Best-effort doc→version map from filename suffixes (e.g. ..._v7.2.md)."""
    out: dict[str, str] = {}
    for p in sorted(Path(scope_dir).glob("*.md")):
        m = _VERSION_RE.search(p.name)
        out[p.name] = ("v" + m.group(1).replace("_", ".")) if m else "—"
    return out


def _git_push_snapshot(snap_dir: Path, snap_id: str, remote: str) -> None:
    """Best-effort commit + push of the snapshot dir. Never raises."""
    try:
        repo = snap_dir.parent
        subprocess.run(["git", "-C", str(repo), "add", snap_id], check=True,
                       capture_output=True, timeout=30)
        subprocess.run(["git", "-C", str(repo), "commit", "-m", f"snapshot {snap_id}"],
                       check=True, capture_output=True, timeout=30)
        subprocess.run(["git", "-C", str(repo), "push", remote, "HEAD"],
                       check=True, capture_output=True, timeout=60)
    except Exception:
        pass  # snapshot durability is best-effort; local copy already written


async def write_snapshot(auth_artifact, config, role_manager=None,
                         telegram_bot=None) -> Optional[str]:
    """Write a validated-state snapshot for an approve AUTH (§7.5). Returns the
    snapshot id, or None on disabled/failure. NEVER raises — a snapshot failure
    must not block the approve/routing flow."""
    if not getattr(config, "SNAPSHOT_ENABLED", True):
        return None
    local_dir = getattr(config, "SNAPSHOT_LOCAL_DIR", None)
    if not local_dir:
        return None
    snap_id = f"SNAP-{_utcnow_compact()}-{auth_artifact.id}"
    snap_dir = Path(local_dir) / snap_id
    try:
        snap_dir.mkdir(parents=True, exist_ok=False)
        # scope/ — dereference symlinks so the snapshot is self-contained
        shutil.copytree(config.SCOPE_DIR, snap_dir / "scope", symlinks=False)
        # triggering artifacts (AUTH + its references: SCN, SUM, …) from the archive
        (snap_dir / "artifacts").mkdir()
        for ref_id in [auth_artifact.id] + list(auth_artifact.references or []):
            src = Path(config.ARTIFACTS_DIR) / f"{ref_id}.md"
            if src.exists():
                shutil.copy(src, snap_dir / "artifacts" / src.name)
        manifest = {
            "snapshot_id": snap_id,
            "timestamp": _utcnow_iso(),
            "trigger": auth_artifact.id,
            "approved_artifact": (list(auth_artifact.references or []) or [None])[0],
            "scope_versions": _extract_scope_versions(snap_dir / "scope"),
            "content_hashes": _compute_content_hashes(snap_dir),
        }
        atomic_write(snap_dir / "manifest.json", json.dumps(manifest, indent=2))
        remote = getattr(config, "SNAPSHOT_GIT_REMOTE", "") or ""
        if remote:
            _git_push_snapshot(snap_dir, snap_id, remote)
        return snap_id
    except Exception as e:  # never block routing (§10.2)
        if role_manager is not None:
            role_manager._log_event("snapshot_failed", trigger=auth_artifact.id,
                                    error=str(e), result="failed")
        if telegram_bot is not None:
            try:
                await telegram_bot.send(
                    f"⚠️ Snapshot failed for {auth_artifact.id}: {e}. "
                    f"AUTH issued normally; snapshot durability degraded.", role="OP")
            except Exception:
                pass
        return None


def list_snapshots(config) -> list[dict[str, Any]]:
    """List available snapshots (newest first) from their manifests."""
    local_dir = Path(getattr(config, "SNAPSHOT_LOCAL_DIR", "") or "")
    if not local_dir.exists():
        return []
    out: list[dict[str, Any]] = []
    for d in sorted(local_dir.glob("SNAP-*"), reverse=True):
        mf = d / "manifest.json"
        if mf.exists():
            try:
                m = json.loads(mf.read_text())
                out.append({"snapshot_id": m.get("snapshot_id", d.name),
                            "timestamp": m.get("timestamp"),
                            "trigger": m.get("trigger"),
                            "scope_versions": m.get("scope_versions", {})})
            except Exception:
                continue
    return out


def _latest_snapshot_dir(config) -> Optional[Path]:
    local_dir = Path(getattr(config, "SNAPSHOT_LOCAL_DIR", "") or "")
    if not local_dir.exists():
        return None
    snaps = sorted(local_dir.glob("SNAP-*"), reverse=True)
    return snaps[0] if snaps else None


def verify_scope(config, snapshot_id: str = "") -> dict[str, Any]:
    """Compare live scope file hashes against a snapshot's manifest (default:
    latest). Returns {snapshot_id, match (bool), docs: {name: 'match'|'drift'|...}}."""
    local_dir = Path(getattr(config, "SNAPSHOT_LOCAL_DIR", "") or "")
    snap_dir = (local_dir / snapshot_id) if snapshot_id else _latest_snapshot_dir(config)
    if not snap_dir or not (snap_dir / "manifest.json").exists():
        return {"error": "no snapshot to verify against"}
    manifest = json.loads((snap_dir / "manifest.json").read_text())
    hashes = manifest.get("content_hashes", {})
    scope_dir = Path(config.SCOPE_DIR)
    docs: dict[str, str] = {}
    all_match = True
    for rel, expected in hashes.items():
        live = scope_dir / Path(rel).relative_to("scope") if rel.startswith("scope/") else scope_dir / rel
        if not live.exists():
            docs[rel] = "missing"; all_match = False
        elif _sha256(live) == expected:
            docs[rel] = "match"
        else:
            docs[rel] = "drift"; all_match = False
    # live docs not in the snapshot are additions
    for p in sorted(scope_dir.glob("*.md")):
        rel = f"scope/{p.name}"
        if rel not in hashes:
            docs[rel] = "added-since-snapshot"; all_match = False
    return {"snapshot_id": manifest.get("snapshot_id"), "match": all_match, "docs": docs}


async def restore_scope(snapshot_id, config, execute_sys: Callable, telegram_bot,
                        role_manager, pause_all: Callable[[], None]) -> bool:
    """Restore scope/ to a snapshot after dual 2FA (§7.5). Pauses all agents,
    backs up current scope, replaces scope/ ONLY, logs, runs a post-restoration
    SYS audit, and notifies — agents stay paused for Admin OP to /resume.

    `execute_sys` is the orchestrator's SYS-audit coroutine; `pause_all` pauses
    every agent (both supplied by the caller so this stays standalone)."""
    local_dir = Path(config.SNAPSHOT_LOCAL_DIR)
    snap_dir = local_dir / snapshot_id
    if not (snap_dir / "scope").exists():
        return False
    # 1. pause all agents (no stale-knowledge work during/after restore)
    pause_all()
    # 2. back up current scope
    backup = local_dir / f"pre-restore-{int(time.time())}"
    backup.mkdir(parents=True, exist_ok=True)
    shutil.copytree(config.SCOPE_DIR, backup / "scope", symlinks=False)
    # 3. replace scope/ ONLY (wikis, archive, routing_log untouched)
    shutil.rmtree(config.SCOPE_DIR)
    shutil.copytree(snap_dir / "scope", config.SCOPE_DIR)
    # 4. log
    role_manager._log_event("restore_executed", snapshot_id=snapshot_id, result="success")
    # 5. SYS audit BEFORE resuming — surface wiki-vs-restored-scope inconsistencies
    await execute_sys(audit_request=(
        f"Post-restoration audit: scope restored to {snapshot_id}. Identify wiki "
        f"entries and decisions that reference post-snapshot scope changes now "
        f"inconsistent with the restored scope. Produce GOV for each inconsistency."))
    # 6. notify — agents remain paused until Admin OP reviews + /resume
    if telegram_bot is not None:
        try:
            await telegram_bot.send(
                f"✅ Scope restored to {snapshot_id}. SYS audit complete — review GOV "
                f"findings before resuming agents. /resume when ready.", role="ADMIN_OP")
            await telegram_bot.send(
                f"Scope restored to {snapshot_id}. /verify to confirm; re-evaluate "
                f"post-snapshot changes via normal PROP→AUTH.", role="OP")
        except Exception:
            pass
    return True
