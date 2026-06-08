"""
Role manager — role assignment, onboarding, 2FA, token management.

Spec v5 §10.4 + Framework v6 §13 (Role-Based Access System). Five roles share
one orchestrator via role-scoped interfaces:

  OP        scope decisions (PROP → AUTH)
  ADMIN_OP  governance (GOV), role management, agent control
  DE        domain expertise (DE_IN to SG)
  EXT       external build interaction (BRQ to BR)
  (+ the two pseudo-roles: None = lobby, "_PENDING" = invite holder)

Tokens are stored only as SHA-256 hashes (config/roles.json); the plaintext token
is shown exactly once at activation. Role-changing actions (assign/modify/revoke)
require 2FA via a 6-digit OTP. Onboarding is invite → activate (D-ARCH-040).
Break-glass recovery (§13.6) resets Admin OP from a hashed recovery key.
All actions append to state/role_events.jsonl (read by SYS, §5.9).
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from state_manager import atomic_append, atomic_write, load_json


def _utcnow_z() -> str:
    """ISO-8601 with Z suffix — the single timestamp format for role_events."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class RoleManager:

    def __init__(self, roles_file, invites_dir, events_file, recovery_file,
                 invite_expiry: int = 24 * 60 * 60, otp_expiry: int = 5 * 60,
                 notify: Optional[Callable[[str, str], Awaitable[Any]]] = None) -> None:
        self.roles_file = Path(roles_file)            # config/roles.json
        self.invites_dir = Path(invites_dir)          # config/invites/
        self.events_file = Path(events_file)          # state/role_events.jsonl
        self.recovery_file = Path(recovery_file)      # config/recovery.hash
        self.invite_expiry = invite_expiry
        self.otp_expiry = otp_expiry
        # Async callback (chat_id, text) → awaitable — used to send OTPs/invites
        # to a person's Telegram. Optional so the manager is testable headless.
        self._notify = notify
        # Spec §10.3 — three separate pending stores (no type-mixing one dict):
        self._pending_role_actions: dict[str, dict] = {}  # chat_id → assign/modify/revoke
        self._pending_activations: dict[str, dict] = {}   # invite_code → activation
        self._pending_restores: dict[str, dict] = {}      # chat_id → restore (SC-7)
        self.invites_dir.mkdir(parents=True, exist_ok=True)
        self.roles_file.parent.mkdir(parents=True, exist_ok=True)
        self.events_file.parent.mkdir(parents=True, exist_ok=True)

    # ─── Token management ────────────────────────────────────────────────────

    def _hash_token(self, token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def verify_token(self, token: str) -> Optional[dict]:
        """Verify an MCP bearer token; return {role, ...role_data} or None."""
        token_hash = self._hash_token(token)
        for role_name, role_data in self._load_roles().items():
            if role_data.get("token_hash") == token_hash:
                return {"role": role_name, **role_data}
        return None

    def get_role_by_telegram(self, chat_id) -> Optional[str]:
        for role_name, role_data in self._load_roles().items():
            if str(role_data.get("telegram_id")) == str(chat_id):
                return role_name
        return None

    def get_telegram_id(self, role: str) -> Optional[str]:
        return self._load_roles().get(role, {}).get("telegram_id")

    # ─── Role assignment (2FA required) ──────────────────────────────────────

    def initiate_assign(self, chat_id, name, telegram_id, role, project) -> str:
        otp = self._generate_otp()
        self._pending_role_actions[str(chat_id)] = {
            "action": "assign",
            "params": {"name": name, "telegram_id": telegram_id,
                       "role": role, "project": project},
            "actor_telegram_id": str(chat_id),
            "otp": otp,
            "expires": time.time() + self.otp_expiry,
        }
        return otp

    def initiate_modify(self, chat_id, role, changes) -> str:
        otp = self._generate_otp()
        self._pending_role_actions[str(chat_id)] = {
            "action": "modify",
            "params": {"role": role, "changes": changes},
            "actor_telegram_id": str(chat_id),
            "otp": otp,
            "expires": time.time() + self.otp_expiry,
        }
        return otp

    def initiate_revoke(self, chat_id, role, name) -> str:
        otp = self._generate_otp()
        self._pending_role_actions[str(chat_id)] = {
            "action": "revoke",
            "params": {"role": role, "name": name},
            "actor_telegram_id": str(chat_id),
            "otp": otp,
            "expires": time.time() + self.otp_expiry,
        }
        return otp

    def has_pending_action(self, chat_id) -> bool:
        pending = self._pending_role_actions.get(str(chat_id))
        return pending is not None and time.time() <= pending["expires"]

    async def confirm_pending(self, chat_id, otp: Optional[str] = None) -> bool:
        """Confirm a pending role action. OTP required when confirming via MCP;
        Telegram 'APPROVE' from the actor's own chat is sufficient there."""
        key = str(chat_id)
        pending = self._pending_role_actions.get(key)
        if not pending or time.time() > pending["expires"]:
            self._log_event("2fa_expired", actor_telegram_id=key, result="failed")
            return False
        if otp is not None and otp != pending["otp"]:
            self._log_event("2fa_failed", actor_telegram_id=key, result="failed")
            return False

        action = pending["action"]
        params = pending["params"]
        if action == "assign":
            await self._execute_assign(params, actor_telegram_id=key)
        elif action == "revoke":
            self._execute_revoke(params, actor_telegram_id=key)
        elif action == "modify":
            self._execute_modify(params, actor_telegram_id=key)
        del self._pending_role_actions[key]
        return True

    # ─── Restore 2FA (SC-7, dual consent §7.5) — uses _pending_restores ───────

    def has_pending_restore(self, chat_id) -> bool:
        pending = self._pending_restores.get(str(chat_id))
        return pending is not None and time.time() <= pending["expires"]

    def initiate_restore(self, admin_chat_id, snapshot_id) -> str:
        """Phase 1: Admin OP initiates restoration → OTP to Admin OP."""
        # Clear any stale OP-side phase-2 entry from a previous attempt (audit
        # MED-3): if Admin OP re-initiates with a different snapshot before the
        # OP consented to the first, a left-over phase-2 entry would let the OP
        # consent to the WRONG snapshot.
        op_telegram_id = self.get_telegram_id("OP")
        if op_telegram_id and str(op_telegram_id) in self._pending_restores:
            del self._pending_restores[str(op_telegram_id)]
        otp = self._generate_otp()
        self._pending_restores[str(admin_chat_id)] = {
            "snapshot_id": snapshot_id, "otp": otp,
            "expires": time.time() + self.otp_expiry, "phase": "admin_confirm",
        }
        return otp

    def confirm_restore_admin(self, admin_chat_id, otp=None) -> Optional[tuple]:
        """Phase 1 confirmed → prepare Phase 2 (OP consent). Returns
        (op_telegram_id, op_otp) or None. OTP required via MCP; Telegram
        'APPROVE' from the actor's own chat is sufficient there (otp=None)."""
        pending = self._pending_restores.get(str(admin_chat_id))
        if (not pending or pending.get("phase") != "admin_confirm"
                or time.time() > pending["expires"]):
            return None
        if otp is not None and pending["otp"] != otp:
            self._log_event("2fa_failed", actor_telegram_id=str(admin_chat_id),
                            result="failed")
            return None
        snapshot_id = pending["snapshot_id"]
        op_telegram_id = self.get_telegram_id("OP")
        op_otp = self._generate_otp()
        del self._pending_restores[str(admin_chat_id)]
        if not op_telegram_id:
            return None   # no OP to consent — can't proceed via dual 2FA
        self._pending_restores[str(op_telegram_id)] = {
            "snapshot_id": snapshot_id, "otp": op_otp,
            "expires": time.time() + self.otp_expiry, "phase": "op_consent",
        }
        self._log_event("restore_initiated", snapshot_id=snapshot_id, result="pending")
        return op_telegram_id, op_otp

    def confirm_restore_op(self, op_chat_id, otp=None) -> Optional[str]:
        """Phase 2 confirmed → return snapshot_id for execution, else None.
        OTP required via MCP; Telegram 'APPROVE' from the OP's own chat is
        sufficient there (otp=None)."""
        pending = self._pending_restores.get(str(op_chat_id))
        if (not pending or pending.get("phase") != "op_consent"
                or time.time() > pending["expires"]):
            return None
        if otp is not None and pending["otp"] != otp:
            self._log_event("2fa_failed", actor_telegram_id=str(op_chat_id),
                            result="failed")
            return None
        snapshot_id = pending["snapshot_id"]
        del self._pending_restores[str(op_chat_id)]
        return snapshot_id

    def cancel_restore(self, chat_id) -> None:
        self._pending_restores.pop(str(chat_id), None)

    # ─── Invite / Activation (D-ARCH-040) ────────────────────────────────────

    def create_invite(self, role, telegram_id, name) -> str:
        code = f"VEGA-{role}-{secrets.token_hex(3).upper()}"
        invite = {
            "code": code, "role": role, "telegram_id": telegram_id,
            "name": name, "created": time.time(),
            "expires": time.time() + self.invite_expiry,
        }
        atomic_write(self.invites_dir / f"{code}.json", json.dumps(invite, indent=2))
        self._log_event("invite_created", target_role=role, target_name=name,
                        target_telegram_id=telegram_id, result="success")
        return code

    def activate_invite(self, invite_code) -> Optional[dict]:
        """Validate an invite code; return its data or None (expired → removed)."""
        path = self.invites_dir / f"{invite_code}.json"
        if not path.exists():
            return None
        invite = json.loads(path.read_text())
        if time.time() > invite["expires"]:
            try:
                path.unlink()
            except OSError:
                pass
            return None
        return invite

    def complete_activation(self, invite_code, otp_verified: bool = True) -> Optional[str]:
        """Mint a permanent token, store its hash, invalidate the invite.
        Returns the plaintext token (shown once) or None."""
        invite = self.activate_invite(invite_code)
        if not invite or not otp_verified:
            return None
        token = f"vega_{secrets.token_hex(32)}"
        roles = self._load_roles()
        roles[invite["role"]] = {
            "name": invite["name"],
            "telegram_id": invite["telegram_id"],
            "token_hash": self._hash_token(token),
            "assigned_at": time.time(),
        }
        self._save_roles(roles)
        try:
            (self.invites_dir / f"{invite_code}.json").unlink()
        except OSError:
            pass
        self._log_event("role_activated", target_role=invite["role"],
                        target_name=invite["name"],
                        target_telegram_id=invite["telegram_id"], result="success")
        return token

    # ─── Break-glass recovery (Framework v6 §13.6) ───────────────────────────

    def verify_recovery_key(self, key: str) -> bool:
        if not self.recovery_file.exists():
            return False
        stored_hash = self.recovery_file.read_text().strip()
        return self._hash_token(key) == stored_hash

    def execute_recovery(self, key, new_admin_telegram_id) -> Optional[str]:
        """Reset Admin OP credentials (server CLI). Returns a fresh invite code."""
        if not self.verify_recovery_key(key):
            self._log_event("recovery_executed", result="failed")
            return None
        roles = self._load_roles()
        roles.pop("ADMIN_OP", None)
        self._save_roles(roles)
        code = self.create_invite("ADMIN_OP", new_admin_telegram_id, "recovery")
        self._log_event("recovery_executed",
                        target_telegram_id=new_admin_telegram_id, result="success")
        return code

    @staticmethod
    def set_recovery_key(recovery_file, key: str) -> None:
        """Store the hash of a break-glass recovery key (first deployment)."""
        path = Path(recovery_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, hashlib.sha256(key.encode()).hexdigest())

    def roles_summary(self) -> dict:
        """Current assignments with token hashes stripped (for vega_roles/CLI)."""
        out = {}
        for role, data in self._load_roles().items():
            out[role] = {k: v for k, v in data.items() if k != "token_hash"}
        return out

    # ─── Internals ───────────────────────────────────────────────────────────

    def _generate_otp(self) -> str:
        return str(secrets.randbelow(900000) + 100000)  # 6-digit

    def _load_roles(self) -> dict:
        return load_json(self.roles_file, default={})

    def _save_roles(self, roles: dict) -> None:
        atomic_write(self.roles_file, json.dumps(roles, indent=2))

    async def _execute_assign(self, params, actor_telegram_id=None) -> str:
        """Create an invite and push it to the assignee's Telegram."""
        code = self.create_invite(params["role"], params["telegram_id"], params["name"])
        self._log_event("assign", actor_telegram_id=actor_telegram_id,
                        target_role=params["role"], target_name=params["name"],
                        target_telegram_id=params["telegram_id"],
                        project=params.get("project"), result="success")
        if self._notify:
            await self._notify(params["telegram_id"],
                f"You've been approved as {params['role']} for "
                f"{params.get('project', 'the project')}.\n"
                f"Your invite code: {code}\n"
                f"Configure MCP with this code and say 'activate my VEGA role.'\n"
                f"Expires in 24 hours.")
        return code

    def _execute_revoke(self, params, actor_telegram_id=None) -> None:
        roles = self._load_roles()
        if params["role"] in roles:
            del roles[params["role"]]
            self._save_roles(roles)
            self._log_event("revoke", actor_telegram_id=actor_telegram_id,
                            target_role=params["role"],
                            target_name=params.get("name"), result="success")

    def _execute_modify(self, params, actor_telegram_id=None) -> None:
        roles = self._load_roles()
        role = params["role"]
        if role not in roles:
            return
        changes = params["changes"]
        if "telegram_id" in changes:
            roles[role]["telegram_id"] = changes["telegram_id"]
        if "name" in changes:
            roles[role]["name"] = changes["name"]
        self._save_roles(roles)
        self._log_event("modify", actor_telegram_id=actor_telegram_id,
                        target_role=role, changes=changes, result="success")

    def _log_event(self, action, **kwargs) -> None:
        """Append one JSON object per line to role_events.jsonl (immutable trail).

        Schema (Spec §10.4): timestamp, action, actor_role, actor_telegram_id,
        target_role, target_name, target_telegram_id, project, result. SYS flags
        off-hours changes, rapid assign/revoke cycles, repeated 2fa_failed, and
        recovery_executed events."""
        event = {"timestamp": _utcnow_z(), "action": action}
        event.update({k: v for k, v in kwargs.items() if v is not None})
        atomic_append(self.events_file, json.dumps(event) + "\n")
