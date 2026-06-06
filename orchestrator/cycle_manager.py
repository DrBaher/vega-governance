"""
Conversation cycle management.

Per Spec §6. A cycle is a bounded multi-turn interaction whose messages array provides
conversational coherence across executions. The array is discarded when the cycle closes.

Cycle types (Spec v4 §6.1):
  scn_application   SG ↔ SE   opens on SCN, closes on VAL
  tcn_application   TG ↔ TE   opens on TCN, closes on VAL(certificate=build)
  prop_exchange     SG ↔ OP   opens on PROP, closes on AUTH
  triage            TG int.   opens on TFR, closes on TRI/ESC/TCN
  build_scope_qa    BR ↔ EXT  opens on PRO-SCOPE@BR, closes on VR or supersession
  build_test_qa     BR ↔ EXT  opens on PRO-TEST-BUILD@BR, closes on VR or supersession
  build_results     BR ↔ EXT  opens on BRQ-results, closes on VR relayed
  build_remediation BR ↔ EXT  opens on TRI relayed to EXT, closes on new results
  de_qa             SG ↔ DE   opens on DE_OUT-SG-NNN, closes on DE_IN
  gov_exchange      SYS ↔ OP  opens on GOV-SYS-NNN, closes on /resolve
"""

from __future__ import annotations

import json
from pathlib import Path

from models import Artifact, Cycle
from state_manager import atomic_write


def _cycle_filename(cycle_id: str) -> str:
    """Sanitize cycle id for filesystem use.
    Cycle ids contain '-' (per Spec §6.2: f'{cycle_type}-{opening_artifact.id}')
    which is already filesystem-safe. The replacements below are belt-and-
    suspenders for older ids that used ':' before Fix 16.
    """
    safe = cycle_id.replace(":", "-").replace("/", "_")
    return f"{safe}.json"


