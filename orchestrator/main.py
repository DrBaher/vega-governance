"""
VEGA Orchestrator — main event loop and entry point.

Per Spec §13–§14.

Usage:
    python main.py                              # day-to-day
    python main.py --bootstrap path/to/init.md  # place initial input in SG inbox + run
    python main.py --sys [--audit-request "..."]  # one-shot SYS audit
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
from datetime import datetime, time, timezone
from pathlib import Path

from anthropic import AsyncAnthropic

import config

from artifact_store import ArtifactStore
from cortex_script import CortexScript
from cycle_manager import CycleManager
from executor import AgentExecutor
from framework_parser import compose_system_prompt
from models import Artifact, utcnow_iso
from backlog import Backlog
from role_manager import RoleManager
from router import Router
from sequence_manager import (
    InstanceManager, ModelAssignmentManager, SequenceManager,
)
from scheduling import is_daily_time, should_fire_sys
from state_manager import (
    LOCKS, atomic_save_json, atomic_write, drain_execution_flags, load_json,
)
from telegram_bot import TelegramBot
from wiki_manager import WikiManager


# ─── Globals managed by the loop ─────────────────────────────────────────────

def _cfg_path(config, attr: str, *default_parts: str) -> str:
    """Read a path from config, deriving a BASE_DIR-relative default when the
    attribute is absent — so deployments whose config.py predates the v6/v5
    role-access keys (admin_backlog, config/, role_events) still work."""
    value = getattr(config, attr, None)
    if value:
        return str(value)
    base = Path(getattr(config, "BASE_DIR", "."))
    return str(base.joinpath(*default_parts))


class Orchestrator:

    def __init__(self) -> None:
        self.cfg = config
        self.client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)

        self.sequences = SequenceManager(config.STATE_DIR)
        self.instances = InstanceManager(config.STATE_DIR)
        # Per Spec §9.3 fallback chain: per-agent override → defaults →
        # project-wide default (config.AGENT_MODEL) → hardcoded last resort.
        # Passing project_default keeps agents absent from
        # AGENT_MODEL_OVERRIDES on the project's chosen model instead of the
        # legacy hardcoded fallback.
        self.models = ModelAssignmentManager(
            config.STATE_DIR,
            defaults=config.AGENT_MODEL_OVERRIDES,
            project_default=getattr(config, "AGENT_MODEL", None),
        )
        self.store = ArtifactStore(config.AGENTS_DIR, config.ARTIFACTS_DIR)
        self.cycles = CycleManager(config.CYCLES_DIR, config.ARTIFACTS_DIR)
        self.wiki = WikiManager(
            agents_dir=config.AGENTS_DIR,
            universal_dir=config.UNIVERSAL_DIR,
            state_dir=config.STATE_DIR,
            instance_lookup=self.instances.get_or_create,
            model_lookup=self.models.get,
            replace_threshold_default=config.WIKI_REPLACE_THRESHOLD_DEFAULT,
            replace_thresholds=config.WIKI_REPLACE_THRESHOLDS,
        )
        # Spec v5 §10.1 — two decision queues: OP (PROP) and Admin OP (GOV).
        self.op_backlog = Backlog(config.OP_BACKLOG_DIR)
        self.admin_backlog = Backlog(_cfg_path(config, "ADMIN_BACKLOG_DIR", "admin_backlog"))
        # Spec v5 §10.4 — role manager (tokens, 2FA, invites, audit trail).
        self.role_manager = RoleManager(
            roles_file=_cfg_path(config, "ROLES_FILE", "config", "roles.json"),
            invites_dir=_cfg_path(config, "INVITES_DIR", "config", "invites"),
            events_file=_cfg_path(config, "ROLE_EVENTS_FILE", "state", "role_events.jsonl"),
            recovery_file=_cfg_path(config, "RECOVERY_FILE", "config", "recovery.hash"),
            invite_expiry=getattr(config, "INVITE_EXPIRY", 24 * 60 * 60),
            otp_expiry=getattr(config, "OTP_EXPIRY", 5 * 60),
        )
        # CORTEX placeholder — stub methods raise NotImplementedError. Never
        # invoked while CORTEX_ENABLED=False (the default). Activating CORTEX
        # requires filling in cortex_script.py per CORTEX spec §6–§17.
        self.cortex = CortexScript(config) if getattr(config, "CORTEX_ENABLED", False) else None
        self.executor = AgentExecutor(
            config=config,
            anthropic_client=self.client,
            store=self.store,
            wiki=self.wiki,
            cycles=self.cycles,
            sequences=self.sequences,
            instances=self.instances,
            models_mgr=self.models,
            agents_dir=config.AGENTS_DIR,
            scope_dir=config.SCOPE_DIR,
            universal_dir=config.UNIVERSAL_DIR,
            state_dir=config.STATE_DIR,
            cortex=self.cortex,
        )
        self.paused_agents: set[str] = set()
        self.bot = TelegramBot(
            config=config,
            op_backlog=self.op_backlog,
            store=self.store,
            cycles=self.cycles,
            wiki=self.wiki,
            instances=self.instances,
            models_mgr=self.models,
            sequences=self.sequences,   # Spec §7.2 — AUTH minted via SequenceManager
            sys_trigger=self.executor.execute_sys,
            agent_pause=self._pause_agent,
            agent_resume=self._resume_agent,
            state_dir=config.STATE_DIR,
        )
        # Wire role-based access into the bot (Spec v5 §10.2/§12.4) and let the
        # role manager push invites/OTPs to a person's Telegram via the bot.
        self.bot.role_manager = self.role_manager
        self.bot.admin_backlog = self.admin_backlog
        self.role_manager._notify = (
            lambda chat_id, text: self.bot.send(text, chat_id=chat_id)
        )
        self.router = Router(
            store=self.store,
            state_dir=config.STATE_DIR,
            op_backlog=self.op_backlog,
            admin_backlog=self.admin_backlog,   # Spec v5 §4.2 — GOV → Admin OP
            telegram_bot=self.bot,
            cycles=self.cycles,   # audit NEW-1 — stamp routing_log with cycle_id
        )
        self.bot.router = self.router

        self._execution_count = 0
        self._stopping = False

        # Spec §16.1 — pre-materialize all instance IDs at startup so subsequent
        # get_or_create calls are pure reads (no race on first-touch).
        for code in config.AGENTS:
            self.instances.get_or_create(code)

        # Spec §13 + §5.6 — track when SYS last ran. Used to de-dupe daily firing
        # (without this, SYS fires once per poll within the matching minute) AND
        # to filter the artifact / execution log inputs to SYS.
        self._sys_run_file = Path(config.STATE_DIR) / "sys_run.json"
        self._last_sys_run: datetime | None = self._load_sys_watermark()

    # ─── Lifecycle ───────────────────────────────────────────────────────────

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._request_stop)
            except (NotImplementedError, RuntimeError):
                pass

        # Start Telegram bot — yield control so the task can initialize
        # (otherwise the startup send below races against self.app being set,
        # and the greeting ends up shimmed to stdout instead of Telegram).
        bot_task = asyncio.create_task(self.bot.start_polling())
        # Give start_polling enough time to set self.app (Application.builder +
        # initialize + start). 2.5s is generous on typical networks.
        for _ in range(25):
            await asyncio.sleep(0.1)
            if self.bot.app is not None:
                break

        # MCP server (Spec v4 §12.1-12.2) — same-process HTTP endpoint that
        # exposes orchestrator tools to any Claude session. Disabled by default;
        # enable via config.MCP_ENABLED = True.
        mcp_server = None
        if getattr(config, "MCP_ENABLED", False):
            try:
                from mcp_server import MCPServer, MCPTools
                tools = MCPTools(
                    config=config,
                    op_backlog=self.op_backlog,
                    store=self.store,
                    cycles=self.cycles,
                    wiki=self.wiki,
                    instances=self.instances,
                    models_mgr=self.models,
                    sequences=self.sequences,
                    sys_trigger=self.executor.execute_sys,
                    agent_pause=self._pause_agent,
                    agent_resume=self._resume_agent,
                    process_disposition=self.bot.process_disposition,
                    state_dir=config.STATE_DIR,
                    router=self.router,
                    admin_backlog=self.admin_backlog,     # Spec v5 §10.1
                    role_manager=self.role_manager,       # Spec v5 §12.3
                    telegram_bot=self.bot,
                )
                mcp_server = MCPServer(config=config, tools=tools,
                                       role_manager=self.role_manager)
                await mcp_server.start()
            except Exception as e:
                print(f"[main] MCP server failed to start: "
                      f"{type(e).__name__}: {e}. Telegram-only mode.",
                      flush=True)
                mcp_server = None

        await self.bot.send(
            "🚀 *VEGA orchestrator started.*\n"
            f"Project: `{getattr(config, 'PROJECT_NAME', '?')}`\n"
            f"Agents: {', '.join(config.AGENTS)}\n"
            f"MCP: {'enabled' if mcp_server else 'off'}\n"
            "Type /help for commands."
        )

        try:
            while not self._stopping:
                try:
                    await self._tick()
                except Exception as e:
                    # Don't let a single bad tick (e.g., a malformed artifact, a
                    # network blip, a routing-table oversight) take down the whole
                    # orchestrator. Log loudly and continue. The user can /pause
                    # the offending agent if the same error repeats.
                    import traceback
                    err = f"{type(e).__name__}: {e}"
                    print(f"[main] ⚠ tick failed: {err}", flush=True)
                    traceback.print_exc()
                    try:
                        # Tick failures are operational/governance — Admin OP's
                        # domain (audit LOW-2), not the scope-decision OP's.
                        await self.bot.send(
                            f"⚠️ *Tick failed* — {err}. Orchestrator continues; "
                            f"check terminal logs for the traceback.",
                            role="ADMIN_OP"
                        )
                    except Exception:
                        pass
                await asyncio.sleep(config.POLL_INTERVAL)
        finally:
            bot_task.cancel()
            if mcp_server is not None:
                try:
                    await mcp_server.stop()
                except Exception:
                    pass

    def _request_stop(self) -> None:
        self._stopping = True
        print("\n[main] Stop requested — finishing current tick…")

    async def _tick(self) -> None:
        # 1. Route all agent outboxes
        for agent_code in config.AGENTS:
            for artifact in self.store.get_outbox(agent_code):
                self.cycles.check_cycle_events(
                    artifact, recipients=_recipients_for(artifact),
                )
                await self.router.route(artifact)
                # SG exchange round-trip notifications (PROP exchange continuation)
                await self._maybe_notify_sg_exchange(artifact)
                self.store.clear_from_outbox(agent_code, artifact)

        # Item #3 (Spec §5.1 + §8): wiki replace_section threshold now triggers SYS
        # INLINE (this tick) instead of being polled. Agents' results carry
        # `threshold_tripped`; we collect it and fire SYS once after the gather.
        threshold_tripped = False

        # 2. Execute agents with unprocessed inbox
        tasks = []
        task_agents = []   # parallel list — attribute gather results/exceptions
        for agent_code in config.AGENTS:
            if agent_code in self.paused_agents:
                continue
            if agent_code == "SYS":
                continue   # SYS runs on schedule / threshold / on-demand
            if self.store.inbox(agent_code).has_unprocessed():
                tasks.append(self.executor.execute(agent_code))
                task_agents.append(agent_code)
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, r in enumerate(results):
                # gather(return_exceptions=True) surfaces a raised exception as the
                # result item. Without this branch it would hit no handler — the
                # agent's inbox stays unprocessed with zero operator visibility.
                if isinstance(r, BaseException):
                    agent_code = task_agents[i]
                    print(f"[main] agent {agent_code} execution failed: "
                          f"{type(r).__name__}: {r}", flush=True)
                    await self.bot.send(
                        f"⚠️ Agent `{agent_code}` execution failed: "
                        f"{type(r).__name__}: {r}. Inbox left unprocessed; it will "
                        f"retry next tick.", role="ADMIN_OP")
                    continue
                if isinstance(r, dict):
                    if r.get("threshold_tripped"):
                        threshold_tripped = True
                    if r.get("executed"):
                        self._execution_count += 1
                    elif r.get("error") == "missing_system_prompt":
                        # Spec §5.3 — surface the missing-prompt condition to OP.
                        await self.bot.send(
                            f"🚨 *CRITICAL* — `system_prompt.md` missing for "
                            f"`{r.get('agent')}`. Agent paused until restored.\n\n"
                            f"{r.get('message', '')}"
                        )
                        self.paused_agents.add(r.get("agent"))
                    elif r.get("error") == "malformed_output":
                        # Spec §15.2 — notify OP and pause after 3+ strikes.
                        count = r.get("consecutive_failures", 0)
                        agent = r.get("agent")
                        if count >= 3:
                            await self.bot.send(
                                f"⚠️ *MALFORMED OUTPUT* — `{agent}` has produced "
                                f"{count} consecutive responses with no parseable "
                                f"ARTIFACT/WIKI_UPDATE/LOG_ENTRY blocks. Inbox is "
                                f"untouched; agent paused. Investigate via "
                                f"`/log {agent}` and `/resume {agent}` once "
                                f"resolved."
                            )
                            self.paused_agents.add(agent)

        # 2b. Drain execution flags (Spec §6.4 / §11 — flag_for_execution's two
        # use cases). A flag WITH a cycle_id is a cycle-internal exchange turn
        # (freeform OP/Admin-OP message → partner agent responds); a flag WITHOUT
        # one is a direct /run (vega_run) → a normal inbox pass.
        for flag in drain_execution_flags(config.STATE_DIR):
            agent_code = flag.get("agent")
            cycle_id = flag.get("cycle_id")
            if not agent_code or agent_code in self.paused_agents:
                continue
            try:
                if cycle_id:
                    result = await self.executor.execute_cycle_turn(agent_code, cycle_id)
                else:
                    result = await self.executor.execute(agent_code)   # /run inbox pass
            except Exception as e:
                print(f"[main] flagged execution failed ({agent_code}/{cycle_id}): "
                      f"{type(e).__name__}: {e}", flush=True)
                continue
            if isinstance(result, dict):
                if result.get("threshold_tripped"):
                    threshold_tripped = True
                if result.get("executed"):
                    self._execution_count += 1
                    reply = result.get("response_text")
                    if reply and cycle_id:
                        await self.bot.relay_exchange_reply(agent_code, cycle_id, reply)

        # 3. SYS on schedule (Spec §13) or inline wiki-threshold trigger (§5.1+§8).
        # The threshold path fires THIS tick (no polling) with a targeted audit
        # request; reset counters + record the run either way.
        if self._should_run_sys() or threshold_tripped:
            print("[main] Running SYS audit…"
                  + (" (wiki replace threshold)" if threshold_tripped else ""))
            since = self._last_sys_run.isoformat() if self._last_sys_run else None
            audit_req = ("Wiki replace_section threshold exceeded — review recent "
                         "replace_section activity across agents." if threshold_tripped
                         and not self._should_run_sys() else None)
            sys_result = await self.executor.execute_sys(audit_request=audit_req, since=since)
            # Spec §15.2 — notify OP if SYS itself produces malformed output 3+ times.
            # (Do NOT pause SYS: scheduled audits matter; operator should investigate.)
            if (isinstance(sys_result, dict)
                    and sys_result.get("error") == "malformed_output"
                    and sys_result.get("consecutive_failures", 0) >= 3):
                await self.bot.send(
                    f"⚠️ *MALFORMED SYS AUDIT* — SYS has produced "
                    f"{sys_result['consecutive_failures']} consecutive responses "
                    f"with no parseable blocks. Cross-agent governance audit is "
                    f"effectively blind. Investigate via `/log SYS` and check "
                    f"the framework + model assignment."
                )
            self.wiki.reset_replace_counters()
            self._record_sys_run()

        # 4. CORTEX periodic maintenance (Spec §13 lines 1286-1289).
        # Gated — stub raises NotImplementedError; we swallow it because the
        # default deployment has CORTEX off.
        # Cadence is configurable via CORTEX_MAINTENANCE_EVERY_N (Spec §13).
        # The previous implementation derived it from SYS_EXECUTION_THRESHOLD
        # // 2 which coupled two unrelated knobs — tuning the SYS audit
        # cadence silently shifted CORTEX maintenance frequency.
        cortex_every_n = getattr(config, "CORTEX_MAINTENANCE_EVERY_N", 10)
        if getattr(config, "CORTEX_ENABLED", False) and self.cortex is not None:
            if self._execution_count > 0 and (
                self._execution_count % max(cortex_every_n, 1) == 0
            ):
                for agent_code in config.AGENTS:
                    try:
                        self.cortex.periodic_maintenance(agent_code)
                    except NotImplementedError:
                        break   # don't spam — stub message logged once is enough

    def _should_run_sys(self, now: datetime | None = None) -> bool:
        """Thin wrapper — see should_fire_sys() for the testable logic."""
        if now is None:
            now = datetime.now(timezone.utc)
        return should_fire_sys(
            now=now,
            schedule=config.SYS_SCHEDULE,
            daily_time=config.SYS_DAILY_TIME,
            execution_count=self._execution_count,
            execution_threshold=config.SYS_EXECUTION_THRESHOLD,
            last_sys_run=self._last_sys_run,
        )

    def _record_sys_run(self, now: datetime | None = None) -> None:
        """Persist the SYS run watermark. Read at startup; updated after each
        execute_sys() call."""
        if now is None:
            now = datetime.now(timezone.utc)
        self._last_sys_run = now
        atomic_save_json(self._sys_run_file, {"last_sys_run": now.isoformat()})

    def _load_sys_watermark(self) -> datetime | None:
        """Load the persisted SYS run timestamp.

        On parse failure (corrupt file, missing field, malformed timestamp),
        warn loudly and return None — the next SYS run will see the full
        history (as on the very first launch). Silent None would cause SYS
        to silently re-audit everything every run, which is the safer
        outcome but the operator should know the watermark is gone."""
        data = load_json(self._sys_run_file, default=None)
        if not data:
            return None
        try:
            return datetime.fromisoformat(
                data.get("last_sys_run", "").replace("Z", "+00:00")
            )
        except (ValueError, AttributeError) as e:
            print(f"[main] WARNING — corrupt sys_run watermark at "
                  f"{self._sys_run_file} ({type(e).__name__}: {e}). "
                  f"Next SYS audit will include the full history.",
                  flush=True)
            return None

    # ─── SG exchange notifications ───────────────────────────────────────────

    async def _maybe_notify_sg_exchange(self, artifact: Artifact) -> None:
        """When SG emits an artifact during an active PROP exchange, surface to OP."""
        if artifact.sender != "SG":
            return
        if artifact.type in {"PROP", "SUM"}:
            return  # already notified or archive-only
        # Heuristic: if any in-progress PROP exists with SG, mirror SG outputs to OP.
        if self.op_backlog.list_in_progress():
            await self.bot.handle_sg_exchange_response(artifact)

    # ─── Agent control ───────────────────────────────────────────────────────

    def _pause_agent(self, code: str) -> None:
        self.paused_agents.add(code)

    def _resume_agent(self, code: str) -> None:
        self.paused_agents.discard(code)


def _recipients_for(artifact: Artifact) -> list[str]:
    """Pre-route lookup of recipients for cycle event detection."""
    from router import ROUTING_TABLE
    if artifact.type == "REJ":
        key = (artifact.sender, "REJ", artifact.ref_type or "")
    else:
        key = (artifact.sender, artifact.type)
    return [r["to"] for r in ROUTING_TABLE.get(key, [])]


# Scheduling helpers (should_fire_sys, is_daily_time) live in scheduling.py
# so they're testable without importing config. See Spec §13.


# ─── Bootstrap helpers ───────────────────────────────────────────────────────

def initialize_project(initial_input_path: str | None = None) -> None:
    """One-time project setup per Spec §14.

    Steps mirror spec §14 sequence:
      1. Create directory structure
      2. Extract system prompts from framework + write agents/<CODE>/system_prompt.md
      3. (Wiki seeds and UNIVERSAL are deployed at /vega-init time, not here.)
      4. Initialize sequence + instance state files
      5. Place initial input in SG inbox (if provided)

    Idempotent — safe to re-run. Existing system_prompt.md files are not
    overwritten (operator may have customized them; regeneration is opt-in).
    """
    print(f"[init] BASE_DIR = {config.BASE_DIR}")
    # Step 1 — directory tree
    for d in [
        config.AGENTS_DIR, config.UNIVERSAL_DIR, config.SCOPE_DIR,
        config.ARTIFACTS_DIR, config.CYCLES_DIR, config.STATE_DIR,
        config.OP_BACKLOG_DIR, config.TEST_MODELS_FULL_DIR, config.TEST_MODELS_BUILD_DIR,
    ]:
        Path(d).mkdir(parents=True, exist_ok=True)
    for code in config.AGENTS:
        for sub in ("wiki", "inbox", "outbox"):
            (Path(config.AGENTS_DIR) / code / sub).mkdir(parents=True, exist_ok=True)
    # Decision backlogs — OP (PROP) and Admin OP (GOV + role requests) (§10.1).
    admin_backlog_dir = _cfg_path(config, "ADMIN_BACKLOG_DIR", "admin_backlog")
    for backlog_dir in (config.OP_BACKLOG_DIR, admin_backlog_dir):
        for sub in ("pending", "in_progress", "resolved"):
            (Path(backlog_dir) / sub).mkdir(parents=True, exist_ok=True)
    # Role-based access scaffolding (Spec v5 §2, §10.4).
    Path(_cfg_path(config, "CONFIG_DIR", "config")).mkdir(parents=True, exist_ok=True)
    Path(_cfg_path(config, "INVITES_DIR", "config", "invites")).mkdir(
        parents=True, exist_ok=True)
    role_events = Path(_cfg_path(config, "ROLE_EVENTS_FILE", "state", "role_events.jsonl"))
    role_events.parent.mkdir(parents=True, exist_ok=True)
    role_events.touch(exist_ok=True)   # append-only audit trail (SYS reads, §5.9)

    # Step 2 — extract system prompts from framework (Spec §14 step 2 + §5.3)
    _generate_system_prompts()

    # Step 4 — state files (idempotent)
    seq_file = Path(config.STATE_DIR) / "sequences.json"
    if not seq_file.exists():
        atomic_save_json(seq_file, {})
    inst_file = Path(config.STATE_DIR) / "instance_ids.json"
    if not inst_file.exists():
        atomic_save_json(inst_file, {})

    # Step 5 — place initial input in SG inbox
    if initial_input_path:
        _place_initial_input(config, initial_input_path)


def _place_initial_input(cfg, initial_input_path: str) -> None:
    """Spec §14 step 5 / audit NEW-12 — archive INIT-OP-001 immutably and deliver
    it to SG's inbox. The router isn't running yet, so without the manual archive
    SYS has no immutable record of the bootstrap directive. Extracted for testing."""
    path = Path(initial_input_path)
    if not path.exists():
        raise FileNotFoundError(initial_input_path)
    artifact = Artifact(
        type="INIT",
        sender="OP",
        content=path.read_text(),
        id="INIT-OP-001",
        timestamp=utcnow_iso(),
        applied_via="import",   # SC-6 — bootstrap artifact, never routed (§7.4/§14)
    )
    store = ArtifactStore(cfg.AGENTS_DIR, cfg.ARTIFACTS_DIR)
    store.archive_artifact(artifact)        # Spec §14 lines 2486-2488 (NEW-12)
    store.inbox("SG").deliver(artifact)
    print(f"[init] Archived + placed initial input in SG inbox: {artifact.id}")


def _generate_system_prompts() -> None:
    """Spec §14 step 2 — extract role definitions from the framework and write
    them to agents/<CODE>/system_prompt.md.

    Idempotent: only writes if the file is missing. If the framework markdown
    isn't available (config.FRAMEWORK_DIR doesn't exist or doesn't contain the
    expected file), prints a warning and continues — operator can fix the
    missing prompt and call again. Combined with Fix 7 (loud failure on
    missing prompt at execute time), this gives the operator a clear path:
    `python main.py --init-only` re-runs this step.
    """
    from executor import FRAMEWORK_FILENAMES, _first_existing
    framework_dir = Path(getattr(config, "FRAMEWORK_DIR",
                                 Path(config.BASE_DIR) / "framework"))
    # Prefer v6, then v5, then v4 (Spec v5 §14 / audit #12).
    framework_md = _first_existing(framework_dir, FRAMEWORK_FILENAMES)
    if framework_md is None:
        print(f"[init] WARNING — framework not found in {framework_dir} "
              "(looked for v6/v5/v4). System prompts will NOT be generated. "
              "Place the framework markdown there and re-run with --init-only.")
        return

    framework_text = framework_md.read_text()
    project_name = getattr(config, "PROJECT_NAME", "")

    for code in config.AGENTS:
        path = Path(config.AGENTS_DIR) / code / "system_prompt.md"
        if path.exists():
            continue   # idempotent — don't clobber operator customization
        try:
            prompt = compose_system_prompt(framework_text, code, project_name)
        except ValueError as e:
            print(f"[init] WARNING — could not generate prompt for {code}: {e}")
            continue
        atomic_write(path, prompt)
        print(f"[init] Wrote {path}")


# ─── Emergency import (SC-6, Spec §5.6) ──────────────────────────────────────

async def import_artifact(path: str, artifact_type: str = "DOC",
                          sender: str = "OP") -> str:
    """Admin OP emergency import (SC-6, §5.6 line 1091). Archive a pre-existing
    document as an artifact carrying `applied_via: import` (so the SYS provenance
    audit recognizes it as a sanctioned bypass, not an orphan), then auto-route a
    GOV-SYS to Admin OP so the bypass is acknowledged through governance."""
    src = Path(path)
    if not src.exists():
        raise FileNotFoundError(path)
    orch = Orchestrator()
    artifact_id = await orch.sequences.next_id(sender, artifact_type)
    imported = Artifact(
        type=artifact_type, sender=sender, content=src.read_text(),
        id=artifact_id, timestamp=utcnow_iso(), applied_via="import",
    )
    # Archive DIRECTLY (not via the router) — an import has no routing_log entry
    # by definition; the applied_via marker is what authorizes that absence.
    orch.store.archive_artifact(imported)
    # Acknowledge the bypass through governance.
    gov_id = await orch.sequences.next_id("SYS", "GOV")
    gov = Artifact(
        type="GOV", sender="SYS", recipient="ADMIN_OP", id=gov_id,
        timestamp=utcnow_iso(), priority="P1",
        content=(f"IMPORT ACKNOWLEDGEMENT — artifact {artifact_id} ({artifact_type} "
                 f"from {sender}) was imported via the emergency CLI with "
                 f"`applied_via: import`. It bypassed the normal generation→"
                 f"routing path. Confirm this bypass was authorized (/resolve), "
                 f"or investigate. Source: {src.name}"))
    await orch.router.route(gov)
    print(f"[import] Archived {artifact_id} (applied_via=import); "
          f"routed {gov_id} to Admin OP for acknowledgement.")
    return artifact_id


# ─── CLI ─────────────────────────────────────────────────────────────────────

async def _amain() -> None:
    parser = argparse.ArgumentParser(prog="vega-orchestrator")
    parser.add_argument("--bootstrap", help="Path to initial input file (placed in SG inbox)")
    parser.add_argument("--sys", action="store_true", help="Run SYS once and exit")
    parser.add_argument("--audit-request", help="Optional audit instruction for --sys")
    parser.add_argument("--init-only", action="store_true", help="Run initialize_project and exit")
    parser.add_argument("--import-artifact", help="Emergency import a document as an "
                        "artifact (applied_via=import) + route a GOV ack (SC-6)")
    parser.add_argument("--import-type", default="DOC", help="Artifact type for --import-artifact")
    parser.add_argument("--import-sender", default="OP", help="Sender for --import-artifact")
    args = parser.parse_args()

    if args.bootstrap or args.init_only:
        initialize_project(args.bootstrap)
        if args.init_only:
            return

    if args.import_artifact:
        await import_artifact(args.import_artifact, args.import_type, args.import_sender)
        return

    orch = Orchestrator()

    if args.sys:
        result = await orch.executor.execute_sys(args.audit_request)
        print(f"SYS result: {result}")
        return

    await orch.run()


def main() -> None:
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(0)


if __name__ == "__main__":
    main()
