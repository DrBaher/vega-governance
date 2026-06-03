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
from op_backlog import OPBacklog, make_auth
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
        agent_retry: Callable[[str], Awaitable[Any]],
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
        self.agent_retry = agent_retry
        self.agent_pause = agent_pause
        self.agent_resume = agent_resume
        self.state_dir = Path(state_dir)

        # Set after construction (avoid circular import)
        self.router: Optional["Router"] = None

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

    async def send(self, text: str) -> None:
        """Send a message to OP. Falls back to plain text if Markdown parsing
        fails on the artifact body (unescaped `_`, `*`, `[`, etc.). Truncates
        anything over Telegram's 4096-char message limit, splitting at a
        newline if possible."""
        if not self.app or not self.chat_id:
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
                chat_id=self.chat_id, text=text, parse_mode=ParseMode.MARKDOWN,
            )
        except BadRequest as e:
            msg = str(e).lower()
            if "parse" in msg or "entities" in msg or "markdown" in msg:
                # Artifact content has chars that broke Markdown (unescaped _, *,
                # [, etc.). Resend as plain text so the message reaches OP.
                await self.app.bot.send_message(
                    chat_id=self.chat_id, text=text,
                    # parse_mode omitted → plain text
                )
            else:
                raise

    # ─── Notifications ───────────────────────────────────────────────────────

    async def notify(self, artifact: Artifact) -> None:
        emoji = PRIORITY_EMOJI.get(artifact.priority or "P2", "🟡")
        thinking = self._latest_thinking(artifact)
        thinking_section = f"\n💭 _{artifact.sender} reasoning:_ {thinking}\n" if thinking else ""
        msg = (
            f"{emoji} *{artifact.id}* ({artifact.priority or 'P2'})\n"
            f"From: {artifact.sender} ({artifact.sender_model or '?'})\n\n"
            f"{_summary(artifact)}\n"
            f"{thinking_section}\n"
            f"Reply to discuss, or:\n"
            f"`/approve {artifact.id}`\n"
            f"`/reject {artifact.id} <reason>`\n"
            f"`/modify {artifact.id} <instructions>`"
        )
        await self.send(msg)

    async def notify_external_relay(self, artifact: Artifact, target: str) -> None:
        """For EXT / DE relays. At launch: OP relays manually."""
        msg = (
            f"📤 *Relay needed → {target}*\n"
            f"*{artifact.id}* ({artifact.type})\n\n"
            f"{_summary(artifact)}\n\n"
            f"_When the {target} responds, forward via_ "
            f"`/build <msg>` _or_ `/expert <msg>`."
        )
        await self.send(msg)

    async def relay_exchange_reply(self, agent_code: str, cycle_id: str,
                                   text: str) -> None:
        """Relay a partner agent's cycle-internal exchange reply to OP (Spec §6.4).

        The reply is a conversational turn (no artifact), so it doesn't flow
        through notify(); we surface it directly. If the same turn also produced
        a formal artifact (e.g. SG closes the exchange with an AUTH), that rides
        the normal routing + notification path on the next tick."""
        msg = (
            f"💬 *{agent_code} response* (cycle {cycle_id})\n\n"
            f"{text}\n\n"
            f"Reply to continue, or `/approve` / `/reject` / `/modify`."
        )
        await self.send(msg)

    async def handle_sg_exchange_response(self, artifact: Artifact) -> None:
        """During PROP exchange: SG response should round-trip to OP via Telegram."""
        thinking = self._latest_thinking(artifact)
        thinking_section = f"\n💭 _SG thinking:_ {thinking}\n" if thinking else ""
        msg = (
            f"💬 *SG response* (re: {artifact.references[0] if artifact.references else '?'})\n\n"
            f"{_summary(artifact)}\n"
            f"{thinking_section}\n"
            f"Reply to continue, or `/approve` / `/reject` / `/modify`."
        )
        await self.send(msg)

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
        app.add_handler(CommandHandler("retry", self._cmd_retry))
        app.add_handler(CommandHandler("model", self._cmd_model))
        app.add_handler(CommandHandler("models", self._cmd_models))
        app.add_handler(CommandHandler("build", self._cmd_build_relay))
        app.add_handler(CommandHandler("expert", self._cmd_expert_relay))
        app.add_handler(CommandHandler("request", self._cmd_request))
        app.add_handler(CommandHandler("resolve", self._cmd_resolve))
        app.add_handler(CommandHandler("routing", self._cmd_routing))
        app.add_handler(CommandHandler("decisions", self._cmd_decisions))
        app.add_handler(CommandHandler("framework", self._cmd_framework))
        app.add_handler(CommandHandler("cycles", self._cmd_cycles))
        # Fall-through: non-slash messages forward to active PROP exchange
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text))

    async def _reply(self, update: Update, text: str) -> None:
        await update.effective_message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def _cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await self._reply(update, _HELP_TEXT)

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

        emoji_label = {"approve": "✅", "reject": "❌", "modify": "✏️"}[disposition]
        return (
            f"{emoji_label} {auth.id} issued for {prop_id}. "
            f"Archived and routed to SG. SG will write SUM-SG-NNN next execution."
        )

    async def _cmd_sys(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        instruction = " ".join(ctx.args).strip() or None
        await self._reply(update, "Triggering SYS audit…")
        result = await self.sys_trigger(instruction)
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
        path = Path(self.config.AGENTS_DIR) / code / "wiki" / "log.md"
        if not path.exists():
            await self._reply(update, f"No log for {code}")
            return
        # WikiManager.append_log writes entries starting with
        # `## [YYYY-MM-DD HH:MM]` (line-start, bracketed timestamp). Split on
        # that anchor instead of the bare `\n## ` substring — log content can
        # contain `## ` naturally (e.g., a WIKI_REPLACE entry that quotes a
        # new section heading like `## SectionTitle` would mis-split).
        text = path.read_text()
        log_entry_re = re.compile(r"(?m)^## \[\d{4}-\d{2}-\d{2}")
        matches = list(log_entry_re.finditer(text))
        if not matches:
            await self._reply(update, text[-3500:] or "(empty log)")
            return
        offsets = [m.start() for m in matches] + [len(text)]
        entries = [text[offsets[i]:offsets[i + 1]] for i in range(len(matches))]
        recent = entries[-n:]
        await self._reply(update, "".join(recent)[-3500:])

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
        if not ctx.args:
            await self._reply(update, "Usage: `/pause <CODE>`")
            return
        self.agent_pause(ctx.args[0].upper())
        await self._reply(update, f"Paused {ctx.args[0].upper()}")

    async def _cmd_resume(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not ctx.args:
            await self._reply(update, "Usage: `/resume <CODE>`")
            return
        self.agent_resume(ctx.args[0].upper())
        await self._reply(update, f"Resumed {ctx.args[0].upper()}")

    async def _cmd_rotate(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not ctx.args:
            await self._reply(update, "Usage: `/rotate <CODE>`")
            return
        code = ctx.args[0].upper()
        new_id = await self.instances.rotate(code)
        await self._reply(update, f"{code} rotated → {new_id}")

    async def _cmd_retry(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not ctx.args:
            await self._reply(update, "Usage: `/retry <CODE>`")
            return
        await self.agent_retry(ctx.args[0].upper())
        await self._reply(update, "Retry queued.")

    async def _cmd_model(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
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

    async def _cmd_build_relay(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = " ".join(ctx.args).strip()
        if not msg:
            await self._reply(update, "Usage: `/build <msg>`")
            return
        if self.router is None:
            await self._reply(update, "Router not wired — cannot relay.")
            return
        from models import Artifact as A  # local
        # Spec §9 — proper sequence id for EXT BRQ artifacts.
        artifact_id = await self.sequences.next_id("EXT", "BRQ")
        artifact = A(type="BRQ", sender="EXT", content=msg, recipient="BR",
                     id=artifact_id)
        # Spec §6.1 — BRQ from EXT opens the build_results cycle.
        self.cycles.check_cycle_events(artifact, recipients=["BR"])
        # Route: archives + routing_log + delivers to BR inbox.
        await self.router.route(artifact)
        await self._reply(update, f"📥 {artifact_id} relayed to BR.")

    async def _cmd_expert_relay(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Spec v4 §12.4 — OP relays Domain Expert response to SG via router.

        Was: direct inbox.deliver bypass that never reached the archive or
        routing_log, leaving DE_IN artifacts off the audit trail and using
        a non-spec id format (`DE-IN-<epoch>`). Now mints a proper
        DE_IN-DE-NNN id via SequenceManager and routes through router so the
        artifact appears in routing_log + archive like every other artifact.
        Also fires cycle-event detection so the DE Q&A cycle (Spec v4 §6.1)
        closes when DE_IN arrives.
        """
        msg = " ".join(ctx.args).strip()
        if not msg:
            await self._reply(update, "Usage: `/expert <msg>`")
            return
        if self.router is None:
            await self._reply(update, "Router not wired — cannot relay.")
            return
        from models import Artifact as A
        artifact_id = await self.sequences.next_id("DE", "DE_IN")
        artifact = A(type="DE_IN", sender="DE", content=msg, recipient="SG",
                     id=artifact_id)
        # DE Q&A cycle closes on DE_IN (Spec v4 §6.1).
        self.cycles.check_cycle_events(artifact, recipients=["SG"])
        await self.router.route(artifact)
        await self._reply(update, f"📥 {artifact_id} relayed to SG.")

    async def _cmd_resolve(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Spec v4 §6.1 + §12.2 — close an active GOV exchange.

        Usage: /resolve <GOV-ID> [action-description]

        Records the resolution in OP backlog (moves GOV from pending →
        resolved with the action note) and closes the gov_exchange cycle so
        the conversation history stops growing.
        """
        if not ctx.args:
            await self._reply(update, "Usage: `/resolve <GOV-ID> [action]`")
            return
        gov_id = ctx.args[0]
        action = " ".join(ctx.args[1:]).strip() or "acknowledged"
        path = self.op_backlog.find(gov_id)
        if not path:
            await self._reply(update, f"No backlog item for {gov_id}")
            return
        # Move GOV to resolved with the action note (op_backlog.resolve handles
        # pending→resolved + appends the resolution text per Spec v5 §10.1 / P1-1).
        self.op_backlog.resolve(gov_id, resolution=action)
        # Close the gov_exchange cycle opened on this GOV (Spec v4 §6.1).
        self.cycles.close_by_artifact(gov_id)
        await self._reply(
            update,
            f"✅ {gov_id} resolved ({action}). gov_exchange cycle closed."
        )

    async def _cmd_request(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Spec v4 §12.2 + §2.1 S13 — OP submits ad-hoc scope work to SG as
        REQ-OP-NNN. Routes through router for archive + routing_log."""
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
        # Prefer v5; fall back to v4 for older deployments.
        framework_path = framework_dir / "VEGA_Architecture_Framework_v5.md"
        if not framework_path.exists():
            framework_path = framework_dir / "VEGA_Architecture_Framework_v4.md"
        if not framework_path.exists():
            await self._reply(update,
                f"Framework not found in `{framework_dir}` (looked for v5/v4).")
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

    # ─── Free-text → SG exchange ─────────────────────────────────────────────

    async def _on_text(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Spec v5 §6.4 + §10.2 — multi-turn OP↔SG PROP exchange, cycle-internal.

        Free-text from OP during an active PROP exchange is NOT a standalone
        artifact (audit P1-2; Spec §6.4: "Exchange turns within a cycle are NOT
        standalone artifacts. They are turns in the cycle's messages array.").
        The turn is appended to the prop_exchange cycle and SG is flagged for
        execution — no PROP-OP-NNN is minted and nothing is routed. SG's reply
        rides back to OP via the main loop's exchange relay.
        """
        # Engage the in-progress exchange; first freeform message on a pending
        # item starts the exchange (Spec §10.2).
        current = self.op_backlog.get_in_progress()
        if not current:
            pending = self.op_backlog.list_pending()
            if pending:
                current = pending[0]
                self.op_backlog.start_exchange(current.id)
        if not current:
            await self._reply(update,
                "No active exchange. Use /pending to see queue, /help for commands."
            )
            return
        cycle = self.cycles.get_active_cycle("SG", current)
        if not cycle:
            await self._reply(update,
                f"No active cycle for {current.id} — cannot forward freeform text."
            )
            return
        text = update.message.text
        self.cycles.append_turn(cycle, text, "OP")
        flag_for_execution(self.state_dir, "SG", cycle.id)
        await self._reply(update, "↩️ Forwarded to SG.")

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

_HELP_TEXT = """*VEGA OP commands*

*Status*
`/status` — system overview
`/backlog` — pending PROP / GOV items
`/pending` — active PROP exchanges
`/agent <CODE>` — agent details
`/agents` — list all agents
`/history <ID>` — routing history for an artifact
`/cycles` — active conversation cycles

*Audit & governance*
`/sys [instruction]` — trigger SYS audit
`/wiki <CODE>` — wiki summary
`/log <CODE> [N]` — last N log entries
`/thinking <ID>` — thinking blocks for an artifact

*Agent control*
`/pause <CODE>` / `/resume <CODE>`
`/rotate <CODE>` — new instance ID
`/retry <CODE>` — re-execute
`/model <CODE> <model>` / `/models`

*Exchanges*
`/approve <ID>` / `/reject <ID> <reason>` / `/modify <ID> <instructions>`

*External*
`/build <msg>` — forward to BR
`/expert <msg>` — forward to SG (Domain Expert response)
`/request <msg>` — submit ad-hoc scope request to SG (REQ-OP-NNN)
`/resolve <GOV-ID> [action]` — close active GOV exchange with SYS

*Reference*
`/routing <TYPE>` — where a type routes
`/decisions [prefix]` — decision counters
`/framework <§>` — framework section reference

Type a plain message during an active PROP exchange to continue the dialogue with SG.
"""
