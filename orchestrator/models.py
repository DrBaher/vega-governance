"""
VEGA data models.

Lightweight dataclasses + YAML frontmatter (de)serialization. No Pydantic — keep the
orchestrator's runtime surface small. Validation is structural (parse failures raise).

References:
- Spec §7.1  Artifact format on disk
- Spec §7.2  AUTH/SUM split
- Spec §5.3  Output blocks (ARTIFACT, WIKI_UPDATE, LOG_ENTRY)
- Spec §6.2  Cycle dataclass
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

import yaml


# ─── Constants ───────────────────────────────────────────────────────────────

PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
PRIORITY_EMOJI = {"P0": "🔴", "P1": "🟠", "P2": "🟡", "P3": "🟢"}

# The full set of document type codes from Framework §1.
# Used for validation and for the routing table key tuples.
DOCUMENT_TYPES = {
    # Formal change vehicles
    "SCN", "TCN",
    # Findings and reports
    "FND", "VR", "TFR", "ESC", "DEV",
    # Workflow
    "DOC", "VAL", "REV", "REJ", "TRI", "BRQ", "BRP", "TSR", "NOTE",
    "PROP", "AUTH", "SUM", "GOV", "REQ",
    # External
    "DE_OUT", "DE_IN", "DE_SG",
    # Propagation
    "PRO-SCOPE", "PRO-TEST-BUILD", "PRO-TEST-FULL",
    # Bootstrap
    "INIT",
}

AGENT_CODES = {"SG", "SA", "SE", "TG", "TA", "TE", "BR", "BTA", "SYS", "OP", "EXT", "DE"}


# ─── Helpers ─────────────────────────────────────────────────────────────────

def utcnow_iso() -> str:
    """UTC timestamp in ISO 8601 with Z suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse '---\\n<yaml>\\n---\\n<body>' into (metadata, body)."""
    if not text.startswith("---"):
        raise ValueError("Artifact missing YAML frontmatter")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ValueError("Artifact frontmatter not terminated by '---'")
    meta = yaml.safe_load(parts[1]) or {}
    body = parts[2].lstrip("\n")
    return meta, body


def _emit_frontmatter(meta: dict[str, Any], body: str) -> str:
    """Serialize as '---\\n<yaml>\\n---\\n<body>'."""
    yaml_str = yaml.safe_dump(meta, sort_keys=False, default_flow_style=False).rstrip()
    return f"---\n{yaml_str}\n---\n\n{body}".rstrip() + "\n"


# ─── Artifact ────────────────────────────────────────────────────────────────

@dataclass
class Artifact:
    """A transiting document between agents (Framework §1 naming, Spec §7.1 format)."""

    type: str                                  # e.g., "FND", "SCN", "PROP"
    sender: str                                # agent code
    content: str = ""                          # markdown body (everything after frontmatter)
    id: str | None = None                      # assigned by SequenceManager
    sender_instance: str | None = None         # e.g., "SG-S003"
    sender_model: str | None = None
    timestamp: str = field(default_factory=utcnow_iso)
    references: list[str] = field(default_factory=list)
    recipient: str | None = None
    priority: str | None = None                # P0-P3 (SG PROP only, and propagated to GOV)
    ref_type: str | None = None                # for REJ: type of artifact being rejected
    status: str = "unprocessed"                # unprocessed | processed | archived
    disposition: str | None = None             # AUTH only: approve | reject | modify
    modifications: str | None = None           # AUTH modify only
    certificate: str | None = None             # VAL only: full | build (test lane; "second" = legacy alias for build)
    filename: str | None = None                # set by ArtifactStore when persisted

    def to_markdown(self) -> str:
        meta = {
            "id": self.id,
            "type": self.type,
            "sender": self.sender,
        }
        if self.sender_instance: meta["sender_instance"] = self.sender_instance
        if self.sender_model: meta["sender_model"] = self.sender_model
        if self.recipient: meta["recipient"] = self.recipient
        meta["timestamp"] = self.timestamp
        if self.references: meta["references"] = self.references
        if self.priority: meta["priority"] = self.priority
        if self.ref_type: meta["ref_type"] = self.ref_type
        if self.disposition: meta["disposition"] = self.disposition
        if self.modifications: meta["modifications"] = self.modifications
        if self.certificate: meta["certificate"] = self.certificate
        meta["status"] = self.status
        return _emit_frontmatter(meta, self.content)

    @classmethod
    def from_markdown(cls, text: str, filename: str | None = None) -> "Artifact":
        meta, body = _split_frontmatter(text)
        refs = meta.get("references") or []
        if isinstance(refs, str):
            refs = [refs]
        return cls(
            type=meta["type"],
            sender=meta["sender"],
            content=body,
            id=meta.get("id"),
            sender_instance=meta.get("sender_instance"),
            sender_model=meta.get("sender_model"),
            timestamp=meta.get("timestamp", utcnow_iso()),
            references=list(refs),
            recipient=meta.get("recipient"),
            priority=meta.get("priority"),
            ref_type=meta.get("ref_type"),
            status=meta.get("status", "unprocessed"),
            disposition=meta.get("disposition"),
            modifications=meta.get("modifications"),
            certificate=meta.get("certificate"),
            filename=filename,
        )


# ─── Wiki Update ─────────────────────────────────────────────────────────────

@dataclass
class WikiUpdate:
    """A change to a wiki file, emitted by an agent via the WIKI_UPDATE block."""

    file: str                                  # e.g., "process_rules.md"
    action: str                                # append | replace_section | new_entry
    content: str
    section: str | None = None                 # required for replace_section
    justification: str | None = None           # required (warning if missing) for replace_section
    target: str = "own"                        # own | universal (SYS only writes universal)


# ─── Log Entry ───────────────────────────────────────────────────────────────

@dataclass
class LogEntry:
    """A line appended to an agent's log.md."""

    content: str                               # raw text (timestamp + instance prefix added by WikiManager)


