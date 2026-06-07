"""
Telegram bot — OP interface.

Per Spec §10 and §11. OP has two channels:
  - SG via PROP / AUTH / SUM (the working exchange)
  - SYS via GOV

This bot handles:
  - Notifications to OP (PROP, GOV, external relay)
  - OP commands (/help, /status, /backlog, /approve, /reject, /modify, etc.)
  - Multi-turn PROP exchange (OP replies without a slash command → forwarded to SG)
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Optional

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application, CommandHandler, ContextTypes, MessageHandler, filters,
)

from artifact_store import ArtifactStore
from cycle_manager import CycleManager
from models import Artifact, PRIORITY_EMOJI, utcnow_iso
from backlog import OPBacklog, make_auth
from sequence_manager import InstanceManager, ModelAssignmentManager, SequenceManager
from state_manager import flag_for_execution, load_json
from wiki_manager import WikiManager

if TYPE_CHECKING:
    from router import Router


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _summary(artifact: Artifact, max_chars: int = 600) -> str:
    """First lines of the artifact content, trimmed."""
    body = artifact.content.strip()
    if len(body) <= max_chars:
        return body
    return body[:max_chars].rsplit(" ", 1)[0] + "…"


def _thinking_snippet(thinking_blocks: list[str], max_chars: int = 400) -> str:
    if not thinking_blocks:
        return ""
    joined = " ".join(b.replace("\n", " ").strip() for b in thinking_blocks)
    return joined[:max_chars] + ("…" if len(joined) > max_chars else "")


# ─── Bot ─────────────────────────────────────────────────────────────────────

class TelegramBot:

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
        agent_pause: Callable[[str], None],
        agent_resume: Callable[[str], None],
        state_dir: str | Path,
    ) -> None:
        self.config = config
        self.op_backlog = op_backlog
        self.store = store
        self.cycles = cycles
        self.wiki = wiki
        self.instances = instances
        self.models = models_mgr
        self.sequences = sequences   # Spec §7.2 — AUTH IDs via SequenceManager, not string splicing
        self.sys_trigger = sys_trigger
        self.agent_pause = agent_pause
        self.agent_resume = agent_resume
        self.state_dir = Path(state_dir)

        # Set after construction (avoid circular import)
        self.router: Optional["Router"] = None
        # RoleManager — set after construction. Enables role-scoped Telegram
        # dispatch and per-role notification targeting (Spec v5 §10.2, §12.4).
        self.role_manager = None
        # Admin OP backlog — set after construction (GOV exchange, Spec §10.1).
        self.admin_backlog = None

        self.app: Application | None = None
        self.chat_id = config.TELEGRAM_OP_CHAT_ID
        self.token = config.TELEGRAM_BOT_TOKEN
        self._exchange_resolvers: dict[str, str] = {}  # in-progress PROP -> last SG instance

    # ─── Lifecycle ───────────────────────────────────────────────────────────

    async def start_polling(self) -> None:
        if not self.token:
            print("[telegram] No TELEGRAM_BOT_TOKEN — bot disabled. Use CLI shim for OP commands.")
            return
        self.app = Application.builder().token(self.token).build()
        self._register_handlers(self.app)
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling()
        # Keep alive
        while True:
            await asyncio.sleep(3600)

    async def send(self, text: str, role: str | None = None,
                   chat_id: str | None = None) -> None:
        """Send a message. Target resolution (Spec v5 §10.2/§12.4):
          - explicit `chat_id` wins (used by role-scoped handlers replying to a
            specific person, and by OTP/invite delivery);
          - else `role` is resolved to a chat via the RoleManager;
          - else the default OP chat (`config.TELEGRAM_OP_CHAT_ID`).
        Falls back to plain text if Markdown parsing fails on the body; truncates
        over Telegram's 4096-char limit, splitting at a newline if possible."""
        target = chat_id
        if target is None and role is not None and self.role_manager is not None:
            target = self.role_manager.get_telegram_id(role)
        if target is None:
            target = self.chat_id
        if not self.app or not target:
            print(f"[telegram-shim] {text}")
            return

        # Telegram's hard message limit is 4096 chars.
        MAX = 4000
        if len(text) > MAX:
            # Try to cut at the last newline before the limit so we don't slice
            # mid-token; append an explicit truncation marker.
            cut = text.rfind("\n", 0, MAX)
            if cut < MAX // 2:
                cut = MAX
            text = text[:cut] + "\n\n…(truncated; full content in artifacts/archive/)"

        try:
            await self.app.bot.send_message(
                chat_id=target, text=text, parse_mode=ParseMode.MARKDOWN,
            )
        except BadRequest as e:
            msg = str(e).lower()
            if "parse" in msg or "entities" in msg or "markdown" in msg:
                # Artifact content has chars that broke Markdown (unescaped _, *,
                # [, etc.). Resend as plain text so the message reaches the role.
                await self.app.bot.send_message(
                    chat_id=target, text=text,
                    # parse_mode omitted → plain text
                )
            else:
                raise

    # ─── Notifications ───────────────────────────────────────────────────────

    async def notify(self, artifact: Artifact, role: str | None = None) -> None:
        """Notify a decision role of an inbound backlog item. `role` selects the
        target chat AND the offered actions (Spec v5 §10.2): OP gets the scope
        decision verbs; Admin OP (GOV) gets /resolve. Falls back to the default
        OP chat when role routing isn't configured."""
        emoji = PRIORITY_EMOJI.get(artifact.priority or "P2", "🟡")
        thinking = self._latest_thinking(artifact)
        thinking_section = f"\n💭 _{artifact.sender} reasoning:_ {thinking}\n" if thinking else ""
        # Role-adapted actions: Admin OP / GOV resolves; OP approves/rejects/modifies.
        if role == "ADMIN_OP" or artifact.type == "GOV":
            actions = f"Reply to discuss, or:\n`/resolve {artifact.id} <action>`"
        elif role == "OP" or role is None:
            actions = (f"Reply to discuss, or:\n"
                       f"`/approve {artifact.id}`\n"
                       f"`/reject {artifact.id} <reason>`\n"
                       f"`/modify {artifact.id} <instructions>`")
        else:
            actions = ""
        msg = (
            f"{emoji} *{artifact.id}* ({artifact.priority or 'P2'})\n"
            f"From: {artifact.sender} ({artifact.sender_model or '?'})\n\n"
            f"{_summary(artifact)}\n"
            f"{thinking_section}\n"
            f"{actions}"
        )
        await self.send(msg, role=role)

    async def notify_external_relay(self, artifact: Artifact, target: str) -> None:
        """Notify a DE/EXT role of an inbound artifact (Spec v5 §12.5-§12.6).

        DE and EXT are first-class roles now — they respond DIRECTLY via their own
        role-scoped tools, not via an OP relay. The message is sent to the target
        role's own chat (falling back to the default chat for unconfigured
        single-operator deployments)."""
        respond_cmd = {"DE": "/de_respond", "EXT": "/ext_submit"}.get(target)
        hint = (f"_Respond via_ `{respond_cmd}`." if respond_cmd
                else f"_Awaiting {target} response._")
        msg = (
            f"📤 *For {target}*\n"
            f"*{artifact.id}* ({artifact.type})\n\n"
            f"{_summary(artifact)}\n\n"
            f"{hint}"
        )
        # Target the role's chat when role routing is configured; else default.
        await self.send(msg, role=target if target in ("DE", "EXT") else None)

    # Which decision role owns the human side of an agent's exchange cycle:
    # SG↔OP (prop_exchange) → OP; SYS↔Admin OP (gov_exchange) → ADMIN_OP.
    _EXCHANGE_PARTNER_ROLE = {"SG": "OP", "SYS": "ADMIN_OP"}

    async def relay_exchange_reply(self, agent_code: str, cycle_id: str,
                                   text: str) -> None:
        """Relay a partner agent's cycle-internal exchange reply to the human role
        that owns the exchange (Spec §6.4): SG→OP, SYS→Admin OP.

        The reply is a conversational turn (no artifact), so it doesn't flow
        through notify(); we surface it directly to the right role's chat. If the
        same turn also produced a formal artifact (e.g. SG closes with an AUTH),
        that rides the normal routing + notification path on the next tick."""
        role = self._EXCHANGE_PARTNER_ROLE.get(agent_code)
        msg = (
            f"💬 *{agent_code} response* (cycle {cycle_id})\n\n"
            f"{text}\n\n"
            f"Reply to continue, or `/approve` / `/reject` / `/modify`."
        )
        await self.send(msg, role=role)

    async def handle_sg_exchange_response(self, artifact: Artifact) -> None:
        """During PROP exchange: SG's response round-trips to the OP role's chat."""
        thinking = self._latest_thinking(artifact)
        thinking_section = f"\n💭 _SG thinking:_ {thinking}\n" if thinking else ""
        msg = (
            f"💬 *SG response* (re: {artifact.references[0] if artifact.references else '?'})\n\n"
            f"{_summary(artifact)}\n"
            f"{thinking_section}\n"
            f"Reply to continue, or `/approve` / `/reject` / `/modify`."
        )
        await self.send(msg, role="OP")

    # ─── Role resolution + authorization (Spec v5 §10.2/§12.4) ───────────────

    def _is_unconfigured(self) -> bool:
        """True when no roles are assigned yet — a single-operator deployment.
        In that mode the configured OP chat acts as a superuser so the bot keeps
        working before the role system is set up."""
        if self.role_manager is None:
            return True
        try:
            return not self.role_manager._load_roles()
        except Exception:
            return True

    def _role_for_update(self, update: Update) -> str | None:
        chat_id = update.effective_chat.id if update.effective_chat else None
        if self.role_manager is not None:
            role = self.role_manager.get_role_by_telegram(chat_id)
            if role:
                return role
        if self._is_unconfigured() and str(chat_id) == str(self.chat_id):
            return "OP"   # back-compat superuser
        return None

    async def _authorize(self, update: Update, allowed: set[str]) -> str | None:
        """Return the caller's role if it is in `allowed`, else reply with a
        refusal and return None. Unconfigured-deployment superuser (the default
        OP chat) passes every gate so single-operator setups keep working."""
        chat_id = update.effective_chat.id if update.effective_chat else None
        if self._is_unconfigured() and str(chat_id) == str(self.chat_id):
            return "ADMIN_OP"   # superuser: full access until roles are configured
        role = self.role_manager.get_role_by_telegram(chat_id) if self.role_manager else None
        if role is None:
            await self._reply(update,
                "Access not configured. Use MCP to request access via "
                "`vega_request_access()`.")
            return None
        if role not in allowed:
            await self._reply(update,
                f"⛔ This command isn't available for your role ({role}).")
            return None
        return role

    # ─── Command handlers ────────────────────────────────────────────────────

    def _register_handlers(self, app: Application) -> None:
        app.add_handler(CommandHandler("help", self._cmd_help))
        app.add_handler(CommandHandler("status", self._cmd_status))
        app.add_handler(CommandHandler("backlog", self._cmd_backlog))
        app.add_handler(CommandHandler("pending", self._cmd_pending))
        app.add_handler(CommandHandler("agent", self._cmd_agent))
        app.add_handler(CommandHandler("agents", self._cmd_agents))
        app.add_handler(CommandHandler("history", self._cmd_history))
        app.add_handler(CommandHandler("approve", self._cmd_approve))
        app.add_handler(CommandHandler("reject", self._cmd_reject))
        app.add_handler(CommandHandler("modify", self._cmd_modify))
        app.add_handler(CommandHandler("sys", self._cmd_sys))
        app.add_handler(CommandHandler("wiki", self._cmd_wiki))
        app.add_handler(CommandHandler("log", self._cmd_log))
        app.add_handler(CommandHandler("thinking", self._cmd_thinking))
        app.add_handler(CommandHandler("pause", self._cmd_pause))
        app.add_handler(CommandHandler("resume", self._cmd_resume))
        app.add_handler(CommandHandler("rotate", self._cmd_rotate))
        app.add_handler(CommandHandler("run", self._cmd_run))
        app.add_handler(CommandHandler("retry", self._cmd_run))   # deprecated alias
        app.add_handler(CommandHandler("model", self._cmd_model))
        app.add_handler(CommandHandler("models", self._cmd_models))
        app.add_handler(CommandHandler("request", self._cmd_request))
        app.add_handler(CommandHandler("resolve", self._cmd_resolve))
        app.add_handler(CommandHandler("routing", self._cmd_routing))
        app.add_handler(CommandHandler("decisions", self._cmd_decisions))
        app.add_handler(CommandHandler("framework", self._cmd_framework))
        app.add_handler(CommandHandler("cycles", self._cmd_cycles))
        app.add_handler(CommandHandler("scope", self._cmd_scope))
        app.add_handler(CommandHandler("snapshots", self._cmd_snapshots))
        app.add_handler(CommandHandler("verify", self._cmd_verify))
        app.add_handler(CommandHandler("restore", self._cmd_restore))
        # Admin OP — role management + notification config (Spec v5 §12.4)
        app.add_handler(CommandHandler("role", self._cmd_role))
        app.add_handler(CommandHandler("roles", self._cmd_roles))
        app.add_handler(CommandHandler("config_notify", self._cmd_config_notify))
        # DE — domain expertise direct with SG (Spec v5 §12.6)
        app.add_handler(CommandHandler("de_respond", self._cmd_de_respond))
        app.add_handler(CommandHandler("de_observe", self._cmd_de_observe))
        app.add_handler(CommandHandler("de_pending", self._cmd_de_pending))
        app.add_handler(CommandHandler("de_history", self._cmd_de_history))
        # EXT — build interaction direct with BR (Spec v5 §12.5)
        app.add_handler(CommandHandler("ext_submit", self._cmd_ext_submit))
        app.add_handler(CommandHandler("ext_ask", self._cmd_ext_ask))
        app.add_handler(CommandHandler("ext_pending", self._cmd_ext_pending))
        app.add_handler(CommandHandler("ext_history", self._cmd_ext_history))
        # Fall-through: non-slash messages → role-based freeform (exchange / APPROVE)
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text))

    async def _reply(self, update: Update, text: str) -> None:
        await update.effective_message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def _cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        # Role-aware help (Spec v5 §12.4). Unconfigured deployments / the default
        # OP chat see the OP view.
        role = self._role_for_update(update) or "OP"
        await self._reply(update, _HELP_BY_ROLE.get(role, _OP_HELP))

    async def _cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        pending = self.op_backlog.list_pending()
        in_progress = self.op_backlog.list_in_progress()
        lines = ["*VEGA status*"]
        lines.append(f"OP backlog — pending: {len(pending)}, in progress: {len(in_progress)}")
        for code in self.config.AGENTS:
            inbox_count = len(self.store.inbox(code).get_unprocessed())
            outbox_count = len(self.store.outbox(code).list_pending())
            lines.append(f"  {code}: inbox {inbox_count}, outbox {outbox_count}")
        await self._reply(update, "\n".join(lines))

    async def _cmd_backlog(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        items = self.op_backlog.list_pending() + self.op_backlog.list_in_progress()
        if not items:
            await self._reply(update, "Backlog empty.")
            return
        lines = ["*OP backlog*"]
        for a in items:
            emoji = PRIORITY_EMOJI.get(a.priority or "P2", "🟡")
            lines.append(f"{emoji} {a.id} ({a.type}) — {a.priority or 'P2'}")
        await self._reply(update, "\n".join(lines))

    async def _cmd_pending(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        items = self.op_backlog.list_in_progress()
        if not items:
            await self._reply(update, "No active exchanges.")
            return
        lines = ["*Active PROP exchanges*"]
        for a in items:
            lines.append(f"• {a.id} ({a.type})")
        await self._reply(update, "\n".join(lines))

    async def _cmd_agent(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Spec §11 — /agent <CODE> shows: last execution, wiki size, inbox."""
        if not ctx.args:
            await self._reply(update, "Usage: `/agent <CODE>`")
            return
        code = ctx.args[0].upper()
        if code not in self.config.AGENTS:
            await self._reply(update, f"Unknown agent: {code}")
            return
        inbox = self.store.inbox(code).get_unprocessed()
        outbox = self.store.outbox(code).list_pending()
        instance = self.instances.get_or_create(code)
        model = self.models.get(code)

        # Last execution (Spec §11 — "last execution")
        last_exec = self._last_execution_for(code)
        if last_exec:
            last_line = (
                f"Last execution: {last_exec.get('execution_id')} at "
                f"{last_exec.get('timestamp')} "
                f"(dur {last_exec.get('duration_seconds', '?')}s)"
            )
        else:
            last_line = "Last execution: none recorded"

        # Wiki size (Spec §11 — "wiki size")
        wiki_dir = Path(self.config.AGENTS_DIR) / code / "wiki"
        wiki_files, wiki_bytes = self._wiki_stats(wiki_dir)
        wiki_line = (
            f"Wiki: {wiki_files} file(s), {wiki_bytes:,} bytes"
            if wiki_files else
            "Wiki: (empty)"
        )

        lines = [
            f"*{code}*",
            f"Instance: {instance}",
            f"Model: {model}",
            last_line,
            wiki_line,
            f"Inbox unprocessed: {len(inbox)}",
            f"Outbox pending: {len(outbox)}",
        ]
        await self._reply(update, "\n".join(lines))

    def _last_execution_for(self, agent_code: str) -> dict | None:
        log = load_json(self.state_dir / "execution_log.json", default=[])
        if not isinstance(log, list):
            return None
        for entry in reversed(log):
            if entry.get("agent") == agent_code:
                return entry
        return None

    def _wiki_stats(self, wiki_dir: Path) -> tuple[int, int]:
        if not wiki_dir.exists():
            return 0, 0
        files = [p for p in wiki_dir.glob("*.md") if p.is_file()]
        total_bytes = sum(p.stat().st_size for p in files)
        return len(files), total_bytes

    async def _cmd_agents(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        lines = ["*Agents*"]
        for code in self.config.AGENTS:
            lines.append(f"• {code} — {AGENT_DESCRIPTIONS.get(code, '')}")
        await self._reply(update, "\n".join(lines))

    async def _cmd_history(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not ctx.args:
            await self._reply(update, "Usage: `/history <ARTIFACT-ID>`")
            return
        artifact_id = ctx.args[0]
        routing_log = load_json(self.state_dir / "routing_log.json", default=[])
        relevant = [e for e in routing_log if e.get("artifact_id") == artifact_id]
        if not relevant:
            await self._reply(update, f"No routing history for {artifact_id}")
            return
        lines = [f"*Routing history — {artifact_id}*"]
        for e in relevant:
            # Spec §17.2 — surface cycle_id in OP-facing routing history so
            # operators can correlate an artifact with the cycle that drove it.
            cycle = e.get("cycle_id")
            cycle_suffix = f"  [cycle: {cycle}]" if cycle else ""
            lines.append(
                f"• {e['timestamp']}: {e['sender']} → "
                f"{', '.join(e.get('routed_to', []))}{cycle_suffix}"
            )
        await self._reply(update, "\n".join(lines))

    async def _cmd_approve(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await self._handle_disposition(update, ctx, "approve")

    async def _cmd_reject(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await self._handle_disposition(update, ctx, "reject")

    async def _cmd_modify(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await self._handle_disposition(update, ctx, "modify")

    async def _handle_disposition(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                                  disposition: str) -> None:
        # Scope decisions are OP's (Spec v5 §12.4).
        if not await self._authorize(update, {"OP"}):
            return
        if not ctx.args:
            await self._reply(update, f"Usage: `/{disposition} <ARTIFACT-ID> [text]`")
            return
        artifact_id = ctx.args[0]
        rest = " ".join(ctx.args[1:]).strip()
        reply_text = await self.process_disposition(artifact_id, disposition, rest)
        await self._reply(update, reply_text)

    async def process_disposition(self, prop_id: str, disposition: str,
                                  text: str = "") -> str:
        """
        Create AUTH, archive it immutably, deliver to SG via router.

        Per Spec §7.2: AUTH and SUM together form the complete auditable record;
        BOTH are archived immutably. AUTH IDs are minted via SequenceManager
        per (OP, AUTH) — no string splicing of the PROP ID.

        Per Spec §13 step 4: resolved OP items route through the router so
        AUTH appears in routing_log.json and artifacts/archive/.
        """
        path = self.op_backlog.find(prop_id)
        if not path:
            return f"No backlog item for {prop_id}"
        if self.router is None:
            return f"Router not wired — cannot route AUTH for {prop_id}"

        auth = make_auth(
            prop_id=prop_id,
            disposition=disposition,
            reason=text if disposition == "reject" else "",
            modifications=text if disposition == "modify" else "",
        )
        # Spec §9 — AUTH gets its own (OP, AUTH) sequence counter
        auth.id = await self.sequences.next_id("OP", "AUTH")

        # Move PROP from pending/in_progress → resolved
        self.op_backlog.resolve(prop_id)

        # Open/close cycle events on AUTH (closes prop_exchange) — same pattern
        # as Orchestrator._tick uses for produced artifacts.
        self.cycles.check_cycle_events(auth, recipients=["SG"])

        # Route through the router: archives immutably + appends routing_log +
        # delivers to SG inbox. AUTH is now an auditable record.
        await self.router.route(auth)

        # SC-7 (§7.5): export a validated-state snapshot on APPROVE only (not
        # reject/modify). Shared path → both Telegram /approve and MCP vega_approve
        # get it. Never blocks the approve flow (write_snapshot swallows errors).
        if disposition == "approve":
            from snapshot_manager import write_snapshot
            await write_snapshot(auth, self.config, self.role_manager, self)

        emoji_label = {"approve": "✅", "reject": "❌", "modify": "✏️"}[disposition]
        return (
            f"{emoji_label} {auth.id} issued for {prop_id}. "
            f"Archived and routed to SG. SG will write SUM-SG-NNN next execution."
        )

    async def _cmd_sys(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        # Governance (SYS audits) is Admin OP's (Spec v5 §12.4).
        role = await self._authorize(update, {"ADMIN_OP"})
        if not role:
            return
        instruction = " ".join(ctx.args).strip()
        # Tag the audit source so SYS's record shows who requested it (audit
        # NEW-13 — matters now roles are split: OP vs Admin OP).
        tagged = f"[requested by {role} via Telegram] " + (instruction or "(no specific instruction)")
        await self._reply(update, "Triggering SYS audit…")
        result = await self.sys_trigger(tagged)
        await self._reply(update, f"SYS done: {result}")

    async def _cmd_wiki(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not ctx.args:
            await self._reply(update, "Usage: `/wiki <CODE>`")
            return
        code = ctx.args[0].upper()
        wiki_dir = Path(self.config.AGENTS_DIR) / code / "wiki"
        if not wiki_dir.exists():
            await self._reply(update, f"No wiki for {code}")
            return
        lines = [f"*{code} wiki*"]
        for path in sorted(wiki_dir.glob("*.md")):
            size = path.stat().st_size
            lines.append(f"• {path.name} ({size} bytes)")
        await self._reply(update, "\n".join(lines))

    async def _cmd_log(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not ctx.args:
            await self._reply(update, "Usage: `/log <CODE> [N]`")
            return
        code = ctx.args[0].upper()
        n = int(ctx.args[1]) if len(ctx.args) > 1 else 10
        # NEW-3 — shared reader on WikiManager (MCP vega_log uses the same).
        text = self.wiki.read_log(code, n)
        await self._reply(update, (text[-3500:] if text else f"No log for {code}"))

    async def _cmd_thinking(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not ctx.args:
            await self._reply(update, "Usage: `/thinking <ARTIFACT-ID>`")
            return
        artifact_id = ctx.args[0]
        index = load_json(self.state_dir / "artifact_index.json", default={})
        record = index.get(artifact_id)
        if not record:
            await self._reply(update, f"No execution record for {artifact_id}")
            return
        log = load_json(self.state_dir / "execution_log.json", default=[])
        if not isinstance(log, list):
            log = []
        match = next((e for e in log if e.get("execution_id") == record["execution_id"]), None)
        if not match:
            await self._reply(update, "Execution not found.")
            return
        blocks = match.get("thinking_blocks", [])
        if not blocks:
            await self._reply(update, "No thinking blocks recorded.")
            return
        text = "\n\n".join(blocks)[:3500]
        await self._reply(update, f"*Thinking — {artifact_id}*\n\n{text}")

    async def _cmd_pause(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update, {"ADMIN_OP"}):
            return
        if not ctx.args:
            await self._reply(update, "Usage: `/pause <CODE>`")
            return
        self.agent_pause(ctx.args[0].upper())
        await self._reply(update, f"Paused {ctx.args[0].upper()}")

    async def _cmd_resume(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update, {"ADMIN_OP"}):
            return
        if not ctx.args:
            await self._reply(update, "Usage: `/resume <CODE>`")
            return
        self.agent_resume(ctx.args[0].upper())
        await self._reply(update, f"Resumed {ctx.args[0].upper()}")

    async def _cmd_rotate(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update, {"ADMIN_OP"}):
            return
        if not ctx.args:
            await self._reply(update, "Usage: `/rotate <CODE>`")
            return
        code = ctx.args[0].upper()
        new_id = await self.instances.rotate(code)
        await self._reply(update, f"{code} rotated → {new_id}")

    async def _cmd_run(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Queue an agent for a normal inbox pass on the next tick (Spec §11,
        replaces /retry). Uses flag_for_execution WITHOUT a cycle_id (D3)."""
        if not await self._authorize(update, {"ADMIN_OP"}):
            return
        if not ctx.args:
            await self._reply(update, "Usage: `/run <CODE>`")
            return
        code = ctx.args[0].upper()
        flag_for_execution(self.state_dir, code)
        await self._reply(update, f"▶️ Queued {code} for execution.")

    async def _cmd_model(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update, {"ADMIN_OP"}):
            return
        if len(ctx.args) < 2:
            await self._reply(update, "Usage: `/model <CODE> <model-string>`")
            return
        code = ctx.args[0].upper()
        model = ctx.args[1]
        await self.models.set(code, model)
        await self._reply(update, f"{code} → {model}")

    async def _cmd_models(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        assignments = self.models.all()
        lines = ["*Models*"]
        for code in self.config.AGENTS:
            lines.append(f"• {code}: {assignments.get(code, '—')}")
        await self._reply(update, "\n".join(lines))

    # ─── DE / EXT direct roles (Spec v5 §12.4-§12.6) ─────────────────────────
    # /build and /expert OP-relay commands are REMOVED. DE and EXT are first-class
    # roles that talk to SG / BR directly through their own role-scoped channels.

    async def _emit_de_in(self, content: str, references=None) -> str:
        """Mint DE_IN-DE-NNN and route to SG (archive + routing_log). Shared by
        the DE Telegram handlers and testable directly."""
        artifact_id = await self.sequences.next_id("DE", "DE_IN")
        artifact = Artifact(type="DE_IN", sender="DE", content=content,
                            recipient="SG", id=artifact_id, references=references or [])
        self.cycles.check_cycle_events(artifact, recipients=["SG"])
        await self.router.route(artifact)
        return artifact_id

    async def _emit_brq(self, content: str) -> str:
        """Mint BRQ-EXT-NNN and route to BR (archive + routing_log)."""
        artifact_id = await self.sequences.next_id("EXT", "BRQ")
        artifact = Artifact(type="BRQ", sender="EXT", content=content,
                            recipient="BR", id=artifact_id)
        self.cycles.check_cycle_events(artifact, recipients=["BR"])
        await self.router.route(artifact)
        return artifact_id

    async def _cmd_de_respond(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update, {"DE"}):
            return
        if len(ctx.args) < 2:
            await self._reply(update, "Usage: `/de_respond <DE_OUT-ID> <response>`")
            return
        ref, response = ctx.args[0], " ".join(ctx.args[1:]).strip()
        de_id = await self._emit_de_in(response, references=[ref])
        await self._reply(update, f"✅ {de_id} sent to SG.")

    async def _cmd_de_observe(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update, {"DE"}):
            return
        msg = " ".join(ctx.args).strip()
        if not msg:
            await self._reply(update, "Usage: `/de_observe <observation>`")
            return
        de_id = await self._emit_de_in(msg)
        await self._reply(update, f"📝 Observation {de_id} sent to SG.")

    async def _cmd_de_pending(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update, {"DE"}):
            return
        cycles = self.cycles.get_pending_for_role("DE")
        await self._reply(update, "*DE pending*\n" +
                          ("\n".join(f"• {c.id}" for c in cycles) or "none"))

    async def _cmd_de_history(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update, {"DE"}):
            return
        hist = self.cycles.get_history_for_role("DE")
        await self._reply(update, "*DE history*\n" +
                          ("\n".join(f"• {c.get('id')}" for c in hist) or "none"))

    async def _cmd_ext_submit(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update, {"EXT"}):
            return
        msg = " ".join(ctx.args).strip()
        if not msg:
            await self._reply(update, "Usage: `/ext_submit <results>`")
            return
        brq_id = await self._emit_brq(msg)
        await self._reply(update, f"✅ {brq_id} submitted to BR.")

    async def _cmd_ext_ask(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update, {"EXT"}):
            return
        msg = " ".join(ctx.args).strip()
        if not msg:
            await self._reply(update, "Usage: `/ext_ask <question>`")
            return
        brq_id = await self._emit_brq(msg)
        await self._reply(update, f"❓ {brq_id} sent to BR.")

    async def _cmd_ext_pending(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update, {"EXT"}):
            return
        cycles = self.cycles.get_pending_for_role("EXT")
        await self._reply(update, "*EXT pending*\n" +
                          ("\n".join(f"• {c.id}" for c in cycles) or "none"))

    async def _cmd_ext_history(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update, {"EXT"}):
            return
        hist = self.cycles.get_history_for_role("EXT")
        await self._reply(update, "*EXT history*\n" +
                          ("\n".join(f"• {c.get('id')}" for c in hist) or "none"))

    # ─── Admin OP role management (Spec v5 §10.2 + §12.4) ─────────────────────

    async def _cmd_role(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """`/role assign|modify|revoke ...` — 2FA-gated (Spec §13.4)."""
        if not await self._authorize(update, {"ADMIN_OP"}):
            return
        if self.role_manager is None or not ctx.args:
            await self._reply(update,
                "Usage: `/role assign <name> <telegram_id> <role> [project]` | "
                "`/role revoke <role> [name]` | `/role modify <role> <key=value>...`")
            return
        sub = ctx.args[0].lower()
        chat_id = update.effective_chat.id
        if sub == "assign" and len(ctx.args) >= 4:
            name, tg, role = ctx.args[1], ctx.args[2], ctx.args[3].upper()
            project = " ".join(ctx.args[4:]).strip()
            otp = self.role_manager.initiate_assign(chat_id, name, tg, role, project)
            await self._reply(update,
                f"⚠️ Assign {role} to {name}. Reply `APPROVE` to confirm here, "
                f"or use code {otp} via MCP. Expires in 5 minutes.")
        elif sub == "revoke" and len(ctx.args) >= 2:
            role = ctx.args[1].upper()
            otp = self.role_manager.initiate_revoke(chat_id, role,
                                                    " ".join(ctx.args[2:]).strip())
            await self._reply(update,
                f"⚠️ Revoke {role}. Reply `APPROVE` or use code {otp}.")
        elif sub == "modify" and len(ctx.args) >= 3:
            role = ctx.args[1].upper()
            changes = {}
            for tok in ctx.args[2:]:
                if "=" in tok:
                    k, _, v = tok.partition("=")
                    changes[k.strip()] = v.strip()
            otp = self.role_manager.initiate_modify(chat_id, role, changes)
            await self._reply(update,
                f"⚠️ Modify {role}: {changes}. Reply `APPROVE` or use code {otp}.")
        else:
            await self._reply(update, "Usage: `/role assign|modify|revoke ...`")

    async def _cmd_roles(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update, {"ADMIN_OP"}):
            return
        if self.role_manager is None:
            await self._reply(update, "Role manager not configured.")
            return
        summary = self.role_manager.roles_summary()
        lines = ["*Role assignments*"]
        for role, data in summary.items():
            lines.append(f"• {role}: {data.get('name', '?')} "
                         f"(tg {data.get('telegram_id', '?')})")
        await self._reply(update, "\n".join(lines) if summary else "No roles assigned.")

    async def _cmd_config_notify(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update, {"ADMIN_OP"}):
            return
        if len(ctx.args) < 3:
            await self._reply(update, "Usage: `/config_notify <role> <channel> <mode>`")
            return
        role, channel, mode = ctx.args[0], ctx.args[1], ctx.args[2]
        path = Path(getattr(self.config, "NOTIFICATIONS_FILE",
                            self.state_dir / "notifications.json"))
        prefs = load_json(path, default={})
        prefs.setdefault(role, {})[channel] = mode
        from state_manager import atomic_write
        atomic_write(path, json.dumps(prefs, indent=2))
        await self._reply(update, f"🔔 {role}.{channel} → {mode}")

    async def _cmd_resolve(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Spec v5 §6.1 + §10.1 + §12.4 — Admin OP closes an active GOV exchange.

        Usage: /resolve <GOV-ID> [action-description]

        Records the resolution in the Admin backlog (moves GOV from pending →
        resolved with the action note) and closes the gov_exchange cycle so the
        conversation history stops growing.
        """
        # Governance closure is Admin OP's (Spec v5 §12.4).
        if not await self._authorize(update, {"ADMIN_OP"}):
            return
        if not ctx.args:
            await self._reply(update, "Usage: `/resolve <GOV-ID> [action]`")
            return
        gov_id = ctx.args[0]
        action = " ".join(ctx.args[1:]).strip() or "acknowledged"
        # GOV lives in the Admin backlog now (Spec v5 §10.1); fall back to OP
        # backlog for older single-queue deployments.
        backlog = self.admin_backlog or self.op_backlog
        if backlog.find(gov_id) is None:
            await self._reply(update, f"No backlog item for {gov_id}")
            return
        # Move GOV to resolved + append the action note (Spec v5 §10.1 / P1-1).
        backlog.resolve(gov_id, resolution=action)
        # Close the gov_exchange cycle opened on this GOV (Spec v5 §6.1).
        self.cycles.close_by_artifact(gov_id)
        await self._reply(
            update,
            f"✅ {gov_id} resolved ({action}). gov_exchange cycle closed."
        )

    async def _cmd_request(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Spec v5 §12.2 + §2.1 S13 — OP submits ad-hoc scope work to SG as
        REQ-OP-NNN. Routes through router for archive + routing_log."""
        if not await self._authorize(update, {"OP"}):
            return
        msg = " ".join(ctx.args).strip()
        if not msg:
            await self._reply(update,
                "Usage: `/request <ad-hoc scope work / change request / question>`")
            return
        if self.router is None:
            await self._reply(update, "Router not wired — cannot route REQ.")
            return
        from models import Artifact as A
        artifact_id = await self.sequences.next_id("OP", "REQ")
        artifact = A(type="REQ", sender="OP", content=msg, recipient="SG",
                     id=artifact_id)
        await self.router.route(artifact)
        await self._reply(update, f"📨 {artifact_id} submitted to SG.")

    async def _cmd_routing(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        from router import ROUTING_TABLE
        if not ctx.args:
            await self._reply(update, "Usage: `/routing <TYPE>`")
            return
        doc_type = ctx.args[0].upper()
        lines = [f"*Routing for type {doc_type}*"]
        for key, recipients in ROUTING_TABLE.items():
            if doc_type in key:
                lines.append(f"• {key} → {[r['to'] for r in recipients]}")
        await self._reply(update, "\n".join(lines))

    async def _cmd_decisions(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Spec §11. Honor the prefix arg: D=SG, TD=TG, GD=SYS. No arg = all three."""
        prefix = (ctx.args[0] if ctx.args else "").upper().rstrip("-")
        prefix_to_agent = {"D": "SG", "TD": "TG", "GD": "SYS"}
        sequences = load_json(self.state_dir / "sequences.json", default={})

        if prefix:
            if prefix not in prefix_to_agent:
                await self._reply(update,
                    "Usage: `/decisions [D|TD|GD]` (or no arg for all).")
                return
            agent = prefix_to_agent[prefix]
            count = sequences.get(f"{agent}:DECISION", 0)
            await self._reply(update,
                f"*{prefix}- decisions* ({agent}): next is `{prefix}-{count+1:03d}` "
                f"(last issued: `{prefix}-{count:03d}`)" if count > 0 else
                f"*{prefix}- decisions* ({agent}): no decisions issued yet"
            )
            return

        lines = ["*Decision counters*"]
        for p, agent in prefix_to_agent.items():
            count = sequences.get(f"{agent}:DECISION", 0)
            last = f"{p}-{count:03d}" if count > 0 else "(none)"
            lines.append(f"• {p}- ({agent}): last {last}, next {p}-{count+1:03d}")
        await self._reply(update, "\n".join(lines))

    async def _cmd_framework(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Spec §11. Extract and quote a section from the Framework document.

        `/framework 5.1` → returns the body of "## 5.1 ..." (or "### 5.1 ...").
        `/framework D-ARCH-020` → returns the decision row from §10.
        No arg → returns the table of contents.
        """
        section = " ".join(ctx.args).strip()
        framework_dir = Path(self.config.FRAMEWORK_DIR)
        # Prefer v6, then v5, then v4 (Spec v5 §14 / audit #12).
        framework_path = None
        for name in ("VEGA_Architecture_Framework_v6.md",
                     "VEGA_Architecture_Framework_v5.md",
                     "VEGA_Architecture_Framework_v4.md"):
            candidate = framework_dir / name
            if candidate.exists():
                framework_path = candidate
                break
        if framework_path is None:
            await self._reply(update,
                f"Framework not found in `{framework_dir}` (looked for v6/v5/v4).")
            return
        text = framework_path.read_text()

        if not section:
            # Return TOC — between "## Table of Contents" and the next "##".
            m = re.search(r"(?ms)^##\s+Table of Contents\s*\n(.*?)(?=^##\s)", text)
            toc = m.group(1).strip() if m else "(TOC not found)"
            await self._reply(update, f"*Framework Table of Contents*\n```\n{toc}\n```")
            return

        # Try exact section heading match (## 5.1, ### 5.1, ## §5.1)
        body = _extract_section(text, section)
        if body:
            await self._reply(update, f"*Framework §{section}*\n\n{body[:3500]}")
        else:
            await self._reply(update,
                f"Section `{section}` not found in framework. "
                f"Try a heading like `5.1`, `6`, or `D-ARCH-020`.")

    async def _cmd_cycles(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        active = list(self.cycles.active_dir.glob("*.json"))
        if not active:
            await self._reply(update, "No active cycles.")
            return
        lines = ["*Active cycles*"]
        for path in active:
            lines.append(f"• {path.stem}")
        await self._reply(update, "\n".join(lines))

    # ─── Free-text → role-based freeform (Spec v5 §10.2) ─────────────────────

    async def _on_text(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Role-dispatched freeform text (Spec v5 §10.2):
          OP        → cycle-internal PROP exchange with SG
          ADMIN_OP  → 2FA `APPROVE`, else cycle-internal GOV exchange with SYS
          DE/EXT    → hint (they use their slash commands)
          unknown   → access-not-configured
        """
        role = self._role_for_update(update)
        if role is None:
            await self._reply(update,
                "Access not configured. Use MCP to request access via "
                "`vega_request_access()`.")
            return
        text = update.message.text or ""
        chat_id = update.effective_chat.id
        is_approve = text.strip().upper() == "APPROVE"
        is_reject = text.strip().upper() == "REJECT"
        rm = self.role_manager

        # "REJECT" blocks a pending dual-2FA restore (§7.5 — "either can block").
        # Works for the Admin OP (phase 1) and the OP consent step (phase 2).
        if is_reject and rm is not None and rm.has_pending_restore(chat_id):
            rm.cancel_restore(chat_id)
            rm._log_event("restore_rejected", actor_telegram_id=str(chat_id),
                          result="blocked")
            await self._reply(update, "🛑 Restore blocked. No scope was changed.")
            return

        # Admin OP: "APPROVE" confirms a pending action. Restore (SC-7 §7.5)
        # takes precedence over a role action — both can't be pending at once,
        # but check restore first so the dual-2FA flow is unambiguous.
        if role == "ADMIN_OP" and is_approve:
            if rm is not None and rm.has_pending_restore(chat_id):
                await self._confirm_restore_admin(update, chat_id)
            elif rm is not None and rm.has_pending_action(chat_id):
                ok = await rm.confirm_pending(chat_id)
                await self._reply(update,
                    "✅ Role action confirmed." if ok else "⛔ No valid pending action.")
            else:
                await self._reply(update, "No pending action to approve.")
            return

        # OP: "APPROVE" gives scope-authority consent for a pending restore.
        if role == "OP" and is_approve and rm is not None and rm.has_pending_restore(chat_id):
            await self._confirm_restore_op(update, chat_id)
            return

        # #10 (Spec §3.1) — freeform OP↔SG / Admin-OP↔SYS exchange is off by
        # default; it happens over MCP vega_exchange. Quick commands (/approve,
        # /reject, /modify, /request, /resolve) and the 2FA APPROVE flows above
        # always work regardless of this gate.
        if not getattr(self.config, "TELEGRAM_EXCHANGE_ENABLED", False):
            await self._reply(update,
                "Freeform exchange is available via MCP only. Use `vega_exchange` "
                "in your Claude session. Quick commands (/approve, /reject, "
                "/modify, /request) still work here.")
            return

        if role == "ADMIN_OP":
            await self._forward_exchange(update, text, backlog=self.admin_backlog,
                                         partner="SYS", role="ADMIN_OP")
            return
        if role == "OP":
            await self._forward_exchange(update, text, backlog=self.op_backlog,
                                         partner="SG", role="OP")
            return
        # DE / EXT use structured commands, not freeform.
        await self._reply(update,
            "Use your role commands (e.g. /de_respond, /ext_submit). /help for the list.")

    async def _forward_exchange(self, update: Update, text: str, backlog,
                                partner: str, role: str) -> None:
        """Cycle-internal exchange turn (Spec §6.4 / audit P1-2): append to the
        active cycle and flag the partner agent — no artifact, nothing routed.
        Used for OP↔SG (PROP) and Admin OP↔SYS (GOV) dialogues."""
        if backlog is None:
            await self._reply(update, "No backlog configured for this exchange.")
            return
        current = backlog.get_in_progress()
        if not current:
            pending = backlog.list_pending()
            if pending:
                current = pending[0]
                backlog.start_exchange(current.id)
        if not current:
            await self._reply(update,
                "No active exchange. Use /backlog to see the queue, /help for commands.")
            return
        cycle = self.cycles.get_active_cycle(partner, current)
        if not cycle:
            await self._reply(update,
                f"No active cycle for {current.id} — cannot forward freeform text.")
            return
        self.cycles.append_turn(cycle, text, role)
        flag_for_execution(self.state_dir, partner, cycle.id)
        await self._reply(update, f"↩️ Forwarded to {partner}.")

    # ─── Scope read (SC-3 §12.3) ─────────────────────────────────────────────

    async def _cmd_scope(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """List scope docs, or `/scope <doc>` for one doc's content. All roles."""
        if not await self._authorize(update, {"OP", "ADMIN_OP", "DE", "EXT"}):
            return
        from pathlib import Path as _P
        import re
        scope_dir = _P(self.config.SCOPE_DIR)
        if not scope_dir.exists():
            await self._reply(update, "No scope directory configured.")
            return
        if ctx.args:
            doc = ctx.args[0]
            hits = [p for p in scope_dir.glob("*.md")
                    if p.name == doc or p.stem == doc or p.name == f"{doc}.md"]
            if not hits:
                await self._reply(update, f"No scope doc matching `{doc}`.")
                return
            await self._reply(update, hits[0].read_text())
            return
        ver_re = re.compile(r"_v(\d+(?:[._]\d+)*)\.md$")
        lines = ["📚 *Scope docs:*"]
        for p in sorted(scope_dir.glob("*.md")):
            m = ver_re.search(p.name)
            ver = ("v" + m.group(1).replace("_", ".")) if m else "—"
            lines.append(f"• `{p.name}` ({ver}, {p.stat().st_size} B)")
        await self._reply(update, "\n".join(lines))

    # ─── Validated-state snapshots (SC-7 §7.5) ───────────────────────────────

    async def _cmd_snapshots(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update, {"OP", "ADMIN_OP"}):
            return
        from snapshot_manager import list_snapshots
        snaps = list_snapshots(self.config)
        if not snaps:
            await self._reply(update, "No snapshots yet. One is written on each AUTH approve.")
            return
        lines = [f"📸 *Snapshots* ({len(snaps)}):"]
        for s in snaps[:15]:
            lines.append(f"• `{s['snapshot_id']}` — {s.get('timestamp','?')} "
                         f"(trigger {s.get('trigger','?')})")
        await self._reply(update, "\n".join(lines))

    async def _cmd_verify(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update, {"OP", "ADMIN_OP"}):
            return
        from snapshot_manager import verify_scope
        snap_id = ctx.args[0] if ctx.args else ""
        result = verify_scope(self.config, snap_id)
        if "error" in result:
            await self._reply(update, f"⚠️ {result['error']}")
            return
        mark = "✅ match" if result["match"] else "⚠️ drift detected"
        lines = [f"🔍 Verify vs `{result['snapshot_id']}`: {mark}"]
        for name, status in result["docs"].items():
            if status != "match":
                lines.append(f"• {name}: *{status}*")
        await self._reply(update, "\n".join(lines))

    async def _cmd_restore(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin OP initiates dual-2FA scope restoration (§7.5 phase 1)."""
        if not await self._authorize(update, {"ADMIN_OP"}):
            return
        if not ctx.args:
            await self._reply(update, "Usage: `/restore <SNAPSHOT-ID>`")
            return
        if self.role_manager is None:
            await self._reply(update, "Role manager unavailable — cannot run dual-2FA restore.")
            return
        chat_id = update.effective_chat.id
        snap_id = ctx.args[0]
        otp = self.role_manager.initiate_restore(chat_id, snap_id)
        await self._reply(update,
            f"⚠️ Restore to `{snap_id}` — technical-authority 2FA code *{otp}*.\n"
            f"Reply `APPROVE` (or enter the code) to confirm; OP scope-authority "
            f"consent will then be requested. Reply `REJECT` to abort.")

    async def _confirm_restore_admin(self, update: Update, chat_id) -> None:
        """Phase 1 confirmed → request OP scope-authority consent (phase 2)."""
        result = self.role_manager.confirm_restore_admin(chat_id)
        if result is None:
            await self._reply(update, "⛔ No valid pending restore (or no OP on record to consent).")
            return
        op_telegram_id, op_otp = result
        await self.send(
            f"⚠️ Admin OP requests scope restoration. Scope-authority 2FA code *{op_otp}*.\n"
            f"Reply `APPROVE` (or enter the code) to consent — this replaces the live "
            f"scope — or `REJECT` to block it.",
            chat_id=op_telegram_id)
        await self._reply(update,
            "✅ Technical authority confirmed. OP scope-authority consent requested — "
            "restore executes once the OP approves.")

    async def _confirm_restore_op(self, update: Update, chat_id) -> None:
        """Phase 2 confirmed → execute restoration (§7.5)."""
        snapshot_id = self.role_manager.confirm_restore_op(chat_id)
        if snapshot_id is None:
            await self._reply(update, "⛔ No valid pending restore consent.")
            return
        from snapshot_manager import restore_scope

        def _pause_all() -> None:
            for code in self.config.AGENTS:
                self.agent_pause(code)

        await self._reply(update, f"⏳ Consent recorded. Restoring scope to `{snapshot_id}`…")
        ok = await restore_scope(snapshot_id, self.config,
                                 execute_sys=self.sys_trigger, telegram_bot=self,
                                 role_manager=self.role_manager, pause_all=_pause_all)
        if not ok:
            await self._reply(update, f"⛔ Restore failed — snapshot `{snapshot_id}` not found.")

    # ─── Thinking lookup ─────────────────────────────────────────────────────

    def _latest_thinking(self, artifact: Artifact) -> str:
        """Look up the thinking snippet for the execution that produced this
        artifact. Uses artifact_index.json to find the execution_id, then
        scans the execution_log in REVERSE so the most-recently-produced
        artifacts (the common case for notifications) are O(1) instead of
        O(N) over the full log."""
        if not artifact.id:
            return ""
        index = load_json(self.state_dir / "artifact_index.json", default={})
        record = index.get(artifact.id)
        if not record:
            return ""
        target = record.get("execution_id")
        if not target:
            return ""
        log = load_json(self.state_dir / "execution_log.json", default=[])
        if not isinstance(log, list):
            return ""
        for e in reversed(log):
            if e.get("execution_id") == target:
                return _thinking_snippet(e.get("thinking_blocks", []))
        return ""


# Framework section extraction lives in framework_parser (shared with main.py).
from framework_parser import extract_section as _extract_section   # noqa: E402,F401


# ─── Reference text ──────────────────────────────────────────────────────────

AGENT_DESCRIPTIONS = {
    "SG":  "Scope Guardian — analytical recommendation, scope integrity",
    "SA":  "Scope Auditor — independent scope verification",
    "SE":  "Scope Editor — applies SCNs to scope documents",
    "TG":  "Tests Guardian — derives test model from scope, triages failures",
    "TA":  "Tests Auditor — independent test model verification, finds blind spots",
    "TE":  "Tests Editor — applies TCNs, generates build version",
    "BR":  "Build Rep — interface to External Build",
    "BTA": "Build Test Auditor — validates build results against full test model",
    "SYS": "System Auditor — governance quality, cross-agent knowledge propagation",
}

_READ_HELP = """*Monitoring*
`/status` — system overview
`/backlog` — pending decision items
`/agent <CODE>` / `/agents` — agent details / list
`/history <ID>` — routing history for an artifact
`/cycles [ID]` — active cycles, or one cycle's messages
`/scope [doc]` — scope docs, or one doc's content
`/wiki <CODE>` · `/log <CODE> [N]` · `/thinking <ID>`
`/snapshots` · `/verify [SNAP-ID]` — validated-state snapshots

*Reference*
`/routing <TYPE>` · `/decisions [prefix]` · `/framework <§>`"""

_OP_HELP = """*VEGA — OP (scope decisions)*

*Scope decisions*
`/approve <ID>` / `/reject <ID> <reason>` / `/modify <ID> <instructions>`
`/request <msg>` — ad-hoc scope request to SG (REQ-OP-NNN)
`/pending` — active PROP exchanges

*Cross-channel (read-only)*
`/de_activity` · `/ext_activity`

""" + _READ_HELP + """

Freeform SG exchange is via MCP `vega_exchange` (unless TELEGRAM_EXCHANGE_ENABLED)."""

_ADMIN_OP_HELP = """*VEGA — Admin OP (governance + administration)*

*Governance*
`/sys [instruction]` — trigger SYS audit
`/resolve <GOV-ID> [action]` — close active GOV exchange with SYS

*Role management* (2FA — reply `APPROVE` to confirm)
`/role assign <name> <tg> <role> [project]`
`/role modify <role> <key=value>...` · `/role revoke <role> [name]`
`/roles` — list assignments
`/config_notify <role> <channel> <mode>`

*Agent control*
`/run <CODE>` — run an agent now · `/pause <CODE>` / `/resume <CODE>` · `/rotate <CODE>`
`/model <CODE> <model>` / `/models`

*Scope restore* (dual 2FA — reply `APPROVE`)
`/restore <SNAP-ID>` — restore scope to a validated snapshot

""" + _READ_HELP + """

Freeform SYS exchange is via MCP `vega_exchange` (unless TELEGRAM_EXCHANGE_ENABLED)."""

_DE_HELP = """*VEGA — Domain Expert*

`/de_respond <DE_OUT-ID> <response>` — answer SG's question
`/de_observe <observation>` — initiate a domain observation to SG
`/de_pending` — outstanding DE_OUT awaiting your response
`/de_history` — your DE↔SG exchange history"""

_EXT_HELP = """*VEGA — External Build*

`/ext_submit <results>` — submit build/test results to BR
`/ext_ask <question>` — ask BR a question
`/ext_pending` — outstanding items awaiting your response
`/ext_history` — your BR↔EXT exchange history"""

_HELP_BY_ROLE = {
    "OP": _OP_HELP, "ADMIN_OP": _ADMIN_OP_HELP,
    "DE": _DE_HELP, "EXT": _EXT_HELP,
}

# Back-compat: a plain module-level help string (the OP view) for any caller or
# test that imports _HELP_TEXT directly.
_HELP_TEXT = _OP_HELP