class CycleManager:

    def __init__(self, cycles_dir: str | Path, archive_dir: str | Path) -> None:
        self.active_dir = Path(cycles_dir)
        self.archive_dir = Path(archive_dir) / "cycles"
        self.active_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)

    # ─── Persistence ─────────────────────────────────────────────────────────

    def _save(self, cycle: Cycle) -> None:
        path = self.active_dir / _cycle_filename(cycle.id)
        atomic_write(path, json.dumps(cycle.to_dict(), indent=2))

    def _load(self, path: Path) -> Cycle:
        with open(path) as f:
            return Cycle.from_dict(json.load(f))

    def _all_active(self) -> list[Cycle]:
        cycles: list[Cycle] = []
        for path in self.active_dir.glob("*.json"):
            try:
                cycles.append(self._load(path))
            except Exception:
                continue
        return cycles

    # ─── Public ──────────────────────────────────────────────────────────────

    def open_cycle(self, cycle_type: str, opening_artifact: Artifact,
                   participants: list[str], primary_agent: str | None = None) -> Cycle:
        # Spec §6.2 uses '-' separator: f"{cycle_type}-{opening_artifact.id}"
        cycle = Cycle(
            id=f"{cycle_type}-{opening_artifact.id}",
            type=cycle_type,
            participants=participants,
            opening_artifact=opening_artifact.id or "",
            primary_agent=primary_agent or (participants[0] if participants else None),
        )
        self._save(cycle)
        return cycle

    def close_cycle(self, cycle: Cycle) -> None:
        archive_path = self.archive_dir / _cycle_filename(cycle.id)
        atomic_write(archive_path, json.dumps(cycle.to_dict(), indent=2))
        active = self.active_dir / _cycle_filename(cycle.id)
        if active.exists():
            active.unlink()

    def get_active_cycle(self, agent_code: str, inbox_item: Artifact | None) -> Cycle | None:
        """Find the cycle this inbox item belongs to, if any."""
        if inbox_item is None:
            return None
        for cycle in self._all_active():
            if not cycle.involves(agent_code):
                continue
            # Cycle matches if the inbox item references the opening artifact or any
            # subsequent artifact in the cycle, OR if the cycle is the active one
            # between this pair and the artifact's sender is the other participant.
            if cycle.opening_artifact and cycle.opening_artifact in (inbox_item.references or []):
                return cycle
            if inbox_item.sender in cycle.participants and agent_code in cycle.participants:
                # Heuristic — for SG↔SE etc., the pair is dedicated.
                # de_qa and gov_exchange are included so that if a DE_IN /
                # GOV-exchange-continuation arrives without a references
                # field (older clients), the cycle still gets matched.
                if cycle.type in {
                    "scn_application", "tcn_application", "prop_exchange",
                    "build_scope_qa", "build_test_qa", "build_results", "build_remediation",
                    "de_qa", "gov_exchange",
                }:
                    return cycle
        return None

    def get_cycle_by_participants(self, *agents: str) -> Cycle | None:
        agents_set = set(agents)
        for cycle in self._all_active():
            if set(cycle.participants) == agents_set:
                return cycle
        return None

    def get_active_by_artifact(self, artifact_id: str) -> Cycle | None:
        """Find the active cycle opened by `artifact_id` (Spec §6.2). Used by the
        cycle-internal exchange dispatch (vega_exchange, §12.3) to attach a turn
        to the right cycle from just the opening artifact's id."""
        for cycle in self._all_active():
            if cycle.opening_artifact == artifact_id:
                return cycle
        return None

    def list_active(self) -> list[str]:
        """Sorted ids of all active cycles (NEW-2 — encapsulates the active_dir
        glob that previously lived inline in mcp_server.vega_cycles)."""
        return sorted(p.stem for p in self.active_dir.glob("*.json"))

    def get_messages(self, artifact_id: str) -> list[dict] | None:
        """Return the messages array for the cycle opened by `artifact_id`
        (Spec §12.3 — vega_cycles detail mode). None if no such active cycle."""
        cycle = self.get_active_by_artifact(artifact_id)
        return list(cycle.messages) if cycle is not None else None

    def get_by_id(self, cycle_id: str) -> Cycle | None:
        """Load an active cycle by its id (e.g. for execute_cycle_turn dispatch)."""
        path = self.active_dir / _cycle_filename(cycle_id)
        if not path.exists():
            return None
        try:
            return self._load(path)
        except Exception:
            return None

    def get_active_by_type(self, cycle_type: str) -> Cycle | None:
        for cycle in self._all_active():
            if cycle.type == cycle_type:
                return cycle
        return None

    # ─── Activity / pending / history (Spec §6.2 — DE/EXT + OP visibility) ────

    ROLE_PARTICIPANTS = {"DE": ["SG", "DE"], "EXT": ["BR", "EXT"]}

    def _load_archived_cycles(self, limit: int = 20) -> list[dict]:
        """Most-recently-archived cycle dicts (raw, no Cycle reconstruction)."""
        out: list[dict] = []
        for path in sorted(self.archive_dir.glob("*.json"), reverse=True)[:limit]:
            try:
                out.append(json.loads(path.read_text()))
            except Exception:
                continue
        return out

    def get_activity_summary(self, cycle_type: str) -> list[dict]:
        """Summaries of recent cycles of a type (active + up to 10 archived).
        Backs vega_de_activity / vega_ext_activity (OP read-only visibility)."""
        summaries: list[dict] = []
        for cycle in self._all_active():
            if cycle.type == cycle_type:
                summaries.append({
                    "id": cycle.id, "status": "active",
                    "turns": len(cycle.messages), "opened": cycle.opened_at,
                })
        for cycle in self._load_archived_cycles():
            if cycle.get("type") == cycle_type:
                summaries.append({
                    "id": cycle.get("id"), "status": "closed",
                    "turns": len(cycle.get("messages", [])),
                    "opened": cycle.get("opened_at"),
                })
                if len(summaries) >= 10:
                    break
        return summaries

    def get_pending_for_role(self, role: str) -> list[Cycle]:
        """Active cycles awaiting a response from this role (DE/EXT). A cycle is
        pending for the role when its trailing turn came from the other party."""
        participants = set(self.ROLE_PARTICIPANTS.get(role, []))
        pending: list[Cycle] = []
        for cycle in self._all_active():
            if participants and participants.issubset(set(cycle.participants)):
                last = cycle.messages[-1] if cycle.messages else None
                # The other party speaks as the assistant turn into the cycle;
                # an open user turn means the role itself still owes a reply.
                if last is not None and last.get("role") == "assistant":
                    pending.append(cycle)
        return pending

    def get_history_for_role(self, role: str) -> list[dict]:
        """Closed (archived) cycles involving this role — backs *_history tools."""
        participants = set(self.ROLE_PARTICIPANTS.get(role, []))
        history: list[dict] = []
        for cycle in self._load_archived_cycles():
            if participants and participants.issubset(set(cycle.get("participants", []))):
                history.append(cycle)
        return history

    # ─── Event detection — open/close cycles on artifact produce ─────────────

    def check_cycle_events(self, artifact: Artifact, recipients: list[str]) -> None:
        """Called by the main loop after an artifact is produced (before routing)."""
        t = artifact.type
        s = artifact.sender

        # ─── Triage close runs BEFORE per-type handlers (no early return) ────
        # Spec §6.1 — triage opens on TFR-BTA, closes on TG producing TRI/ESC/TCN.
        # Must run before TCN/TRI handlers below, which return early.
        if t in {"TRI", "ESC", "TCN"} and s == "TG":
            triage = self.get_active_by_type("triage")
            if triage:
                self.close_cycle(triage)
            # No early return — this is intentional, NOT a fall-through bug.
            # Closing triage is a side-effect; the per-type handlers below
            # are responsible for the *next* cycle in the chain:
            #   - TCN → opens tcn_application (TG↔TE)
            #   - TRI → opens build_remediation (BR↔EXT)
            #   - ESC → terminal, no follow-up cycle opens

        # SCN: opens scn_application SG↔SE
        if t == "SCN" and s == "SG":
            self.open_cycle("scn_application", artifact, ["SG", "SE"], primary_agent="SG")
            return

        # VAL from SG (scope): closes scn_application
        if t == "VAL" and s == "SG":
            cycle = self.get_cycle_by_participants("SG", "SE")
            if cycle:
                self.close_cycle(cycle)
            return

        # TCN: opens tcn_application TG↔TE (triage already closed above)
        if t == "TCN" and s == "TG":
            self.open_cycle("tcn_application", artifact, ["TG", "TE"], primary_agent="TG")
            return

        # VAL from TG: only the build certificate closes the cycle.
        # Spec v4 §6.1 + Framework v5 §1: TG VAL carries certificate ∈ {full, build}.
        #   full  = approves the test model (TE generates build version next).
        #   build = approves the build-version test model (cycle closes).
        # "second" is preserved as an alias for back-compat with older
        # artifacts in the archive from before the v4 renaming.
        if t == "VAL" and s == "TG":
            if artifact.certificate in {"build", "second"}:
                cycle = self.get_cycle_by_participants("TG", "TE")
                if cycle:
                    self.close_cycle(cycle)
            return

        # PROP from SG: opens prop_exchange SG↔OP
        if t == "PROP" and s == "SG":
            self.open_cycle("prop_exchange", artifact, ["SG", "OP"], primary_agent="SG")
            return

        # AUTH from OP: closes prop_exchange
        if t == "AUTH" and s == "OP":
            cycle = self.get_cycle_by_participants("SG", "OP")
            if cycle:
                self.close_cycle(cycle)
            return

        # PRO-SCOPE arriving at BR → opens build_scope_qa; supersedes any prior one
        if t == "PRO-SCOPE" and "BR" in recipients:
            old = self.get_active_by_type("build_scope_qa")
            if old:
                self.close_cycle(old)
            self.open_cycle("build_scope_qa", artifact, ["BR", "EXT"], primary_agent="BR")
            return

        # PRO-TEST-BUILD arriving at BR → opens build_test_qa; supersedes any prior one
        if t == "PRO-TEST-BUILD" and "BR" in recipients:
            old = self.get_active_by_type("build_test_qa")
            if old:
                self.close_cycle(old)
            self.open_cycle("build_test_qa", artifact, ["BR", "EXT"], primary_agent="BR")
            return

        # TFR from BTA → TG opens triage (Spec §6.1)
        # Triage is a TG-internal cycle; participants are [TG] alone.
        if t == "TFR" and s == "BTA":
            self.open_cycle("triage", artifact, ["TG"], primary_agent="TG")
            return

        # TRI from TG → BR relays to EXT → opens build_remediation (triage already closed)
        if t == "TRI" and s == "TG":
            self.open_cycle("build_remediation", artifact, ["BR", "EXT"],
                            primary_agent="BR")
            return

        # BRQ from EXT (arrives at BR via /build relay) → opens build_results.
        # Spec §6.1: opens on "BRQ (results) received from EXT".
        if t == "BRQ" and s == "EXT":
            old = self.get_active_by_type("build_results")
            if old:
                self.close_cycle(old)
            self.open_cycle("build_results", artifact, ["BR", "EXT"], primary_agent="BR")
            return

        # VR from BTA: close build_scope_qa / build_test_qa / build_results for that version
        # (Spec §6.1: "VR-BTA-NNN relayed to EXT" closes build_results. We close on VR
        # arrival at BR since the relay is implicit at the operator level.)
        if t == "VR" and s == "BTA":
            for cycle_type in ("build_results", "build_scope_qa", "build_test_qa"):
                cycle = self.get_active_by_type(cycle_type)
                if cycle:
                    self.close_cycle(cycle)
            return

        # DE Q&A cycle (Spec v4 §6.1) — opens on DE_OUT-SG-NNN, closes on DE_IN.
        # Per D-ARCH-035 SG is non-blocking on DE: many DE_OUT can be in flight,
        # so cycle id includes the opening artifact id (already done in
        # open_cycle) and DE_IN matches via the references field.
        if t == "DE_OUT" and s == "SG":
            self.open_cycle("de_qa", artifact, ["SG", "DE"], primary_agent="SG")
            return
        if t == "DE_IN" and s == "DE":
            ref_target = (artifact.references or [None])[0]
            closed = False
            if ref_target:
                expected_id = f"de_qa-{ref_target}"
                for active in self._all_active():
                    if active.id == expected_id:
                        self.close_cycle(active)
                        closed = True
                        break
            if not closed:
                # Fallback: no references field → close the oldest active de_qa.
                cycle = self.get_active_by_type("de_qa")
                if cycle:
                    self.close_cycle(cycle)
            return

        # GOV exchange cycle (Spec v4 §6.1) — opens on GOV-SYS-NNN. Closes
        # explicitly via /resolve → close_by_artifact (so OP can have a
        # multi-turn dialogue with SYS across audits before resolving).
        if t == "GOV" and s == "SYS":
            self.open_cycle("gov_exchange", artifact, ["SYS", "OP"],
                            primary_agent="SYS")
            return

    # ─── Cycle continuation context monitoring ───────────────────────────────

    def append_turn(self, cycle: Cycle, user_msg, assistant_msg=None) -> None:
        """Append turn(s) to a cycle. Two shapes (Spec §6.2-§6.4):

        - Pair shape — ``append_turn(cycle, user_dict, assistant_dict)``: records
          a full request/response turn. Used by the executor after a normal
          cycle execution (one user message + one assistant message).

        - Single cycle-internal exchange turn — ``append_turn(cycle, text, role)``:
          records ONE user turn (Spec §6.4 / §10.2 / §12.3). `text` is a str and
          the third positional arg is the role label (e.g. "OP", "ADMIN_OP").
          Used by the Telegram/MCP freeform handlers so an exchange turn is added
          to the cycle's messages array WITHOUT minting or routing an artifact.
          The speaker label is folded into the message text so it survives in the
          cycle archive for SYS audit (and so the partner agent sees who spoke).
        """
        if isinstance(user_msg, str):
            role = assistant_msg or "user"
            cycle.messages.append({"role": "user", "content": f"[{role}] {user_msg}"})
            self._save(cycle)
            return
        cycle.messages.append(user_msg)
        cycle.messages.append(assistant_msg)
        self._save(cycle)

    def append_assistant_turn(self, cycle: Cycle, text: str) -> None:
        """Append a single assistant turn (Spec §6.4 — the partner agent's reply
        to a cycle-internal exchange turn). Counterpart to the single-turn
        ``append_turn(cycle, text, role)`` used for the inbound side."""
        cycle.messages.append({"role": "assistant", "content": text})
        self._save(cycle)

    def compress_early_turns(self, cycle: Cycle, keep_recent: int = 6) -> int:
        """Replace early turns with a summary stub, keep most recent `keep_recent`.

        Per Spec §6.4: when cycle context > CYCLE_CONTEXT_WARNING (40%), older
        turns are replaced with a synthetic summary so the cycle can continue
        without hitting the model's context limit.

        U-RC-08 (UNIVERSAL/case_index.md): context compression can produce
        confabulation. The summary explicitly tells the agent that any claim
        about the early turns must be verified against the artifacts on disk —
        the summary itself is not a trustworthy source.

        Returns the number of turns compressed (0 if no compression was needed).
        """
        if len(cycle.messages) <= keep_recent:
            return 0
        older = cycle.messages[:-keep_recent]
        recent = cycle.messages[-keep_recent:]

        summary_lines: list[str] = []
        for msg in older:
            role = msg.get("role", "?")
            raw = msg.get("content", "")
            if isinstance(raw, list):
                raw = " ".join(
                    c.get("text", "") for c in raw if isinstance(c, dict)
                )
            snippet = (raw or "").replace("\n", " ")[:200]
            summary_lines.append(f"  [{role}] {snippet}…")

        summary_text = (
            "[CYCLE COMPRESSION — earlier turns summarized per Spec §6.4 and "
            "U-RC-08. The summary below is for context only; any factual claim "
            "about the early turns must be verified against the cycle artifacts "
            "on disk, not recalled from this summary.]\n\n"
            f"Skipped {len(older)} earlier turns:\n" + "\n".join(summary_lines)
        )
        cycle.messages = [{"role": "user", "content": summary_text}] + recent
        self._save(cycle)
        return len(older)

    def close_by_artifact(self, artifact_id: str) -> None:
        """Close the cycle opened by `artifact_id`. Used by /retry to roll back
        a cycle when its opener is re-issued.

        We match ONLY against the cycle's explicit `opening_artifact` field
        — never against a substring scan of `messages`. The old substring
        scan could close the wrong cycle if a later message merely *quoted*
        the id (e.g., "rejecting SCN-SG-012" arriving as a turn body).
        """
        for cycle in self._all_active():
            if cycle.opening_artifact == artifact_id:
                self.close_cycle(cycle)
                return

    def check_context_usage(self, cycle: Cycle, model_limit: int | None,
                            warning_fraction: float) -> dict:
        """NEW-2 / Spec §6.4 — estimate a cycle's context use and, if it exceeds
        `warning_fraction` of the model window, compress early turns. Returns a
        status dict so the caller (which owns the wiki) does the logging:
        {over, used, limit, usage_pct, compressed}. Encapsulates the estimate +
        threshold + compress logic that was duplicated inline in the executor."""
        used = self.estimate_tokens(cycle.messages)
        usage_pct = (used / model_limit) if model_limit else 0.0
        if not model_limit or usage_pct <= warning_fraction:
            return {"over": False, "used": used, "limit": model_limit,
                    "usage_pct": usage_pct, "compressed": 0}
        compressed = self.compress_early_turns(cycle)
        return {"over": True, "used": used, "limit": model_limit,
                "usage_pct": usage_pct, "compressed": compressed}

    def estimate_tokens(self, messages: list[dict]) -> int:
        """Cheap heuristic: 1 token ≈ 4 chars. Used for the 40% warning."""
        total = 0
        for m in messages:
            content = m.get("content", "")
            if isinstance(content, str):
                total += len(content) // 4
            elif isinstance(content, list):
                for c in content:
                    if isinstance(c, dict) and "text" in c:
                        total += len(c["text"]) // 4
        return total