# ─── Conversation Cycle ──────────────────────────────────────────────────────

CYCLE_TYPES = {
    "scn_application",        # SG ↔ SE
    "tcn_application",        # TG ↔ TE
    "prop_exchange",          # SG ↔ OP
    "triage",                 # TG internal
    "build_scope_qa",         # BR ↔ EXT
    "build_test_qa",          # BR ↔ EXT
    "build_results",          # BR ↔ EXT
    "build_remediation",      # BR ↔ EXT
    "de_qa",                  # SG ↔ DE  (opens on DE_OUT, closes on DE_IN)
    "gov_exchange",           # SYS ↔ Admin OP  (opens on GOV, closes on /resolve)
}


@dataclass
class Cycle:
    """A bounded multi-turn interaction. Messages array discarded on close (Spec §6)."""

    id: str                                    # e.g., "scn_application:SCN-SG-003"
    type: str                                  # one of CYCLE_TYPES
    participants: list[str]                    # agent codes (e.g., ["SG", "SE"])
    opening_artifact: str                      # artifact id that opened the cycle
    messages: list[dict[str, Any]] = field(default_factory=list)
    primary_agent: str | None = None           # the agent that drives this cycle
    model: str | None = None                   # for context-limit lookup
    opened_at: str = field(default_factory=utcnow_iso)

    def involves(self, agent_code: str) -> bool:
        return agent_code in self.participants

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Cycle":
        return cls(**d)


# ─── Consultation Record ─────────────────────────────────────────────────────

@dataclass
class ConsultationRecord:
    """What wiki entries were in the API call (observable, not self-reported). Spec §5.1."""

    agent: str
    instance: str
    timestamp: str
    wiki_entries_included: list[str]
    concepts_md_read: bool = False
    task_concepts: list[str] = field(default_factory=list)


# ─── Governance Finding helper ───────────────────────────────────────────────

def make_gov(reason: str, sender: str = "SYS", priority: str = "P1",
             references: list[str] | None = None) -> Artifact:
    """Construct an auto-GOV artifact. Used by router on unknown routing keys (Spec §15.3).

    Assigns a timestamp-based id since the router doesn't hold a SequenceManager
    (router is constructed before sequences during orchestrator init). The 'AUTO'
    prefix marks these as auto-generated; SYS can re-issue with a proper
    SequenceManager-minted id during its next audit. Without an id, op_backlog.add
    raises ValueError and the orchestrator crashes when SG produces an unexpected
    artifact type.
    """
    import secrets
    import time
    # Append a short random suffix so two unknown-key artifacts produced in the
    # same millisecond by the same sender (possible under asyncio.gather) don't
    # collide on the id and silently drop one at archive (audit NEW-4).
    return Artifact(
        type="GOV",
        sender=sender,
        id=f"GOV-{sender}-AUTO-{int(time.time() * 1000)}-{secrets.token_hex(2)}",
        content=f"## Auto-generated governance finding\n\n{reason}\n",
        priority=priority,
        references=references or [],
    )
