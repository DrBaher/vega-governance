"""
Agent execution model.

Per Spec §5. One agent invocation = one Anthropic API call (stateless). The wiki is
the memory. Conversation cycles carry context across executions for bounded multi-turn
interactions.

Static content (system prompt + wiki + UNIVERSAL + scope) is cache_control'd to
reduce input cost on repeat calls within the cache TTL.

Extended thinking is enabled for all agents. Thinking blocks are stored in the
execution log for OP review and SYS audit.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import traceback
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from anthropic import AsyncAnthropic, APIError

from artifact_store import ArtifactStore
from cycle_manager import CycleManager
from models import (
    Artifact, ConsultationRecord, Cycle, LogEntry, WikiUpdate, utcnow_iso,
)
from sequence_manager import InstanceManager, ModelAssignmentManager, SequenceManager
from state_manager import LOCKS, atomic_save_json, load_json
from wiki_manager import WikiManager


# ─── Framework document version chains (Spec v5 §14, audit #12) ──────────────
# Newest first; the loader uses the first file that exists. v6 framework + v5
# spec are the v0.3.0 source of truth; older filenames are kept for back-compat
# with deployments that haven't synced yet.
FRAMEWORK_FILENAMES = (
    "VEGA_Architecture_Framework_v6.md",
    "VEGA_Architecture_Framework_v5.md",
    "VEGA_Architecture_Framework_v4.md",
)
SPEC_FILENAMES = (
    "VEGA_Orchestrator_Technical_Spec_v5.md",
    "VEGA_Orchestrator_Technical_Spec_v4.md",
    "VEGA_Orchestrator_Technical_Spec_v3.md",
)
MANIFESTO_FILENAMES = ("VEGA_Manifesto_v4.md",)


def _first_existing(framework_dir, names) -> "Path | None":
    for name in names:
        path = framework_dir / name
        if path.exists():
            return path
    return None


# ─── Response parsing ────────────────────────────────────────────────────────

# Block headers expected in agent output per Spec §5.3.
BLOCK_HEADERS = {"ARTIFACT", "WIKI_UPDATE", "LOG_ENTRY"}

_BLOCK_RE = re.compile(
    r"(?ms)^###\s+(ARTIFACT|WIKI_UPDATE|LOG_ENTRY)\s*\n"
    r"(.*?)"
    r"(?=^###\s+(?:ARTIFACT|WIKI_UPDATE|LOG_ENTRY)\s*\n|\Z)"
)


def _strip_blocks(text: str) -> str:
    """Return the conversational text outside ARTIFACT/WIKI_UPDATE/LOG_ENTRY
    blocks — the human-facing reply for a cycle-internal exchange turn (§6.4)."""
    return _BLOCK_RE.sub("", text).strip()


def _split_block(block_text: str) -> tuple[dict[str, str], str]:
    """Split 'key: value\\n...\\n---\\n<content>' into (header dict, content)."""
    if "---" in block_text:
        header_text, body = block_text.split("---", 1)
    else:
        header_text, body = block_text, ""
    header: dict[str, str] = {}
    for line in header_text.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            header[key.strip().lower()] = value.strip()
    return header, body.strip()


def _parse_blocks(text: str) -> dict[str, list[tuple[dict[str, str], str]]]:
    """Return {block_type: [(header, content), ...]}."""
    by_type: dict[str, list[tuple[dict[str, str], str]]] = {h: [] for h in BLOCK_HEADERS}
    for match in _BLOCK_RE.finditer(text):
        block_type = match.group(1)
        block_body = match.group(2)
        header, content = _split_block(block_body)
        by_type[block_type].append((header, content))
    return by_type


# ─── Executor ────────────────────────────────────────────────────────────────

class AgentExecutor:

    def __init__(
        self,
        config,
        anthropic_client: AsyncAnthropic,
        store: ArtifactStore,
        wiki: WikiManager,
        cycles: CycleManager,
        sequences: SequenceManager,
        instances: InstanceManager,
        models_mgr: ModelAssignmentManager,
        agents_dir: str | Path,
        scope_dir: str | Path,
        universal_dir: str | Path,
        state_dir: str | Path,
        cortex: Any = None,
    ) -> None:
        self.config = config
        self.client = anthropic_client
        self.store = store
        self.wiki = wiki
        self.cycles = cycles
        self.sequences = sequences
        self.instances = instances
        self.models = models_mgr
        self.agents_dir = Path(agents_dir)
        self.scope_dir = Path(scope_dir)
        self.universal_dir = Path(universal_dir)
        self.state_dir = Path(state_dir)
        self.execution_log_path = self.state_dir / "execution_log.json"
        self.artifact_index_path = self.state_dir / "artifact_index.json"
        # Spec §15.2 — per-agent counter of consecutive malformed responses.
        self.malformed_counters_path = self.state_dir / "malformed_counters.json"
        # CORTEX is a navigation layer over wikis — see cortex_script.py.
        # Off by default; the orchestrator gates all calls on CORTEX_ENABLED.
        self.cortex = cortex

    # ─── Public ──────────────────────────────────────────────────────────────

    async def execute(self, agent_code: str) -> dict[str, Any]:
        inbox = self.store.inbox(agent_code)
        items = inbox.get_unprocessed()
        if not items:
            return {"executed": False}

        start_ts = time.perf_counter()   # Spec §17.1 — duration_seconds
        cycle = self.cycles.get_active_cycle(agent_code, items[0])
        # Spec §5.3 — fail loudly on missing system_prompt; do not silently
        # degrade with a generic stub.
        try:
            system_prompt = self._load_system_prompt(agent_code)
        except FileNotFoundError as e:
            self.wiki.append_log(agent_code, LogEntry(
                f"CRITICAL | system_prompt.md missing — agent skipped. {e}"
            ))
            return {"executed": False, "agent": agent_code,
                    "error": "missing_system_prompt", "message": str(e)}
        wiki_content, entries_included = self.wiki.read_all(agent_code)
        universal = self.wiki.read_universal(agent_code)
        scope = self._load_scope(agent_code)
        # Framework v5 §12 + Spec v4 §5.3 — tiered context per role.
        # Minimal tier (SE/TE) receives only their system prompt; the full
        # framework would risk over-reasoning about governance instead of
        # executing scope/test changes precisely.
        framework = self._load_framework_view(agent_code)

        static_content = self._compose_static(
            wiki_content=wiki_content,
            universal=universal,
            scope=scope,
            framework=framework,
        )
        dynamic_content = self._format_inbox(items)

        if cycle:
            messages = self._build_cycle_messages(cycle, static_content, dynamic_content)
            cycle.model = self.models.get(agent_code)
        else:
            messages = self._build_fresh_messages(static_content, dynamic_content)

        model = self.models.get(agent_code)
        instance = self.instances.get_or_create(agent_code)

        # Anthropic call with retry
        response, error = await self._call_with_retry(
            model=model, system=system_prompt, messages=messages,
        )

        if error is not None:
            self.wiki.append_log(agent_code, LogEntry(
                f"ERROR | API call failed: {error}"
            ))
            return {"executed": False, "error": str(error)}

        thinking_blocks, full_text = self._extract_response(response)

        # Parse output blocks
        parsed = _parse_blocks(full_text)

        artifacts: list[Artifact] = []
        for header, content in parsed["ARTIFACT"]:
            artifacts.append(await self._build_artifact(agent_code, header, content, instance, model))

        wiki_updates: list[WikiUpdate] = []
        for header, content in parsed["WIKI_UPDATE"]:
            wiki_updates.append(self._build_wiki_update(agent_code, header, content))

        log_entries: list[LogEntry] = [LogEntry(content) for _, content in parsed["LOG_ENTRY"]]

        # Spec §15.2 — malformed agent output handling.
        # If the agent produced ANY signal (visible text OR thinking blocks)
        # but no parseable ARTIFACT/WIKI_UPDATE/LOG_ENTRY blocks, that's a
        # malformed-output strike. The earlier check looked only at
        # `full_text.strip()`, so an empty-text response with thinking-only
        # content (model "thought" but emitted nothing) was silently consumed
        # — the inbox got marked processed and the malformed counter never
        # bumped. With thinking-only included, we catch that case too.
        produced_signal = bool(full_text.strip()) or bool(thinking_blocks)
        if not (artifacts or wiki_updates or log_entries) and produced_signal:
            return await self._handle_malformed(agent_code, full_text, items)

        # Successful parse — reset consecutive counter (Spec §15.2).
        await self._reset_malformed_counter(agent_code)

        # Apply wiki updates with diff logging (Spec §16.1 — async lock on counters)
        threshold_tripped = await self.wiki.apply_updates(agent_code, wiki_updates)

        # Append agent's log entries
        for entry in log_entries:
            self.wiki.append_log(agent_code, entry)

        # Cycle bookkeeping
        if cycle:
            # Use None instead of {} when there's no prior user message —
            # an empty-dict turn would silently introduce a malformed message
            # into the cycle history; downstream cycle-context estimation +
            # serialization would treat it as a real turn.
            user_msg = messages[-1] if messages else None
            assistant_msg = {"role": "assistant", "content": full_text}
            if user_msg is not None:
                self.cycles.append_turn(cycle, user_msg, assistant_msg)
            used = self.cycles.estimate_tokens(cycle.messages)
            limit = _model_context_limit(model)
            if limit and used / limit > self.config.CYCLE_CONTEXT_WARNING:
                # Spec §6.4 — compress early turns and emit the U-RC-08 warning.
                usage_pct = used / limit
                compressed = self.cycles.compress_early_turns(cycle)
                if compressed > 0:
                    self.wiki.append_log(agent_code, LogEntry(
                        f"CYCLE_COMPRESSION | {cycle.id} | Context at {usage_pct:.0%}, "
                        f"compressed early turns (U-RC-08 warning: verify post-compression)"
                    ))
                else:
                    # Not enough turns to compress yet; just warn.
                    self.wiki.append_log(agent_code, LogEntry(
                        f"CYCLE_CONTEXT_WARNING | {cycle.id} | Context at {usage_pct:.0%} "
                        f"({used}/{limit}) — too few turns to compress yet"
                    ))

        # Build consultation record
        consultation = ConsultationRecord(
            agent=agent_code,
            instance=instance,
            timestamp=utcnow_iso(),
            wiki_entries_included=entries_included,
        )

        # Place artifacts in outbox
        for artifact in artifacts:
            self.store.outbox(agent_code).place(artifact)

        # Mark inbox processed
        inbox.mark_processed(items)

        # Log execution (Spec §17.1)
        duration_seconds = time.perf_counter() - start_ts
        execution_id = await self._log_execution(
            agent=agent_code, model=model, instance=instance,
            inbox_items=items, artifacts=artifacts, wiki_updates=wiki_updates,
            thinking_blocks=thinking_blocks, consultation=consultation,
            cycle=cycle,
            tokens=getattr(response, "usage", None),
            duration_seconds=duration_seconds,
        )

        # Update artifact index for /thinking
        await self._update_artifact_index(artifacts, execution_id)

        # CORTEX post-execution hook (Spec §5.1 lines 454-457).
        # Gated — stub raises NotImplementedError; we swallow it because the
        # default deployment has CORTEX_ENABLED=False.
        if getattr(self.config, "CORTEX_ENABLED", False) and self.cortex is not None:
            try:
                self.cortex.post_execution(
                    agent_code=agent_code,
                    log_entries=log_entries,
                    consultation_record=consultation,
                    wiki_updates=wiki_updates,
                )
            except NotImplementedError:
                pass

        return {
            "executed": True,
            "agent": agent_code,
            "execution_id": execution_id,
            "artifacts": [a.id for a in artifacts],
            "wiki_updates": len(wiki_updates),
            "threshold_tripped": threshold_tripped,
        }

    async def execute_cycle_turn(self, agent_code: str, cycle_id: str) -> dict[str, Any]:
        """Run `agent_code` on a cycle whose trailing message is an unanswered
        exchange turn (Spec §6.4 — cycle-internal exchange).

        Unlike execute(), there is NO inbox item: the cycle's last message IS the
        new user turn (appended by the Telegram/MCP freeform handler). The agent
        sees the full cycle history, responds, and its reply is appended to the
        cycle as an assistant turn. Any formal artifacts it emits (e.g. SG decides
        to issue an AUTH/PROP, closing the exchange) go to the outbox and route
        normally on the next tick. The conversational text is returned so the
        main loop can relay it back to the human operator.
        """
        cycle = self.cycles.get_by_id(cycle_id)
        if cycle is None or not cycle.messages:
            return {"executed": False, "agent": agent_code, "reason": "no_cycle"}
        # Only run if the trailing turn is an unanswered user turn.
        if cycle.messages[-1].get("role") != "user":
            return {"executed": False, "agent": agent_code, "reason": "no_pending_turn"}

        start_ts = time.perf_counter()
        try:
            system_prompt = self._load_system_prompt(agent_code)
        except FileNotFoundError as e:
            self.wiki.append_log(agent_code, LogEntry(
                f"CRITICAL | system_prompt.md missing — agent skipped. {e}"
            ))
            return {"executed": False, "agent": agent_code,
                    "error": "missing_system_prompt", "message": str(e)}

        wiki_content, entries_included = self.wiki.read_all(agent_code)
        universal = self.wiki.read_universal(agent_code)
        scope = self._load_scope(agent_code)
        framework = self._load_framework_view(agent_code)
        static_content = self._compose_static(
            wiki_content=wiki_content, universal=universal,
            scope=scope, framework=framework,
        )
        messages = self._build_cycle_continuation(cycle, static_content)
        cycle.model = self.models.get(agent_code)

        model = self.models.get(agent_code)
        instance = self.instances.get_or_create(agent_code)
        response, error = await self._call_with_retry(
            model=model, system=system_prompt, messages=messages,
        )
        if error is not None:
            self.wiki.append_log(agent_code, LogEntry(
                f"ERROR | API call failed (cycle {cycle_id}): {error}"
            ))
            return {"executed": False, "agent": agent_code, "error": str(error)}

        thinking_blocks, full_text = self._extract_response(response)
        parsed = _parse_blocks(full_text)

        artifacts: list[Artifact] = []
        for header, content in parsed["ARTIFACT"]:
            artifacts.append(await self._build_artifact(agent_code, header, content, instance, model))
        wiki_updates: list[WikiUpdate] = []
        for header, content in parsed["WIKI_UPDATE"]:
            wiki_updates.append(self._build_wiki_update(agent_code, header, content))
        log_entries: list[LogEntry] = [LogEntry(content) for _, content in parsed["LOG_ENTRY"]]

        # Spec §15.2 — malformed if the agent produced signal but nothing parseable
        # AND nothing conversational to relay.
        conversational = _strip_blocks(full_text)
        produced_signal = bool(full_text.strip()) or bool(thinking_blocks)
        if not (artifacts or wiki_updates or log_entries or conversational) and produced_signal:
            return await self._handle_malformed(agent_code, full_text, [])
        await self._reset_malformed_counter(agent_code)

        threshold_tripped = await self.wiki.apply_updates(agent_code, wiki_updates)
        for entry in log_entries:
            self.wiki.append_log(agent_code, entry)

        # Record the agent's reply as the cycle's assistant turn (§6.4). Use the
        # conversational text when present, else the full text (so a turn that
        # only emits artifacts still records *something* in the dialogue).
        self.cycles.append_assistant_turn(cycle, conversational or full_text)
        used = self.cycles.estimate_tokens(cycle.messages)
        limit = _model_context_limit(model)
        if limit and used / limit > self.config.CYCLE_CONTEXT_WARNING:
            usage_pct = used / limit
            compressed = self.cycles.compress_early_turns(cycle)
            if compressed > 0:
                self.wiki.append_log(agent_code, LogEntry(
                    f"CYCLE_COMPRESSION | {cycle.id} | Context at {usage_pct:.0%}, "
                    f"compressed early turns (U-RC-08 warning: verify post-compression)"
                ))

        for artifact in artifacts:
            self.store.outbox(agent_code).place(artifact)

        consultation = ConsultationRecord(
            agent=agent_code, instance=instance, timestamp=utcnow_iso(),
            wiki_entries_included=entries_included,
        )
        duration_seconds = time.perf_counter() - start_ts
        execution_id = await self._log_execution(
            agent=agent_code, model=model, instance=instance,
            inbox_items=[], artifacts=artifacts, wiki_updates=wiki_updates,
            thinking_blocks=thinking_blocks, consultation=consultation,
            cycle=cycle, tokens=getattr(response, "usage", None),
            duration_seconds=duration_seconds,
        )
        await self._update_artifact_index(artifacts, execution_id)

        return {
            "executed": True, "agent": agent_code, "execution_id": execution_id,
            "cycle_id": cycle.id, "response_text": conversational,
            "artifacts": [a.id for a in artifacts],
            "wiki_updates": len(wiki_updates),
            "threshold_tripped": threshold_tripped,
        }

    async def execute_sys(self, audit_request: str | None = None,
                          since: str | None = None) -> dict[str, Any]:
        """SYS execution.

        Spec §5.6 — SYS inbox is composed from:
          - all_artifacts = artifact_store.get_recent(since=last_sys_run)
          - execution_log = load_execution_log(since=last_sys_run)
          - all agent wikis + log.md files
          - the framework
          - optional audit_request

        Without artifact + execution log inputs, SYS cannot audit cross-agent
        artifact flow against its own log — the audit becomes wiki-only.

        `since` is an ISO 8601 timestamp; pass the orchestrator's last_sys_run
        watermark (None on the very first run = include everything).
        """
        agent_code = "SYS"
        system_prompt = self._load_system_prompt(agent_code)
        wiki_content, entries_included = self.wiki.read_all(agent_code)
        universal = self.wiki.read_universal(agent_code)

        all_agents = [c for c in self.config.AGENTS if c != "SYS"]
        all_wikis = self.wiki.read_all_agents(all_agents)
        all_logs = self.wiki.read_all_logs(all_agents)
        framework_text = self._load_framework()
        recent_artifacts = self.store.get_recent_artifacts(since=since)
        recent_execution_log = self._load_recent_execution_log(since=since)

        inbox_content = self._format_sys_inbox(
            all_wikis=all_wikis, all_logs=all_logs,
            framework=framework_text, audit_request=audit_request,
            recent_artifacts=recent_artifacts,
            recent_execution_log=recent_execution_log,
            since=since,
        )

        static_content = self._compose_static(
            wiki_content=wiki_content,
            universal=universal,
            scope="",  # SYS doesn't need scope
            framework=framework_text,
        )
        messages = self._build_fresh_messages(static_content, inbox_content)

        model = self.models.get(agent_code)
        instance = self.instances.get_or_create(agent_code)
        response, error = await self._call_with_retry(
            model=model, system=system_prompt, messages=messages,
        )
        if error is not None:
            self.wiki.append_log(agent_code, LogEntry(
                f"ERROR | SYS API call failed: {error}"
            ))
            return {"executed": False, "error": str(error)}

        thinking_blocks, full_text = self._extract_response(response)
        parsed = _parse_blocks(full_text)

        artifacts: list[Artifact] = []
        for header, content in parsed["ARTIFACT"]:
            artifacts.append(await self._build_artifact(agent_code, header, content, instance, model))

        wiki_updates: list[WikiUpdate] = []
        for header, content in parsed["WIKI_UPDATE"]:
            wiki_updates.append(self._build_wiki_update(agent_code, header, content))

        log_entries_parsed: list[tuple[dict, str]] = parsed["LOG_ENTRY"]

        # Spec §15.2 also applies to SYS — silently producing nothing for
        # multiple daily audits is a signal OP must see. A SYS audit that
        # returns thinking-only with empty visible text must also strike
        # (audit NEW-2 — mirrors the execute() fix at line ~196 so the SYS
        # path doesn't silently consume a thinking-only response).
        produced_signal = bool(full_text.strip()) or bool(thinking_blocks)
        if not (artifacts or wiki_updates or log_entries_parsed) and produced_signal:
            return await self._handle_malformed(agent_code, full_text, [])
        await self._reset_malformed_counter(agent_code)

        await self.wiki.apply_updates(agent_code, wiki_updates)

        for _, content in log_entries_parsed:
            self.wiki.append_log(agent_code, LogEntry(content))

        for artifact in artifacts:
            self.store.outbox(agent_code).place(artifact)

        consultation = ConsultationRecord(
            agent=agent_code,
            instance=instance,
            timestamp=utcnow_iso(),
            wiki_entries_included=entries_included,
        )
        execution_id = await self._log_execution(
            agent=agent_code, model=model, instance=instance,
            inbox_items=[], artifacts=artifacts, wiki_updates=wiki_updates,
            thinking_blocks=thinking_blocks, consultation=consultation,
            cycle=None,
            tokens=getattr(response, "usage", None),
            duration_seconds=None,   # SYS-specific timing tracked elsewhere if needed
        )
        await self._update_artifact_index(artifacts, execution_id)
        return {"executed": True, "agent": "SYS", "execution_id": execution_id,
                "artifacts": [a.id for a in artifacts]}

    # ─── Anthropic API ───────────────────────────────────────────────────────

    async def _call_with_retry(self, model: str, system: str,
                               messages: list[dict[str, Any]],
                               max_retries: int = 3) -> tuple[Any, Optional[Exception]]:
        # Try in order: adaptive (Claude 4.x), enabled+budget (legacy), no thinking
        # The first call uses the configured mode; on a thinking-shape API error,
        # subsequent retries try the alternatives. This makes the orchestrator
        # robust to Anthropic API surface changes between SDK versions / model
        # families (the spec was written against the older 'enabled' shape).
        thinking_modes: list[dict[str, Any] | None] = []
        if self.config.EXTENDED_THINKING_ENABLED:
            thinking_modes.extend([
                {"type": "adaptive"},                                    # current Claude 4.x
                {"type": "enabled",
                 "budget_tokens": self.config.THINKING_BUDGET_TOKENS},   # legacy Claude 3.x
            ])
        thinking_modes.append(None)                                       # always last fallback

        last_error: Optional[Exception] = None
        for thinking in thinking_modes:
            for attempt in range(max_retries):
                try:
                    kwargs: dict[str, Any] = {
                        "model": model,
                        "max_tokens": self.config.MAX_TOKENS,
                        "system": _build_system_param(system, self.config),
                        "messages": messages,
                    }
                    if thinking is not None:
                        kwargs["thinking"] = thinking
                    response = await self.client.messages.create(**kwargs)
                    return response, None
                except APIError as e:
                    last_error = e
                    msg = str(e).lower()
                    # If the API rejected the thinking shape, stop retrying this
                    # mode and move to the next one (don't waste retries on a
                    # shape we know won't work).
                    if "thinking" in msg and (
                        "not supported" in msg or "invalid" in msg
                        or "unknown" in msg or "deprecated" in msg
                    ):
                        break
                    # Otherwise (rate limit, server error, etc.) retry this mode.
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2 ** attempt)
                        continue
                except Exception as e:
                    return None, e
        return None, last_error or RuntimeError("Exhausted retries without raising")

    def _extract_response(self, response: Any) -> tuple[list[str], str]:
        thinking: list[str] = []
        text_parts: list[str] = []
        for block in getattr(response, "content", []):
            btype = getattr(block, "type", None)
            if btype == "thinking":
                thinking.append(getattr(block, "thinking", ""))
            elif btype == "text":
                text_parts.append(getattr(block, "text", ""))
        return thinking, "\n".join(text_parts)

    # ─── Message composition ─────────────────────────────────────────────────

    def _compose_static(self, wiki_content: str, universal: str, scope: str,
                        framework: str = "") -> str:
        """Static (cacheable) content fed to every agent. Order matters for
        cache hit consistency — framework first (rarely changes), then UNIVERSAL,
        then wiki (changes most), then scope (large + project-specific).

        Framework is included so agents see the Project Addendum, the full
        Framework v4 (artifact-type vocabulary, interaction catalog, D-ARCH log),
        the Manifesto, and the Orchestrator Spec — not just their role excerpt
        embedded in the system_prompt. Without this, agents like SG can't see
        the artifact types they're allowed to produce, the Project Addendum's
        document manifest, or the Spec §11 OP exchange protocol — which leads
        to malformed output (e.g., DOC where PROP was needed).
        """
        parts = []
        if framework:
            parts.append("# FRAMEWORK + PROJECT CONTEXT\n" + framework)
        parts.append("# UNIVERSAL\n" + universal)
        parts.append("# AGENT WIKI\n" + wiki_content)
        if scope:
            parts.append("# SCOPE DOCUMENTS\n" + scope)
        return "\n\n".join(parts)

    def _build_fresh_messages(self, static_content: str,
                              dynamic_content: str) -> list[dict[str, Any]]:
        if self.config.PROMPT_CACHING_ENABLED:
            return [{
                "role": "user",
                "content": [
                    {"type": "text", "text": static_content,
                     "cache_control": {"type": "ephemeral"}},
                    {"type": "text", "text": dynamic_content},
                ],
            }]
        return [{"role": "user", "content": static_content + "\n\n" + dynamic_content}]

    def _build_cycle_continuation(self, cycle: Cycle,
                                  static_content: str) -> list[dict[str, Any]]:
        """Like _build_cycle_messages but with NO trailing dynamic turn — the
        cycle's own last message is the new user turn (Spec §6.4 exchange)."""
        if self.config.PROMPT_CACHING_ENABLED:
            messages: list[dict[str, Any]] = [{
                "role": "user",
                "content": [
                    {"type": "text", "text": static_content,
                     "cache_control": {"type": "ephemeral"}},
                    {"type": "text", "text": "Cycle context follows."},
                ],
            }]
        else:
            messages = [{"role": "user", "content": static_content}]
        for turn in cycle.messages:
            messages.append(turn)
        return messages

    def _build_cycle_messages(self, cycle: Cycle, static_content: str,
                              dynamic_content: str) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        if self.config.PROMPT_CACHING_ENABLED:
            messages.append({
                "role": "user",
                "content": [
                    {"type": "text", "text": static_content,
                     "cache_control": {"type": "ephemeral"}},
                    {"type": "text", "text": "Cycle context follows."},
                ],
            })
        else:
            messages.append({"role": "user", "content": static_content})
        # Prior cycle turns
        for turn in cycle.messages:
            messages.append(turn)
        messages.append({"role": "user", "content": dynamic_content})
        return messages

    def _format_inbox(self, items: list[Artifact]) -> str:
        chunks = []
        for a in items:
            chunks.append(f"--- INCOMING: {a.id} ({a.type} from {a.sender}) ---\n{a.to_markdown()}")
        return "\n\n".join(chunks)

    def _format_sys_inbox(
        self, all_wikis: dict[str, str], all_logs: dict[str, str],
        framework: str, audit_request: str | None,
        recent_artifacts: list[Artifact] | None = None,
        recent_execution_log: list[dict[str, Any]] | None = None,
        since: str | None = None,
    ) -> str:
        """Spec §5.6 SYS inbox composition."""
        chunks = ["# SYS AUDIT INBOX\n"]
        if since:
            chunks.append(f"## Audit window\nArtifacts and executions since: {since}")
        else:
            chunks.append("## Audit window\nFirst SYS run — full history included.")
        if audit_request:
            chunks.append(f"## OP audit request\n{audit_request}\n")
        chunks.append("## Framework (reference)\n" + framework[:20000])

        # Recent artifact archive — Spec §5.6 line 587
        if recent_artifacts:
            artifact_chunks = []
            for a in recent_artifacts:
                artifact_chunks.append(
                    f"### {a.id} ({a.type} from {a.sender} at {a.timestamp})\n"
                    f"{a.to_markdown()}"
                )
            chunks.append(
                "## Recent artifacts (archived)\n" + "\n\n".join(artifact_chunks)
            )

        # Recent execution log — Spec §5.6 line 588
        if recent_execution_log:
            log_lines = [
                f"- {e.get('timestamp')} {e.get('execution_id')} "
                f"{e.get('agent')} ({e.get('instance')}) "
                f"→ artifacts: {e.get('artifacts_produced', [])} "
                f"wiki_updates: {e.get('wiki_updates', [])}"
                for e in recent_execution_log
            ]
            chunks.append("## Recent execution log\n" + "\n".join(log_lines))

        for code, wiki in all_wikis.items():
            chunks.append(f"## Agent wiki: {code}\n{wiki}")
        for code, log in all_logs.items():
            chunks.append(f"## Agent log.md: {code}\n{log}")
        return "\n\n".join(chunks)

    def _load_recent_execution_log(self, since: str | None) -> list[dict[str, Any]]:
        """Return execution log entries with `timestamp > since`.

        IMPORTANT INVARIANT: all timestamps in execution_log.json are ISO 8601
        with trailing `Z` (produced by utcnow_iso() — see models.py). String
        comparison is therefore lexicographic = chronological. If anyone ever
        introduces timestamps with `+00:00` or naïve formats, this comparison
        silently misorders entries (lexicographic order across mixed formats
        is not chronological). Keep utcnow_iso() as the single producer.
        """
        log = load_json(self.execution_log_path, default=[])
        if not isinstance(log, list):
            return []
        if since is None:
            return log
        return [e for e in log if e.get("timestamp", "") > since]

    # ─── Artifact / WikiUpdate construction from parsed blocks ───────────────

    async def _build_artifact(self, agent_code: str, header: dict[str, str],
                              content: str, instance: str, model: str) -> Artifact:
        artifact_type = header.get("type", "").strip()
        references = [r.strip() for r in header.get("references", "").split(",") if r.strip()]
        priority = header.get("priority") or None
        ref_type = header.get("ref_type") or None
        certificate = header.get("certificate") or None
        recipient = header.get("recipient") or None
        disposition = header.get("disposition") or None

        artifact_id = header.get("id", "").strip()
        if not artifact_id and artifact_type:
            artifact_id = await self.sequences.next_id(agent_code, artifact_type)

        return Artifact(
            type=artifact_type, sender=agent_code, content=content,
            id=artifact_id, sender_instance=instance, sender_model=model,
            timestamp=utcnow_iso(), references=references, priority=priority,
            ref_type=ref_type, certificate=certificate, recipient=recipient,
            disposition=disposition,
        )

    def _build_wiki_update(self, agent_code: str, header: dict[str, str],
                          content: str) -> WikiUpdate:
        return WikiUpdate(
            file=header.get("file", "").strip(),
            action=header.get("action", "append").strip(),
            content=content,
            section=header.get("section") or None,
            justification=header.get("justification") or None,
            target="universal" if (agent_code == "SYS"
                                   and header.get("target", "").lower() == "universal") else "own",
        )

    # ─── System prompt + scope loading ───────────────────────────────────────

    def _load_system_prompt(self, agent_code: str) -> str:
        """Spec §5.3 — system prompt is the agent's role definition + output
        format. Missing prompt = silent agent-quality degradation, which is the
        opposite of what an auditable governance system wants.

        Raises FileNotFoundError. Caller (execute) catches and notifies OP.
        """
        path = self.agents_dir / agent_code / "system_prompt.md"
        if not path.exists():
            raise FileNotFoundError(
                f"system_prompt.md missing for agent {agent_code} at {path}. "
                f"Generate via /vega-init Step 6, or compose from Framework §5.X."
            )
        return path.read_text()

    def _load_scope(self, agent_code: str) -> str:
        # Agents with scope read access per Spec §3.2
        SCOPE_READERS = {"SG", "SA", "SE", "TG", "TA", "BR", "SYS"}
        if agent_code not in SCOPE_READERS:
            return ""
        if not self.scope_dir.exists():
            return ""
        chunks = []
        for path in sorted(self.scope_dir.glob("**/*")):
            if path.is_file() and path.suffix in {".md", ".txt"}:
                try:
                    chunks.append(f"## {path.relative_to(self.scope_dir)}\n{path.read_text()}")
                except Exception:
                    continue
        return "\n\n".join(chunks)

    def _load_framework_view(self, agent_code: str) -> str:
        """Framework v5 §12 + Spec v4 §5.3 — return the tiered view for this
        agent. Minimal-tier agents (SE/TE) receive an empty string; other
        tiers receive the relevant section excerpts. Project Addendum is
        attached for guardians (SG/TG) per §12.2.

        Falls back to loading the entire framework if the version isn't v5
        (older v4 deployments lack §12 — give them the whole document so
        they don't lose context completely)."""
        from framework_parser import load_framework_view

        framework_dir = (
            self.config.BASE_DIR / "framework"
            if hasattr(self.config, "BASE_DIR") else None
        )
        if framework_dir is None or not framework_dir.exists():
            return ""

        # Prefer v6, then v5, then v4 (Spec v5 §14 / audit #12). If only the
        # legacy v4 is around, fall back to the full-framework loader so we don't
        # ship an empty context.
        framework_path = _first_existing(framework_dir, FRAMEWORK_FILENAMES)
        if framework_path is None or framework_path.name.endswith("_v4.md"):
            return self._load_framework()

        framework_text = framework_path.read_text()

        # Manifesto and spec are useful at all tiers above minimal — attach
        # them after the section extracts so guardians see the full project
        # picture and SYS keeps spec context for audits.
        addendum_text = ""
        for path in sorted(framework_dir.glob("VEGA_*_Project_Addendum.md")):
            addendum_text += path.read_text() + "\n\n"

        view = load_framework_view(framework_text, agent_code, addendum_text)
        if not view:
            # Minimal tier — system prompt is sufficient (Framework v5 §12.4).
            return ""

        # For non-minimal tiers, also include the Manifesto AND the
        # Orchestrator Spec so agents know the artifact-output format
        # (Spec v4 §5.3) and SYS sees the auto-GOV reissue protocol
        # (Spec v4 §4.3 — delta 2.13). These are two separate documents;
        # appending only one would leave SYS blind to the spec.
        extras: list[str] = [view]

        manifesto_path = _first_existing(framework_dir, MANIFESTO_FILENAMES)
        if manifesto_path is not None:
            extras.append(f"## {manifesto_path.name}\n" +
                          manifesto_path.read_text())

        # Prefer spec v5; fall back to v4/v3 for older deployments.
        spec_path = _first_existing(framework_dir, SPEC_FILENAMES)
        if spec_path is not None:
            extras.append(f"## {spec_path.name}\n" + spec_path.read_text())
        return "\n\n".join(extras)

    def _load_framework(self) -> str:
        """Load all framework/ files: Framework v4, Manifesto, Spec, CORTEX,
        and the Project Addendum (whichever VEGA_*_Project_Addendum.md is present).

        Called for every agent execution so they can see artifact-type definitions,
        interaction catalog, D-ARCH log, and project-specific context. With prompt
        caching, the first call per agent pays full cost; subsequent calls within
        the TTL hit the cache.
        """
        framework_dir = self.config.BASE_DIR / "framework" if hasattr(self.config, "BASE_DIR") else None
        if framework_dir is None or not framework_dir.exists():
            return ""
        chunks = []
        # Prefer the newest published versions; fall back to older filenames
        # for back-compat with deployments that haven't synced framework v6 /
        # spec v5 yet. First match per slot wins. Stable order = cache-friendly.
        for filename_options in [
            FRAMEWORK_FILENAMES,
            MANIFESTO_FILENAMES,
            SPEC_FILENAMES,
            ("VEGA_CORTEX_Addon_Specification_v0.2_BETA.md",),
        ]:
            for name in filename_options:
                path = framework_dir / name
                if path.exists():
                    chunks.append(f"## {name}\n" + path.read_text())
                    break
        # Project Addendum — match VEGA_*_Project_Addendum.md
        for path in sorted(framework_dir.glob("VEGA_*_Project_Addendum.md")):
            chunks.append(f"## {path.name}\n" + path.read_text())
        return "\n\n".join(chunks)

    # ─── Logging ─────────────────────────────────────────────────────────────

    async def _log_execution(self, agent: str, model: str, instance: str,
                             inbox_items: list[Artifact], artifacts: list[Artifact],
                             wiki_updates: list[WikiUpdate], thinking_blocks: list[str],
                             consultation: ConsultationRecord, cycle: Cycle | None,
                             tokens: Any, duration_seconds: float | None = None) -> str:
        """Append a Spec §17.1-shaped execution log entry."""
        async with LOCKS.get("execution_log"):
            log = load_json(self.execution_log_path, default=[])
            if not isinstance(log, list):
                log = []
            execution_id = f"EXEC-{len(log)+1:06d}"

            # Spec §17.1 — thinking_summary is the first 200 chars of joined thinking
            # blocks (kept compact for scan; full text remains in thinking_blocks).
            thinking_joined = " ".join(b.replace("\n", " ") for b in (thinking_blocks or []))
            thinking_summary = thinking_joined[:200] if thinking_joined else None

            api_tokens: dict[str, Any] | None = None
            if tokens:
                api_tokens = {
                    "input":    getattr(tokens, "input_tokens", None),
                    "output":   getattr(tokens, "output_tokens", None),
                    # thinking_tokens may not be exposed by all SDK versions/models.
                    "thinking": getattr(tokens, "thinking_tokens", None),
                    "cache_creation_input":
                        getattr(tokens, "cache_creation_input_tokens", None),
                    "cache_read_input":
                        getattr(tokens, "cache_read_input_tokens", None),
                }

            entry = {
                "execution_id": execution_id,
                "timestamp": utcnow_iso(),
                "agent": agent,
                "instance": instance,
                "model": model,
                "inbox_items_processed": [a.id for a in inbox_items],
                "artifacts_produced": [a.id for a in artifacts if a.id],
                "wiki_updates": [
                    f"{u.file}: {u.action}" + (f" § {u.section}" if u.section else "")
                    for u in wiki_updates
                ],
                "wiki_entries_included": consultation.wiki_entries_included,
                "thinking_blocks": thinking_blocks,
                "thinking_summary": thinking_summary,
                "cycle_id": cycle.id if cycle else None,
                "api_tokens": api_tokens,
                "duration_seconds": (
                    round(duration_seconds, 3) if duration_seconds is not None else None
                ),
            }
            log.append(entry)
            atomic_save_json(self.execution_log_path, log)
            return execution_id

    async def _update_artifact_index(self, artifacts: list[Artifact],
                                     execution_id: str) -> None:
        async with LOCKS.get("artifact_index"):
            index = load_json(self.artifact_index_path, default={})
            for a in artifacts:
                if a.id:
                    index[a.id] = {"execution_id": execution_id, "sender": a.sender}
            atomic_save_json(self.artifact_index_path, index)

    # ─── Malformed output handling (Spec §15.2) ──────────────────────────────

    async def _handle_malformed(self, agent_code: str, raw_response: str,
                                items: list[Artifact]) -> dict[str, Any]:
        """Spec §15.2: log raw response, do not route, do not mark inbox
        processed, bump counter, signal OP if 3+ consecutive strikes."""
        excerpt = raw_response[:500].replace("\n", " ")
        self.wiki.append_log(agent_code, LogEntry(
            f"MALFORMED_OUTPUT | Response had no parseable ARTIFACT/WIKI_UPDATE/"
            f"LOG_ENTRY blocks. Inbox NOT marked processed; will retry next tick. "
            f"Raw excerpt: {excerpt}…"
        ))
        count = await self._bump_malformed_counter(agent_code)
        return {
            "executed": False,
            "agent": agent_code,
            "error": "malformed_output",
            "consecutive_failures": count,
            "inbox_items_left_unprocessed": [a.id for a in items],
        }

    async def _bump_malformed_counter(self, agent_code: str) -> int:
        async with LOCKS.get("malformed_counters"):
            counters = load_json(self.malformed_counters_path, default={})
            counters[agent_code] = counters.get(agent_code, 0) + 1
            atomic_save_json(self.malformed_counters_path, counters)
            return counters[agent_code]

    async def _reset_malformed_counter(self, agent_code: str) -> None:
        async with LOCKS.get("malformed_counters"):
            counters = load_json(self.malformed_counters_path, default={})
            if counters.get(agent_code, 0) > 0:
                counters[agent_code] = 0
                atomic_save_json(self.malformed_counters_path, counters)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _build_system_param(system_prompt: str, config: Any):
    """Spec §5.2 — when PROMPT_CACHING_ENABLED, wrap the system prompt as a
    single-element list with cache_control: ephemeral so the API caches it
    across executions for the same agent.

    Without this, SG/TG hot-path agents pay full system-prompt input cost on
    every call. With it, the system prompt cache hits as soon as the second
    call (within the cache TTL) and the static portion of the input cost
    drops accordingly.

    Returns:
      - a string (no caching, OR empty prompt — let the SDK reject naturally)
      - a list with one cache_control'd text block when caching is on AND the
        prompt has content
    """
    if not system_prompt:
        # Defensive: an empty cache_control'd block would be ambiguous to the
        # API. Returning the empty string lets the Anthropic SDK surface its
        # standard "system content required" error rather than a cache-shape
        # error. With Fix 7 + Fix 13 in place, this branch should be
        # unreachable in production.
        return system_prompt
    if not getattr(config, "PROMPT_CACHING_ENABLED", False):
        return system_prompt
    return [
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }
    ]


