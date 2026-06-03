"""
MCP Server — Spec v4 §12.1-12.2.

HTTP endpoint that exposes orchestrator tools to any MCP-speaking Claude
session. Runs in the same process as the orchestrator so it shares the
ArtifactStore, OPBacklog, Router, CycleManager, etc. — no IPC, no separate
process, no separate state. The Claude session connects over the network.

Authentication: bearer token (config.MCP_AUTH_TOKEN). The server rejects any
request without `Authorization: Bearer <token>` matching the configured value.
One token per deployment, matching the single-operator model (same as
Telegram's OP chat ID).

Mediation note (per spec §12.1): messages submitted via MCP may be paraphrased
by the mediating Claude session. The cycle archive records what was sent to
the agent, not what OP typed verbatim. For word-exact intent, OP can instruct
Claude "send exactly this: ..." or use Telegram (which preserves OP's literal
text).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from aiohttp import web

from artifact_store import ArtifactStore
from cycle_manager import CycleManager
from models import Artifact, PRIORITY_EMOJI
from backlog import OPBacklog
from sequence_manager import (
    InstanceManager, ModelAssignmentManager, SequenceManager,
)
from state_manager import flag_for_execution, load_json
from wiki_manager import WikiManager


# ─── Tool implementations ────────────────────────────────────────────────────

class MCPTools:
    """Concrete tool implementations. Calls these are 1:1 with the
    Telegram command set so both interfaces stay in sync (Spec v4 §12.2)."""

    def __init__(
        self,
        config,
        op_backlog: OPBacklog,
        store: ArtifactStore,
        cycles: CycleManager,
        wiki: WikiManager,
        instances: InstanceManager,
        models_mgr: ModelAssignmentManager,
        sequences: SequenceManager,
        sys_trigger: Callable[[Optional[str]], Awaitable[Any]],
        agent_retry: Callable[[str], Awaitable[Any]],
        agent_pause: Callable[[str], None],
        agent_resume: Callable[[str], None],
        process_disposition: Callable[..., Awaitable[str]],
        state_dir: str | Path,
        router: Any | None = None,
        admin_backlog: Any | None = None,
        role_manager: Any | None = None,
        telegram_bot: Any | None = None,
    ) -> None:
        self.config = config
        self.op_backlog = op_backlog
        # Spec v5 §10.1 — Admin OP backlog (GOV + role requests).
        self.admin_backlog = admin_backlog
        self.role_manager = role_manager
        self.telegram_bot = telegram_bot
        self.store = store
        self.cycles = cycles
        self.wiki = wiki
        self.instances = instances
        self.models = models_mgr
        self.sequences = sequences
        self.sys_trigger = sys_trigger
        self.agent_retry = agent_retry
        self.agent_pause = agent_pause
        self.agent_resume = agent_resume
        self.process_disposition = process_disposition
        self.state_dir = Path(state_dir)
        self.router = router

    # ─── Status & monitoring ────────────────────────────────────────────────

    async def vega_status(self) -> dict[str, Any]:
        pending = self.op_backlog.list_pending()
        in_progress = self.op_backlog.list_in_progress()
        agents = {}
        for code in self.config.AGENTS:
            agents[code] = {
                "inbox_unprocessed": len(self.store.inbox(code).get_unprocessed()),
                "outbox_pending":    len(self.store.outbox(code).list_pending()),
                "instance":          self.instances.get_or_create(code),
                "model":             self.models.get(code),
            }
        return {
            "op_backlog": {"pending": len(pending), "in_progress": len(in_progress)},
            "agents": agents,
        }

    async def vega_backlog(self, role_info: dict | None = None) -> list[dict[str, Any]]:
        items = self.op_backlog.list_pending() + self.op_backlog.list_in_progress()
        # Admin OP sees both backlogs (Spec v5 §12.3).
        if (role_info or {}).get("role") == "ADMIN_OP" and self.admin_backlog is not None:
            items += (self.admin_backlog.list_pending()
                      + self.admin_backlog.list_in_progress())
        return [_artifact_dict(a) for a in items]

    async def vega_agent(self, code: str) -> dict[str, Any]:
        code = code.upper()
        if code not in self.config.AGENTS:
            return {"error": f"unknown agent: {code}"}
        inbox = self.store.inbox(code).get_unprocessed()
        outbox = self.store.outbox(code).list_pending()
        last_exec = _last_execution_for(self.state_dir, code)
        return {
            "code":             code,
            "instance":         self.instances.get_or_create(code),
            "model":            self.models.get(code),
            "inbox":            len(inbox),
            "outbox":           len(outbox),
            "last_execution":   last_exec,
        }

    async def vega_cycles(self) -> list[str]:
        return sorted(p.stem for p in self.cycles.active_dir.glob("*.json"))

    async def vega_history(self, artifact_id: str) -> list[dict[str, Any]]:
        log = load_json(self.state_dir / "routing_log.json", default=[])
        return [e for e in log if e.get("artifact_id") == artifact_id]

    # ─── Decision (Spec §11) ────────────────────────────────────────────────

    async def vega_approve(self, artifact_id: str) -> str:
        return await self.process_disposition(artifact_id, "approve", "")

    async def vega_reject(self, artifact_id: str, reason: str = "") -> str:
        return await self.process_disposition(artifact_id, "reject", reason)

    async def vega_modify(self, artifact_id: str, instructions: str = "") -> str:
        return await self.process_disposition(artifact_id, "modify", instructions)

    async def vega_exchange(self, artifact_id: str, message: str,
                            role_info: dict | None = None) -> str:
        """Multi-turn SG↔OP or SYS↔Admin-OP dialogue (Spec v5 §6.4 + §12.3).

        Cycle-internal (audit P1-2): the turn is appended to the cycle opened by
        `artifact_id` and the partner agent is flagged for execution. NO artifact
        is minted and nothing is routed (Spec §6.4). The partner's reply is
        relayed back through the main loop / notification channel.
        """
        cycle = self.cycles.get_active_by_artifact(artifact_id)
        if cycle is None:
            return f"No active exchange for {artifact_id}."
        # Partner = the non-OP participant (SG for prop_exchange, SYS for
        # gov_exchange). primary_agent records exactly that.
        partner = cycle.primary_agent or next(
            (p for p in cycle.participants if p not in ("OP", "ADMIN_OP")), "SG")
        role = (role_info or {}).get("role", "OP")
        self.cycles.append_turn(cycle, message, role)
        flag_for_execution(self.state_dir, partner, cycle.id)
        return f"Message added to {cycle.id}. {partner} will respond."

    # ─── Lobby / role management (Spec §12.2-§12.3) ──────────────────────────

    async def vega_request_access(self, name: str, telegram_id: str,
                                  role: str, project: str = "") -> str:
        """Lobby tool — unauthenticated role request. Notifies Admin OP."""
        if role not in ("OP", "DE", "EXT"):
            return f"Invalid role: {role}. Must be OP, DE, or EXT."
        if self.role_manager is not None and self.telegram_bot is not None:
            admin_chat = self.role_manager.get_telegram_id("ADMIN_OP")
            if admin_chat:
                await self.telegram_bot.send(
                    f"📋 Role request:\nName: {name}\nRole: {role}\n"
                    f"Project: {project}\nTelegram: {telegram_id}\n\n"
                    f"Approve via `/role assign` or vega_assign_role.",
                    chat_id=admin_chat)
        return "Request submitted. Admin will review."

    async def vega_assign_role(self, name: str, telegram_id: str, role: str,
                               project: str = "", role_info: dict | None = None) -> str:
        otp = self.role_manager.initiate_assign(
            (role_info or {}).get("telegram_id"), name, telegram_id, role, project)
        return (f"2FA required. Code {otp} sent to your Telegram. "
                f"Confirm with vega_activate_role/APPROVE or enter it here.")

    async def vega_modify_role(self, role: str, changes: dict,
                               role_info: dict | None = None) -> str:
        otp = self.role_manager.initiate_modify(
            (role_info or {}).get("telegram_id"), role, changes)
        return f"2FA required. Code {otp} sent to your Telegram. Enter it to confirm."

    async def vega_revoke_role(self, role: str, telegram_id: str = "",
                               role_info: dict | None = None) -> str:
        otp = self.role_manager.initiate_revoke(
            (role_info or {}).get("telegram_id"), role, telegram_id)
        return f"2FA required. Code {otp} sent to your Telegram. Enter it to confirm."

    async def vega_roles(self) -> dict:
        return self.role_manager.roles_summary()

    async def vega_activate_role(self, invite_code: str, otp: str = "") -> str:
        """Two-phase activation (D-ARCH-040): phase 1 sends an OTP to the
        invitee's Telegram; phase 2 verifies it and returns the permanent token."""
        rm = self.role_manager
        if otp:
            pending = rm._pending_actions.get(invite_code)
            if not pending or pending.get("action") != "activate":
                return "No pending activation. Request a fresh invite."
            import time as _t
            if _t.time() > pending["expires"]:
                return "Expired. Request a new invite from Admin OP."
            if otp != pending["otp"]:
                rm._log_event("2fa_failed", invite_code=invite_code, result="failed")
                return "Invalid code. Try again."
            token = rm.complete_activation(invite_code)
            del rm._pending_actions[invite_code]
            if token:
                return (f"✅ Role activated.\nYour permanent token: {token}\n"
                        f"Update your MCP connection with this token. "
                        f"This is the only time it will be shown.")
            return "Activation failed."
        invite = rm.activate_invite(invite_code)
        if not invite:
            return "Invalid or expired invite code."
        new_otp = rm._generate_otp()
        import time as _t
        rm._pending_actions[invite_code] = {
            "action": "activate", "invite": invite,
            "otp": new_otp, "expires": _t.time() + rm.otp_expiry,
        }
        if self.telegram_bot is not None:
            await self.telegram_bot.send(f"Activation code: {new_otp}",
                                         chat_id=invite["telegram_id"])
        return "Activation code sent to your Telegram. Call again with the otp."

    async def vega_config_notifications(self, role: str, channel: str,
                                        mode: str) -> str:
        path = Path(getattr(self.config, "NOTIFICATIONS_FILE",
                            self.state_dir / "notifications.json"))
        prefs = load_json(path, default={})
        prefs.setdefault(role, {})[channel] = mode
        from state_manager import atomic_write
        atomic_write(path, json.dumps(prefs, indent=2))
        return f"{role}.{channel} → {mode}"

    # ─── DE tools (Spec §12.2) ───────────────────────────────────────────────

    async def _de_to_sg(self, content: str, references=None) -> str:
        if self.router is None:
            return "router not wired"
        artifact_id = await self.sequences.next_id("DE", "DE_IN")
        a = Artifact(type="DE_IN", sender="DE", content=content,
                     recipient="SG", id=artifact_id, references=references or [])
        self.cycles.check_cycle_events(a, recipients=["SG"])
        await self.router.route(a)
        return artifact_id

    async def vega_de_respond(self, artifact_id: str, response: str) -> str:
        de_id = await self._de_to_sg(response, references=[artifact_id])
        return f"{de_id} sent to SG."

    async def vega_de_observe(self, message: str) -> str:
        de_id = await self._de_to_sg(message)
        return f"Observation {de_id} sent to SG."

    async def vega_de_history(self) -> list[dict]:
        return self.cycles.get_history_for_role("DE")

    async def vega_de_pending(self) -> list[str]:
        return [c.id for c in self.cycles.get_pending_for_role("DE")]

    async def vega_de_activity(self) -> list[dict]:
        return self.cycles.get_activity_summary("de_qa")

    # ─── EXT tools (Spec §12.2) ──────────────────────────────────────────────

    async def _ext_to_br(self, content: str) -> str:
        if self.router is None:
            return "router not wired"
        artifact_id = await self.sequences.next_id("EXT", "BRQ")
        a = Artifact(type="BRQ", sender="EXT", content=content,
                     recipient="BR", id=artifact_id)
        self.cycles.check_cycle_events(a, recipients=["BR"])
        await self.router.route(a)
        return artifact_id

    async def vega_ext_submit(self, results: str) -> str:
        brq_id = await self._ext_to_br(results)
        return f"{brq_id} submitted to BR."

    async def vega_ext_ask(self, question: str) -> str:
        brq_id = await self._ext_to_br(question)
        return f"Question {brq_id} sent to BR."

    async def vega_ext_history(self) -> list[dict]:
        return self.cycles.get_history_for_role("EXT")

    async def vega_ext_pending(self) -> list[str]:
        return [c.id for c in self.cycles.get_pending_for_role("EXT")]

    async def vega_ext_activity(self) -> list[dict]:
        return self.cycles.get_activity_summary("build_scope_qa")

    async def vega_resolve(self, gov_id: str, action: str = "acknowledged") -> str:
        """Close an active GOV exchange (Spec v5 §10.1 / §12.3 — Admin OP).
        GOV lives in the admin_backlog now; fall back to op_backlog for safety."""
        backlog = self.admin_backlog or self.op_backlog
        if backlog.find(gov_id) is None:
            return f"No backlog item for {gov_id}"
        # Spec v5 §10.1 / audit P1-1 — persist the action note with the item.
        backlog.resolve(gov_id, resolution=action)
        self.cycles.close_by_artifact(gov_id)
        return f"{gov_id} resolved ({action}); gov_exchange cycle closed"

    # ─── Audit & governance ─────────────────────────────────────────────────

    async def vega_sys(self, instruction: str = "") -> dict[str, Any]:
        return await self.sys_trigger(instruction or None)

    async def vega_thinking(self, artifact_id: str) -> str:
        index = load_json(self.state_dir / "artifact_index.json", default={})
        record = index.get(artifact_id)
        if not record:
            return ""
        log = load_json(self.state_dir / "execution_log.json", default=[])
        if not isinstance(log, list):
            return ""
        target = record.get("execution_id")
        for e in reversed(log):
            if e.get("execution_id") == target:
                blocks = e.get("thinking_blocks", [])
                return "\n\n".join(blocks)
        return ""

    async def vega_wiki(self, code: str) -> dict[str, int]:
        code = code.upper()
        wiki_dir = Path(self.config.AGENTS_DIR) / code / "wiki"
        if not wiki_dir.exists():
            return {}
        return {p.name: p.stat().st_size for p in sorted(wiki_dir.glob("*.md"))}

    async def vega_log(self, code: str, n: int = 10) -> str:
        code = code.upper()
        path = Path(self.config.AGENTS_DIR) / code / "wiki" / "log.md"
        if not path.exists():
            return ""
        import re
        text = path.read_text()
        log_entry_re = re.compile(r"(?m)^## \[\d{4}-\d{2}-\d{2}")
        matches = list(log_entry_re.finditer(text))
        if not matches:
            return text[-3500:]
        offsets = [m.start() for m in matches] + [len(text)]
        entries = [text[offsets[i]:offsets[i + 1]] for i in range(len(matches))]
        return "".join(entries[-n:])

    # ─── Control ────────────────────────────────────────────────────────────

    async def vega_pause(self, code: str) -> str:
        self.agent_pause(code.upper())
        return f"paused {code.upper()}"

    async def vega_resume(self, code: str) -> str:
        self.agent_resume(code.upper())
        return f"resumed {code.upper()}"

    async def vega_rotate(self, code: str) -> str:
        new_id = await self.instances.rotate(code.upper())
        return new_id

    async def vega_model(self, code: str, model: str) -> str:
        await self.models.set(code.upper(), model)
        return f"{code.upper()} → {model}"

    # ─── Scope input + relay ────────────────────────────────────────────────

    async def vega_request(self, message: str) -> str:
        """Spec v4 §2.1 S13 — submit REQ-OP-NNN to SG."""
        if self.router is None:
            return "router not wired"
        artifact_id = await self.sequences.next_id("OP", "REQ")
        a = Artifact(type="REQ", sender="OP", content=message,
                     recipient="SG", id=artifact_id)
        await self.router.route(a)
        return f"{artifact_id} submitted to SG"

    # Spec v5 §12.2: vega_build / vega_expert (OP relay) are REMOVED. DE and EXT
    # are first-class roles with their own MCP tools (vega_de_*, vega_ext_*) —
    # no OP relay. See vega_de_respond / vega_ext_submit above.


