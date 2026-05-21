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

    # System Auditor (§2.7)
    ("SYS", "GOV"):           [{"to": "OP"}],

    # AUTH from OP (Spec §7.2, §13 step 4) — archived immutably and delivered to SG.
    # Routing through the table ensures appearance in routing_log + artifacts/archive.
    ("OP",  "AUTH"):          [{"to": "SG"}],
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

EXTERNAL_TARGETS = {"EXT", "DE", "OP", "ARCHIVE"}


# ─── Router ──────────────────────────────────────────────────────────────────

class Router:

    def __init__(self, store: ArtifactStore, state_dir: str | Path,
                 op_backlog=None, telegram_bot=None) -> None:
        self.store = store
        self.routing_log = Path(state_dir) / "routing_log.json"
        self.op_backlog = op_backlog
        self.telegram_bot = telegram_bot

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
            await self._append_log(artifact, routed_to=["OP"])
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
        await self._append_log(artifact, routed_to=recipients)

    async def _route_to_op(self, artifact: Artifact, spec: dict[str, Any]) -> None:
        if self.op_backlog is not None:
            self.op_backlog.add(artifact)
        if spec.get("notify") and self.telegram_bot is not None:
            await self.telegram_bot.notify(artifact)

    async def _route_external(self, artifact: Artifact, target: str) -> None:
        """EXT and DE communications. At launch: OP relay (Spec §12.1, §12.2)."""
        if self.telegram_bot is not None:
            await self.telegram_bot.notify_external_relay(artifact, target)

    async def _handle_unknown(self, artifact: Artifact, key: tuple[str, ...]) -> None:
        """Unknown routing key — auto-create GOV-SYS, route to OP (Spec §4.3 rule 8)."""
        reason = f"Unknown routing key: {key}. Artifact {artifact.id} not delivered."
        gov = make_gov(reason=reason, references=[artifact.id] if artifact.id else [])
        gov.priority = "P1"
        if self.op_backlog is not None:
            self.op_backlog.add(gov)
        if self.telegram_bot is not None:
            await self.telegram_bot.notify(gov)
        self.store.archive_artifact(artifact)
        await self._append_log(artifact, routed_to=["GOV_VIOLATION"])

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