# Explicit per-model context limits (tokens). Keyed by model-id substring.
# Update when Anthropic changes published limits or we adopt a new model.
# Used by the cycle context-warning logic; an unrecognized model falls back
# to the conservative DEFAULT.
_MODEL_CONTEXT_LIMITS: dict[str, int] = {
    "claude-opus-4-7":      1_000_000,   # Opus 4.7 (1M)
    "claude-opus-4":          200_000,   # Opus 4.x
    "claude-sonnet-4-7":    1_000_000,   # Sonnet 4.7 (1M)
    "claude-sonnet-4-6":    1_000_000,   # Sonnet 4.6 (1M)
    "claude-sonnet-4":        200_000,   # Sonnet 4.x default
    "claude-haiku-4-5":       200_000,   # Haiku 4.5
    "claude-3-5-sonnet":      200_000,
    "claude-3-5-haiku":       200_000,
    "claude-3-opus":          200_000,
    "claude-3-sonnet":        200_000,
    "claude-3-haiku":         200_000,
}
_MODEL_CONTEXT_DEFAULT = 200_000


def _model_context_limit(model: str) -> int:
    """Return the context window for a model id.

    Longest-prefix-match against _MODEL_CONTEXT_LIMITS so `claude-opus-4-7`
    correctly resolves to its 1M entry instead of being shadowed by a shorter
    `claude-opus-4` match.
    """
    # Try exact match first.
    if model in _MODEL_CONTEXT_LIMITS:
        return _MODEL_CONTEXT_LIMITS[model]
    # Longest-prefix match (keys sorted desc by length).
    for key in sorted(_MODEL_CONTEXT_LIMITS, key=len, reverse=True):
        if key in model:
            return _MODEL_CONTEXT_LIMITS[key]
    return _MODEL_CONTEXT_DEFAULT