# ─── Tool registry — MCP tool name → method name + JSON schema ──────────────

TOOL_REGISTRY: dict[str, dict[str, Any]] = {
    # Monitoring / read-only
    "vega_status":   {"method": "vega_status",   "params": {}},
    "vega_backlog":  {"method": "vega_backlog",  "params": {}},
    "vega_agent":    {"method": "vega_agent",    "params": {"code": "string"}},
    "vega_cycles":   {"method": "vega_cycles",   "params": {}},
    "vega_history":  {"method": "vega_history",  "params": {"artifact_id": "string"}},
    "vega_thinking": {"method": "vega_thinking", "params": {"artifact_id": "string"}},
    "vega_wiki":     {"method": "vega_wiki",     "params": {"code": "string"}},
    "vega_log":      {"method": "vega_log",      "params": {"code": "string",
                                                            "n":    "integer"}},

    # OP — scope decisions
    "vega_approve":  {"method": "vega_approve",  "params": {"artifact_id": "string"}},
    "vega_reject":   {"method": "vega_reject",   "params": {"artifact_id": "string",
                                                            "reason":      "string"}},
    "vega_modify":   {"method": "vega_modify",   "params": {"artifact_id":  "string",
                                                            "instructions": "string"}},
    "vega_exchange": {"method": "vega_exchange", "params": {"artifact_id": "string",
                                                            "message":     "string"}},
    "vega_request":  {"method": "vega_request",  "params": {"message": "string"}},
    "vega_de_activity":  {"method": "vega_de_activity",  "params": {}},
    "vega_ext_activity": {"method": "vega_ext_activity", "params": {}},

    # Admin OP — governance + role mgmt + control
    "vega_sys":      {"method": "vega_sys",      "params": {"instruction": "string"}},
    "vega_resolve":  {"method": "vega_resolve",  "params": {"gov_id": "string",
                                                            "action": "string"}},
    "vega_assign_role": {"method": "vega_assign_role", "params": {
        "name": "string", "telegram_id": "string", "role": "string",
        "project": "string"}},
    "vega_modify_role": {"method": "vega_modify_role", "params": {
        "role": "string", "changes": "object"}},
    "vega_revoke_role": {"method": "vega_revoke_role", "params": {
        "role": "string", "telegram_id": "string"}},
    "vega_roles":    {"method": "vega_roles",    "params": {}},
    "vega_activate_role": {"method": "vega_activate_role", "params": {
        "invite_code": "string", "otp": "string"}},
    "vega_config_notifications": {"method": "vega_config_notifications", "params": {
        "role": "string", "channel": "string", "mode": "string"}},
    "vega_pause":    {"method": "vega_pause",    "params": {"code": "string"}},
    "vega_resume":   {"method": "vega_resume",   "params": {"code": "string"}},
    "vega_rotate":   {"method": "vega_rotate",   "params": {"code": "string"}},
    "vega_model":    {"method": "vega_model",    "params": {"code":  "string",
                                                            "model": "string"}},

    # DE — domain expertise (direct with SG)
    "vega_de_respond": {"method": "vega_de_respond", "params": {
        "artifact_id": "string", "response": "string"}},
    "vega_de_observe": {"method": "vega_de_observe", "params": {"message": "string"}},
    "vega_de_history": {"method": "vega_de_history", "params": {}},
    "vega_de_pending": {"method": "vega_de_pending", "params": {}},

    # EXT — build interaction (direct with BR)
    "vega_ext_submit": {"method": "vega_ext_submit", "params": {"results": "string"}},
    "vega_ext_ask":    {"method": "vega_ext_ask",    "params": {"question": "string"}},
    "vega_ext_history": {"method": "vega_ext_history", "params": {}},
    "vega_ext_pending": {"method": "vega_ext_pending", "params": {}},

    # Lobby / onboarding
    "vega_request_access": {"method": "vega_request_access", "params": {
        "name": "string", "telegram_id": "string", "role": "string",
        "project": "string"}},
}


