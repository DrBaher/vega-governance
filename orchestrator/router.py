"""
Routing table.

Single source of truth derived exhaustively from Framework §2 (Interaction Catalog).
The router is a pure table lookup, except for REJ artifacts which need to consult the
archived referenced artifact to resolve ref_type.

Per Spec §4.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from artifact_store import ArtifactStore, parse_edit_ops, parse_file_sections
from models import Artifact, make_gov
from state_manager import LOCKS, atomic_save_json, atomic_write, load_json


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

    # ── Backlog-bound routes (Spec v5 §4.1-4.2) — a route with a `backlog` key
    # goes to the decision role's backlog (not an agent inbox) + notifies the
    # role. PROP → OP, GOV → Admin OP. Merged into the single table (SC: the old
    # OP_BOUND_TYPES / ADMIN_BOUND_TYPES split is eliminated).
    ("SG",  "PROP"):          [{"to": "OP", "backlog": "op_backlog/pending",
                                "notify_role": "OP", "exchange_mode": True,
                                "exchange_partner": "SG"}],
    # System Auditor (§2.7) — GOV is governance → Admin OP backlog.
    ("SYS", "GOV"):           [{"to": "ADMIN_OP", "backlog": "admin_backlog/pending",
                                "notify_role": "ADMIN_OP", "exchange_mode": True,
                                "exchange_partner": "SYS"}],

    # AUTH from OP (Spec §7.2, §13 step 4) — archived immutably and delivered to SG.
    # Routing through the table ensures appearance in routing_log + artifacts/archive.
    ("OP",  "AUTH"):          [{"to": "SG"}],

    # Operator Request (Spec v4 §2.1 S13 + §4.1) — ad-hoc scope work / change
    # request / question from OP to SG. Triggered by /request or vega_request.
    ("OP",  "REQ"):           [{"to": "SG"}],

    # NOTE: there is intentionally NO ("OP", "PROP") route. PROP exchange
    # continuation turns are cycle-internal per Spec v5 §6.4 (audit P1-2) —
    # appended to the prop_exchange cycle's messages array and dispatched via
    # flag_for_execution, never minted as artifacts or routed.
}


# ─── External targets ────────────────────────────────────────────────────────
#
# Sentinels recognized in the ROUTING_TABLE's `to` field.
#
#   EXT     — external relay (BR ↔ EXT build channel, Spec §12.1)
#   DE      — domain expert relay (SG → DE, Spec §12.2)
#   ARCHIVE — archive-only, no recipient inbox (SG SUM)
#
# Backlog routes (PROP→OP, GOV→ADMIN_OP) are NOT here — they carry a `backlog`
# key on the route dict and are handled inside the table loop in route().
EXTERNAL_TARGETS = {"EXT", "DE", "ARCHIVE"}


# ─── Router ──────────────────────────────────────────────────────────────────

class Router:

    def __init__(self, store: ArtifactStore, state_dir: str | Path,
                 op_backlog=None, telegram_bot=None, cycles=None,
                 admin_backlog=None, config=None) -> None:
        self.store = store
        self.routing_log = Path(state_dir) / "routing_log.json"
        self.op_backlog = op_backlog
        # Spec v5 §4.2 / §10.1 — separate Admin OP backlog for GOV + role reqs.
        self.admin_backlog = admin_backlog
        self.telegram_bot = telegram_bot
        # CycleManager — used to stamp routing_log entries with the active
        # cycle id (audit NEW-1; Spec §17.2). Optional so the Router can still
        # be constructed in tests/bootstrap before cycles exist.
        self.cycles = cycles
        # Config — provides SCOPE_DIR / TEST_MODELS_* for the scope/test file
        # commit on PRO-SCOPE/PRO-TEST routing (Spec sc4 §4.3). Optional so the
        # Router can be constructed without it (commit then no-ops).
        self.config = config

    def _backlog_for(self, route: dict[str, Any]):
        """Pick the decision backlog a backlog-route targets (Spec §4.2)."""
        bl = route.get("backlog", "") or ""
        if bl.startswith("admin_backlog") or route.get("notify_role") == "ADMIN_OP":
            return self.admin_backlog
        return self.op_backlog

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
        referenced = self.store.load_from_archive(ref_id)
        if referenced is None:
            return None
        return referenced.type

    def _propagation_target_dir(self, artifact_type: str) -> str | None:
        """Target dir for a propagation commit (Spec sc4 §4.3), or None if this
        type doesn't commit files / no config is wired."""
        cfg = self.config
        if cfg is None:
            return None
        if artifact_type == "PRO-SCOPE":
            return getattr(cfg, "SCOPE_DIR", None)
        if artifact_type == "PRO-TEST-FULL":
            return getattr(cfg, "TEST_MODELS_FULL_DIR", None)
        if artifact_type == "PRO-TEST-BUILD":
            return getattr(cfg, "TEST_MODELS_BUILD_DIR", None)
        return None

    @staticmethod
    def _unsafe_filename(name: str) -> bool:
        """Reject path traversal / nested paths — commit only flat filenames."""
        return "/" in name or "\\" in name or ".." in name

    async def commit_propagation_files(self, artifact: Artifact) -> None:
        """Spec sc4 §4.3 — write scope/test files from the referenced (validated,
        archived) DOC to disk. THE ONLY code path that writes to scope/ or
        test_models/. Two DOC modes:
          • `### FILE:` — full-file replacement (new files / full rewrites).
          • `### EDIT:` — surgical find/replace ops applied to the LIVE file, so a
            large doc isn't re-emitted to change a few lines (B / §4.3 patch mode).
        Both use write-ahead staging (`<name>.incoming` → `os.replace`, atomic per
        file). An EDIT whose anchor is missing or ambiguous (≠1 match) is REJECTED
        for that file (no write) rather than guessing — no partial/where-wrong edits.
        Never raises: a commit failure is logged for SYS but must not break routing."""
        target_dir = self._propagation_target_dir(artifact.type)
        if target_dir is None:
            return
        try:
            if not artifact.references:
                print(f"[router] propagation_commit: {artifact.type} {artifact.id} "
                      f"has no referenced DOC — nothing committed", flush=True)
                return
            doc_id = artifact.references[0]
            doc = self.store.load_from_archive(doc_id)
            if doc is None:
                print(f"[router] propagation_commit: DOC {doc_id} not in archive "
                      f"(for {artifact.id}) — nothing committed", flush=True)
                return
            files = parse_file_sections(doc.content)
            edit_ops = parse_edit_ops(doc.content)
            if not files and not edit_ops:
                print(f"[router] propagation_commit: no ### FILE: or ### EDIT: markers "
                      f"in {doc_id} (for {artifact.id}) — nothing committed", flush=True)
                return
            target = Path(target_dir)
            target.mkdir(parents=True, exist_ok=True)

            # ── Full-file mode (### FILE:) ──
            full_staged: list[str] = []
            for filename, content in files.items():
                if self._unsafe_filename(filename):
                    print(f"[router] propagation_commit: rejecting unsafe filename "
                          f"{filename!r} in {doc_id}", flush=True)
                    continue
                body = content if content.endswith("\n") else content + "\n"
                atomic_write(target / f"{filename}.incoming", body)
                full_staged.append(filename)
            for filename in full_staged:
                os.replace(target / f"{filename}.incoming", target / filename)

            # ── Surgical mode (### EDIT:) — apply ops in-place against the LIVE file ──
            edits_by_file: dict[str, list[tuple[str, str]]] = {}
            for filename, find, replace in edit_ops:
                if self._unsafe_filename(filename):
                    print(f"[router] propagation_commit: rejecting unsafe filename "
                          f"{filename!r} in {doc_id}", flush=True)
                    continue
                edits_by_file.setdefault(filename, []).append((find, replace))
            for filename, ops in edits_by_file.items():
                path = target / filename
                if not path.exists():
                    print(f"[router] propagation_commit: EDIT target {filename} "
                          f"missing (for {doc_id}) — skipped, no write", flush=True)
                    continue
                text = path.read_text()
                ok = True
                for find, replace in ops:
                    n = text.count(find)
                    if n != 1:
                        print(f"[router] propagation_commit: EDIT anchor in {filename} "
                              f"matched {n}× (need exactly 1) for {doc_id} — file "
                              f"REJECTED, no write", flush=True)
                        ok = False
                        break
                    text = text.replace(find, replace, 1)
                if not ok:
                    continue   # leave the file untouched — never a partial apply
                atomic_write(target / f"{filename}.incoming", text)
                os.replace(target / f"{filename}.incoming", target / filename)
        except Exception as e:  # never break routing on a commit failure
            print(f"[router] propagation_commit FAILED for {artifact.id} "
                  f"({artifact.type}): {type(e).__name__}: {e}", flush=True)

    async def route(self, artifact: Artifact) -> None:
        """Place artifact in each recipient's inbox, archive immutably, log."""
        # Resolve REJ ref_type if not set
        if artifact.type == "REJ" and not artifact.ref_type:
            artifact.ref_type = self._resolve_ref_type(artifact)

        key = self._key_for(artifact)
        if key not in ROUTING_TABLE:
            await self._handle_unknown(artifact, key)
            return

        # Spec sc4 §4.3 — commit scope/test files from the referenced DOC to disk
        # BEFORE delivering PRO-SCOPE/PRO-TEST to downstream inboxes, so those
        # agents read the updated files on their next execution. No-op for other
        # artifact types. Never raises.
        await self.commit_propagation_files(artifact)

        # Single table loop (Spec §4.1-4.2). A route with a `backlog` key goes to
        # a decision backlog + notify; otherwise inbox / external / archive.
        recipients: list[str] = []
        for route in ROUTING_TABLE[key]:
            to = route["to"]
            recipients.append(to)
            if "backlog" in route:
                await self._route_to_backlog(artifact, route, self._backlog_for(route))
            elif to == "ARCHIVE":
                continue
            elif to in EXTERNAL_TARGETS:
                await self._route_external(artifact, to)
            else:
                self.store.inbox(to).deliver(artifact)

        # Archive after delivery
        self.store.archive_artifact(artifact)
        await self._append_log(artifact, routed_to=recipients,
                               cycle_id=self._cycle_id_for(artifact, recipients))

    async def _route_to_backlog(self, artifact: Artifact, route: dict[str, Any],
                                backlog) -> None:
        """Place in the given decision backlog (OP or Admin OP); attempt a
        role-targeted Telegram notify. Notify failures must NOT break routing —
        the artifact's primary delivery is the backlog file (which the role can
        see via /backlog). Telegram is a notification channel, not the source of
        truth (Spec §10.2)."""
        if backlog is not None:
            backlog.add(artifact)
        role = route.get("notify_role")
        if role and self.telegram_bot is not None:
            try:
                await self.telegram_bot.notify(artifact, role=role)
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
        """Unknown routing key — auto-create GOV-SYS and route to the Admin OP
        backlog (Spec v5 §4.3 rule 8: GOV is governance → admin_backlog). Falls
        back to op_backlog only if no admin_backlog is wired."""
        reason = f"Unknown routing key: {key}. Artifact {artifact.id} not delivered."
        gov = make_gov(reason=reason, references=[artifact.id] if artifact.id else [])
        gov.priority = "P1"
        # Derive backlog + notify_role from the GOV route entry (NEW-7) instead of
        # hardcoding, so it stays aligned with the merged table.
        gov_route = (ROUTING_TABLE.get(("SYS", "GOV")) or [{}])[0]
        target_backlog = self._backlog_for(gov_route) or self.op_backlog
        notify_role = gov_route.get("notify_role", "ADMIN_OP")
        if target_backlog is not None:
            target_backlog.add(gov)
        if self.telegram_bot is not None:
            # Audit NEW-3 / Spec §10.2: Telegram notify failures must NOT break
            # archival or routing_log of the offending artifact.
            try:
                await self.telegram_bot.notify(gov, role=notify_role)
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
