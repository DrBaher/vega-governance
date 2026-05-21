"""
Conversation cycle management.

Per Spec §6. A cycle is a bounded multi-turn interaction whose messages array provides
conversational coherence across executions. The array is discarded when the cycle closes.

Cycle types (Spec §6.1):
  scn_application   SG ↔ SE   opens on SCN, closes on VAL
  tcn_application   TG ↔ TE   opens on TCN, closes on VAL(certificate=second)
  prop_exchange     SG ↔ OP   opens on PROP, closes on AUTH
  triage            TG int.   opens on TFR, closes on TRI/ESC/TCN
  build_scope_qa    BR ↔ EXT  opens on PRO-SCOPE@BR, closes on VR or supersession
  build_test_qa     BR ↔ EXT  opens on PRO-TEST-BUILD@BR, closes on VR or supersession
  build_results     BR ↔ EXT  opens on BRQ-results, closes on VR relayed
  build_remediation BR ↔ EXT  opens on TRI relayed to EXT, closes on new results
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
                if cycle.type in {
                    "scn_application", "tcn_application", "prop_exchange",
                    "build_scope_qa", "build_test_qa", "build_results", "build_remediation",
                }:
                    return cycle
        return None

    def get_cycle_by_participants(self, *agents: str) -> Cycle | None:
        agents_set = set(agents)
        for cycle in self._all_active():
            if set(cycle.participants) == agents_set:
                return cycle
        return None

    def get_active_by_type(self, cycle_type: str) -> Cycle | None:
        for cycle in self._all_active():
            if cycle.type == cycle_type:
                return cycle
        return None

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
            # Fall through — TCN still opens tcn_application; TRI still opens
            # build_remediation; ESC has no follow-up cycle open.

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

        # VAL from TG: only the second certificate closes the cycle
        if t == "VAL" and s == "TG":
            if artifact.certificate == "second":
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

    # ─── Cycle continuation context monitoring ───────────────────────────────

    def append_turn(self, cycle: Cycle, user_msg: dict, assistant_msg: dict) -> None:
        cycle.messages.append(user_msg)
        cycle.messages.append(assistant_msg)
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
        for cycle in self._all_active():
            if cycle.opening_artifact == artifact_id or artifact_id in (
                # quick scan of any messages content for the id
                json.dumps(cycle.messages) if cycle.messages else ""
            ):
                self.close_cycle(cycle)
                return

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