# ─── Role → visible tools (Spec v5 §12.3 ROLE_TOOLS) ─────────────────────────
# `None` = lobby (no token); "_PENDING" = invite holder, not yet activated.
ROLE_TOOLS: dict[str | None, list[str]] = {
    None: ["vega_request_access"],
    "_PENDING": ["vega_activate_role"],
    "OP": [
        "vega_approve", "vega_reject", "vega_modify", "vega_request",
        "vega_exchange", "vega_backlog", "vega_status", "vega_agent",
        "vega_cycles", "vega_history", "vega_thinking", "vega_wiki",
        "vega_log", "vega_de_activity", "vega_ext_activity",
    ],
    "ADMIN_OP": [
        "vega_sys", "vega_resolve", "vega_exchange",
        "vega_assign_role", "vega_modify_role", "vega_revoke_role",
        "vega_roles", "vega_activate_role",
        "vega_model", "vega_rotate", "vega_pause", "vega_resume",
        "vega_config_notifications",
        "vega_status", "vega_backlog", "vega_agent", "vega_cycles",
        "vega_history", "vega_thinking", "vega_wiki", "vega_log",
    ],
    "DE": ["vega_de_respond", "vega_de_observe", "vega_de_history", "vega_de_pending"],
    "EXT": ["vega_ext_submit", "vega_ext_ask", "vega_ext_history", "vega_ext_pending"],
}

