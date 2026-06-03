"""
Routing table.

Single source of truth derived exhaustively from Framework §2 (Interaction Catalog).
The router is a pure table lookup, except for REJ artifacts which need to consult the
archived referenced artifact to resolve ref_type.

Per Spec §4.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from artifact_store import ArtifactStore
from models import Artifact, make_gov
from state_manager import LOCKS, atomic_save_json, load_json


# ─── Agent-to-agent routes ───────────────────────────────────────────────────

ROUTING_TABLE: dict[tuple[str, ...], list[dict[str, str]]] = {
    # Scope lane (Framework §2.1)
    ("SA",  "FND"):           [{"to": "SG"}],
    ("SG",  "REJ", "FND"):    [{"to": "SA"}],          # S2: rejecting SA finding
    ("SG",  "REJ", "DEV"):    [{"to": "BR"}],          # Flow 7: rejecting BR deviation
    ("SG",  "REJ", "ESC"):    [{"to": "TG"}],          # Rejecting TG escalation
    ("SG",  "SCN"):           [{"to": "SE"}],
    ("SE",  "DOC"):           [{"to": "SG"}],
    ("SE",  "NOTE"):          [{"to": "SG"}],
    ("SG",  "VAL"):           [{"to": "SE"}],
    ("SG",  "REV"):           [{"to": "SE"}],
    ("SG",  "PRO-SCOPE"):     [{"to": "SA"}, {"to": "BR"}, {"to": "TG"}, {"to": "TA"}],
    ("SG",  "SUM"):           [{"to": "ARCHIVE"}],     # Exchange summary — archived only
    ("SG",  "DE_OUT"):        [{"to": "DE"}],          # Domain Expert (external)

    # Test lane (§2.2)
    ("TG",  "TCN"):           [{"to": "TE"}],
    ("TE",  "DOC"):           [{"to": "TG"}],
    ("TE",  "NOTE"):          [{"to": "TG"}],
    ("TG",  "VAL"):           [{"to": "TE"}],
    ("TG",  "REV"):           [{"to": "TE"}],
    ("TG",  "PRO-TEST-FULL"): [{"to": "TA"}, {"to": "BTA"}],
    ("TG",  "PRO-TEST-BUILD"):[{"to": "BR"}],
    ("TA",  "FND"):           [{"to": "TG"}],
    ("TG",  "REJ", "FND"):    [{"to": "TA"}],          # T7

    # Cross-lane (§2.3)
    ("TG",  "ESC"):           [{"to": "SG"}],

    # Build layer (§2.4)
    ("BR",  "TSR"):           [{"to": "BTA"}],
    ("BTA", "TFR"):           [{"to": "TG"}],
    ("BTA", "VR"):            [{"to": "BR"}],
    ("BR",  "DEV"):           [{"to": "SG"}],
    ("TG",  "TRI"):           [{"to": "BR"}],

    # External Build (§2.5)
    ("BR",  "BRP"):           [{"to": "EXT"}],
    # BRQ from EXT (injected via OP /build relay) — delivered to BR.
    # Routing through the table ensures archive + routing_log inclusion.
    ("EXT", "BRQ"):           [{"to": "BR"}],

    # Domain Expert (Spec v4 §2.6 + §4.1) — both directions go through router.
    # DE_IN arrives via OP /expert relay (Spec §12.4); routing here guarantees
    # archive + routing_log instead of a silent direct-inbox bypass.
    ("DE",  "DE_IN"):         [{"to": "SG"}],

    # System Auditor (§2.7)
    ("SYS", "GOV"):           [{"to": "OP"}],

    # AUTH from OP (Spec §7.2, §13 step 4) — archived immutably and delivered to SG.
    # Routing through the table ensures appearance in routing_log + artifacts/archive.
    ("OP",  "AUTH"):          [{"to": "SG"}],

    # Operator Request (Spec v4 §2.1 S13 + §4.1) — ad-hoc scope work / change
    # request / question from OP to SG. Triggered by /request or vega_request.
    ("OP",  "REQ"):           [{"to": "SG"}],

    # NOTE: there is intentionally NO ("OP", "PROP") route. PROP exchange
    # continuation turns are cycle-internal per Spec v5 §6.4 (audit P1-2) —
    # appended to the prop_exchange cycle's messages array and dispatched via
    # flag_for_execution, never minted as artifacts or routed. The old workaround
    # route existed only to suppress auto-GOV for the (now removed) deviation.
}


# ─── OP-bound types (non-blocking) ───────────────────────────────────────────

OP_BOUND_TYPES: dict[tuple[str, str], dict[str, Any]] = {
    ("SG",  "PROP"): {
        "queue": "pending",
        "notify": True,
        "priority_field": True,
        "exchange_mode": True,
        "exchange_partner": "SG",
    },
    ("SYS", "GOV"): {
        "queue": "pending",
        "notify": True,
        "priority_field": False,
        "exchange_mode": False,
        "exchange_partner": None,
    },
}


# ─── External targets ────────────────────────────────────────────────────────
#
# Sentinels recognized in the ROUTING_TABLE's `to` field.
#
#   EXT     — external relay (BR ↔ EXT build channel, Spec §12.1)
#   DE      — domain expert relay (SG → DE, Spec §12.2)
#   ARCHIVE — archive-only, no recipient inbox (SG SUM)
#
# "OP" is NOT in this set. OP routing happens BEFORE the ROUTING_TABLE loop
# via OP_BOUND_TYPES (SG/PROP and SYS/GOV) which calls _route_to_op directly.
# Listing OP here would create unreachable dead code inside the loop.
EXTERNAL_TARGETS = {"EXT", "DE", "ARCHIVE"}


# ─── Router ──────────────────────────────────────────────────────────────────

class Router:

    def __init__(self, store: ArtifactStore, state_dir: str | Path,
                 op_backlog=None, telegram_bot=None, cycles=None) -> None:
        self.store = store
        self.routing_log = Path(state_dir) / "routing_log.json"
        self.op_backlog = op_backlog
        self.telegram_bot = telegram_bot
        # CycleManager — used to stamp routing_log entries with the active
        # cycle id (audit NEW-1; Spec §17.2). Optional so the Router can still
        # be constructed in tests/bootstrap before cycles exist.
        self.cycles = cycles

    def _cycle_id_for(self, artifact: Artifact,
                      recipients: list[str] | None = None) -> str | None:
        """Best-effort active-cycle lookup for routing_log stamping (audit NEW-1,
        Spec §17.2). Returns None when no CycleManager is wired or no active
        cycle involves this artifact. Tries the recipients first (the artifact
        is usually a turn *into* a cycle), then the sender."""
        if self.cycles is None:
            return None
        for agent in list(recipients or []) + [artifact.sender]:
            if not agent:
                continue
            cycle = self.cycles.get_active_cycle(agent, artifact)
            if cycle:
                return cycle.id
        return None

    def _key_for(self, artifact: Artifact) -> tuple[str, ...]:
        if artifact.type == "REJ":
            return (artifact.sender, "REJ", artifact.ref_type or "")
        return (artifact.sender, artifact.type)

    def _resolve_ref_type(self, artifact: Artifact) -> str | None:
        """For REJ, read the referenced artifact's type from the archive."""
        if artifact.type != "REJ":
            return artifact.ref_type
        if artifact.ref_type:
            return artifact.ref_type
        if not artifact.references:
            return None
        ref_id = artifact.references[0]
        referenced = self.store.archive.load(ref_id)
        if referenced is None:
            return None
        return referenced.type

    async def route(self, artifact: Artifact) -> None:
        """Place artifact in each recipient's inbox, archive immutably, log."""
        # Resolve REJ ref_type if not set
        if artifact.type == "REJ" and not artifact.ref_type:
            artifact.ref_type = self._resolve_ref_type(artifact)

        op_key = (artifact.sender, artifact.type)
        if op_key in OP_BOUND_TYPES:
            await self._route_to_op(artifact, OP_BOUND_TYPES[op_key])
            self.store.archive_artifact(artifact)
            await self._append_log(artifact, routed_to=["OP"],
                                   cycle_id=self._cycle_id_for(artifact, ["OP"]))
            return

        key = self._key_for(artifact)
        if key not in ROUTING_TABLE:
            await self._handle_unknown(artifact, key)
            return

        recipients: list[str] = []
        for route in ROUTING_TABLE[key]:
            to = route["to"]
            recipients.append(to)
            if to == "ARCHIVE":
                continue
            if to in EXTERNAL_TARGETS:
                await self._route_external(artifact, to)
                continue
            self.store.inbox(to).deliver(artifact)

        # Archive after delivery
        self.store.archive_artifact(artifact)
        await self._append_log(artifact, routed_to=recipients,
                               cycle_id=self._cycle_id_for(artifact, recipients))

    async def _route_to_op(self, artifact: Artifact, spec: dict[str, Any]) -> None:
        """Place in OP backlog; attempt Telegram notify. Notify failures must
        NOT break routing — the artifact's primary delivery is the op_backlog
        file (which OP can /backlog to see). Telegram is a notification
        channel, not the source of truth."""
        if self.op_backlog is not None:
            self.op_backlog.add(artifact)
        if spec.get("notify") and self.telegram_bot is not None:
            try:
                await self.telegram_bot.notify(artifact)
            except Exception as e:
                print(f"[router] Telegram notify failed for {artifact.id}: "
                      f"{type(e).__name__}: {e}", flush=True)

    async def _route_external(self, artifact: Artifact, target: str) -> None:
        """EXT and DE communications. At launch: OP relay (Spec §12.1, §12.2)."""
        if self.telegram_bot is not None:
            try:
                await self.telegram_bot.notify_external_relay(artifact, target)
            except Exception as e:
                print(f"[router] Telegram relay notify failed for {artifact.id}: "
                      f"{type(e).__name__}: {e}", flush=True)

    async def _handle_unknown(self, artifact: Artifact, key: tuple[str, ...]) -> None:
        """Unknown routing key — auto-create GOV-SYS, route to OP (Spec §4.3 rule 8)."""
        reason = f"Unknown routing key: {key}. Artifact {artifact.id} not delivered."
        gov = make_gov(reason=reason, references=[artifact.id] if artifact.id else [])
        gov.priority = "P1"
        if self.op_backlog is not None:
            self.op_backlog.add(gov)
        if self.telegram_bot is not None:
            # Audit NEW-3 / Spec §10.2: Telegram notify failures must NOT break
            # archival or routing_log of the offending artifact. _route_to_op
            # already guards its notify; _handle_unknown must too.
            try:
                await self.telegram_bot.notify(gov)
            except Exception as e:
                print(f"[router] Telegram notify failed for auto-GOV {gov.id}: "
                      f"{type(e).__name__}: {e}", flush=True)
        self.store.archive_artifact(artifact)
        await self._append_log(artifact, routed_to=["GOV_VIOLATION"],
                               cycle_id=self._cycle_id_for(artifact))

    async def _append_log(self, artifact: Artifact, routed_to: list[str],
                          cycle_id: str | None = None) -> None:
        """Append a routing entry.

        Spec §16.1 — routing_log.json requires asyncio.Lock.
        Spec §17.2 — entry shape includes cycle_id (null for non-cycle artifacts).
        """
        async with LOCKS.get("routing_log"):
            entries: list[dict[str, Any]] = load_json(self.routing_log, default=[])
            if not isinstance(entries, list):
                entries = []
            entries.append({
                "timestamp": artifact.timestamp,
                "artifact_id": artifact.id,
                "sender": artifact.sender,
                "sender_instance": artifact.sender_instance,
                "sender_model": artifact.sender_model,
                "type": artifact.type,
                "ref_type": artifact.ref_type,
                "routed_to": routed_to,
                "cycle_id": cycle_id,
                "archived": True,
            })
            atomic_save_json(self.routing_log, entries)