# Tools whose behaviour depends on the caller's role (they take role_info).
ROLE_AWARE_TOOLS = {
    "vega_exchange", "vega_backlog", "vega_assign_role",
    "vega_modify_role", "vega_revoke_role",
}


# ─── HTTP server ─────────────────────────────────────────────────────────────

class MCPServer:
    """aiohttp-based MCP-style JSON-RPC server.

    Endpoints:
      GET  /health            — liveness probe (no auth)
      GET  /mcp/tools         — tool list (auth required)
      POST /mcp/call          — invoke {name, arguments} (auth required)

    The shape is "MCP-style" rather than strict MCP-over-stdio, because
    this orchestrator runs as a long-lived process and the OP's Claude
    session connects over HTTP. The tool schemas are equivalent to MCP
    tool definitions.
    """

    def __init__(self, config, tools: MCPTools, role_manager=None) -> None:
        self.config = config
        self.tools = tools
        # Spec v5 §12.3 — role-scoped auth. Falls back to the MCPTools' own
        # role_manager reference if not passed explicitly.
        self.role_manager = role_manager or getattr(tools, "role_manager", None)
        self._runner: web.AppRunner | None = None
        self._site: web.BaseSite | None = None

    class _Unauthorized(Exception):
        """Invalid token (not lobby) → 401."""

    def _authenticate(self, request: web.Request) -> dict | None:
        """Spec v5 §12.3 — resolve the bearer token to role info.
          - no/empty Bearer  → None  (lobby)
          - valid permanent  → {"role": <ROLE>, ...}
          - valid invite     → {"role": "_PENDING", "invite_code": ..., "invite": ...}
          - anything else    → raise _Unauthorized (401)

        Back-compat: if no RoleManager is wired but a legacy MCP_AUTH_TOKEN is
        configured, that single token authenticates as OP."""
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return None  # lobby
        token = header[7:]
        if self.role_manager is not None:
            role_info = self.role_manager.verify_token(token)
            if role_info:
                return role_info
            invite = self.role_manager.activate_invite(token)
            if invite:
                return {"role": "_PENDING", "invite_code": token, "invite": invite}
        # Legacy single-token fallback — keeps an existing single-operator
        # deployment working as OP until real roles are assigned (mirrors the
        # Telegram unconfigured-superuser back-compat). Once role tokens exist
        # they take precedence; the legacy token still resolves to OP.
        legacy = getattr(self.config, "MCP_AUTH_TOKEN", "") or ""
        if legacy and token == legacy:
            return {"role": "OP"}
        raise self._Unauthorized()

    @staticmethod
    def _tool_defs(role: str | None) -> list[dict]:
        defs = []
        for name in ROLE_TOOLS.get(role, []):
            spec = TOOL_REGISTRY.get(name)
            if not spec:
                continue
            defs.append({
                "name": name,
                "input_schema": {
                    "type": "object",
                    "properties": {k: {"type": v} for k, v in spec["params"].items()},
                },
            })
        return defs

    async def _health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def _list_tools(self, request: web.Request) -> web.Response:
        try:
            role_info = self._authenticate(request)
        except self._Unauthorized:
            return web.json_response({"error": "unauthorized"}, status=401)
        role = role_info["role"] if role_info else None
        return web.json_response({"tools": self._tool_defs(role)})

    async def _call_tool(self, request: web.Request) -> web.Response:
        try:
            role_info = self._authenticate(request)
        except self._Unauthorized:
            return web.json_response({"error": "unauthorized"}, status=401)
        role = role_info["role"] if role_info else None
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "invalid json"}, status=400)
        name = payload.get("name")
        args = payload.get("arguments", {}) or {}
        if name not in TOOL_REGISTRY:
            return web.json_response({"error": f"unknown tool: {name}"}, status=404)
        # Enforce role membership (Spec §12.3).
        if name not in ROLE_TOOLS.get(role, []):
            return web.json_response(
                {"error": f"tool {name} not available for role {role}"}, status=403)
        method = getattr(self.tools, TOOL_REGISTRY[name]["method"])
        if name in ROLE_AWARE_TOOLS:
            args = {**args, "role_info": role_info}
        try:
            result = await method(**args)
        except TypeError as e:
            return web.json_response({"error": f"bad arguments: {e}"}, status=400)
        except Exception as e:
            return web.json_response(
                {"error": f"{type(e).__name__}: {e}"}, status=500)
        return web.json_response({"result": result})

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/health", self._health)
        app.router.add_get("/mcp/tools", self._list_tools)
        app.router.add_post("/mcp/call", self._call_tool)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        host = getattr(self.config, "MCP_HOST", "127.0.0.1")
        port = getattr(self.config, "MCP_PORT", 8765)
        self._site = web.TCPSite(self._runner, host, port)
        await self._site.start()
        print(f"[mcp] listening on http://{host}:{port}", flush=True)

    async def stop(self) -> None:
        if self._site is not None:
            await self._site.stop()
        if self._runner is not None:
            await self._runner.cleanup()


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _artifact_dict(a: Artifact) -> dict[str, Any]:
    return {
        "id":        a.id,
        "type":      a.type,
        "sender":    a.sender,
        "recipient": a.recipient,
        "priority":  a.priority,
        "timestamp": a.timestamp,
        "emoji":     PRIORITY_EMOJI.get(a.priority or "P2", "🟡"),
    }


def _last_execution_for(state_dir: Path, agent_code: str) -> dict | None:
    log = load_json(state_dir / "execution_log.json", default=[])
    if not isinstance(log, list):
        return None
    for entry in reversed(log):
        if entry.get("agent") == agent_code:
            return {
                "execution_id":     entry.get("execution_id"),
                "timestamp":        entry.get("timestamp"),
                "duration_seconds": entry.get("duration_seconds"),
            }
    return None
