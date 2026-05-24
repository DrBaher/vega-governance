"""
Document ID + decision sequence + instance ID generation.

Per Spec §9. Sequences are stored in state/sequences.json and protected by an
asyncio.Lock — id collisions would corrupt the artifact archive.

ID formats:
  Documents:  TYPE-AGENT-NNN          e.g., FND-SA-003, SCN-SG-012
  Decisions:  PREFIX-NNN              D-014 (SG), TD-007 (TG), GD-002 (SYS)
  Instances:  AGENT-SNNN              e.g., SG-S001
"""

from __future__ import annotations

from pathlib import Path

from state_manager import LOCKS, atomic_save_json, load_json


DECISION_PREFIX = {"SG": "D", "TG": "TD", "SYS": "GD"}


class SequenceManager:
    """Persists per-(agent, doc_type) counters. Thread-safe via asyncio.Lock."""

    def __init__(self, state_dir: str | Path) -> None:
        self.state_file = Path(state_dir) / "sequences.json"
        self.lock_name = f"sequences:{self.state_file}"

    def _load(self) -> dict[str, int]:
        return load_json(self.state_file, default={})

    async def next_id(self, agent_code: str, doc_type: str) -> str:
        """Next artifact ID for (agent, doc_type)."""
        async with LOCKS.get(self.lock_name):
            sequences = self._load()
            key = f"{agent_code}:{doc_type}"
            sequences[key] = sequences.get(key, 0) + 1
            seq = sequences[key]
            atomic_save_json(self.state_file, sequences)
            return f"{doc_type}-{agent_code}-{seq:03d}"

    async def next_decision(self, agent_code: str) -> str:
        """Next decision ID. Only SG (D-), TG (TD-), SYS (GD-) have decision counters."""
        if agent_code not in DECISION_PREFIX:
            raise ValueError(f"{agent_code} does not own a decision counter")
        prefix = DECISION_PREFIX[agent_code]
        async with LOCKS.get(self.lock_name):
            sequences = self._load()
            key = f"{agent_code}:DECISION"
            sequences[key] = sequences.get(key, 0) + 1
            seq = sequences[key]
            atomic_save_json(self.state_file, sequences)
            return f"{prefix}-{seq:03d}"

    def initialize(self, offsets: dict[str, int]) -> None:
        """One-time setup. Project Addendum §5 may set starting offsets."""
        sequences = self._load()
        for key, value in offsets.items():
            sequences[key] = max(sequences.get(key, 0), value)
        atomic_save_json(self.state_file, sequences)


class InstanceManager:
    """Per-agent instance IDs. Bumped via /rotate.

    Invariant: get_or_create() is intentionally non-async. The orchestrator
    pre-materializes all known agents at startup (Orchestrator.__init__ →
    self.instances.get_or_create(code) for every agent in the registry).
    In steady state, get_or_create() is therefore a pure read — the
    `if agent_code not in instances` branch is dead. A race here would only
    occur for an agent never seen at startup, which already violates the
    pre-materialize contract.

    If that invariant ever changes (e.g., dynamic agent registration), this
    method must become async and wrap the read-modify-write in
    `LOCKS.get(self.lock_name)` like rotate() does.
    """

    def __init__(self, state_dir: str | Path) -> None:
        self.state_file = Path(state_dir) / "instance_ids.json"
        self.lock_name = f"instances:{self.state_file}"

    def _load(self) -> dict[str, int]:
        return load_json(self.state_file, default={})

    def get_or_create(self, agent_code: str) -> str:
        instances = self._load()
        if agent_code not in instances:
            instances[agent_code] = 1
            atomic_save_json(self.state_file, instances)
        return f"{agent_code}-S{instances[agent_code]:03d}"

    async def rotate(self, agent_code: str) -> str:
        """Bump instance counter — new logical session for the agent."""
        async with LOCKS.get(self.lock_name):
            instances = self._load()
            instances[agent_code] = instances.get(agent_code, 0) + 1
            atomic_save_json(self.state_file, instances)
            return f"{agent_code}-S{instances[agent_code]:03d}"


class ModelAssignmentManager:
    """Tracks per-agent model overrides (mutable at runtime via /model).

    Fallback chain (Spec §9.3):
      1. per-agent runtime override (set via /model)
      2. per-agent default from `defaults` (typically from Project Addendum §6.1)
      3. project-wide default (config.AGENT_MODEL, passed via project_default)
      4. last-resort hardcoded "claude-sonnet-4-6"

    The project_default param lets the orchestrator pass config.AGENT_MODEL
    so an agent absent from `defaults` doesn't silently get the legacy
    hardcoded model — it gets whatever the project picked.
    """

    def __init__(self, state_dir: str | Path, defaults: dict[str, str],
                 project_default: str | None = None) -> None:
        self.state_file = Path(state_dir) / "model_assignments.json"
        self.lock_name = f"models:{self.state_file}"
        self._defaults = dict(defaults)
        self._project_default = project_default

    def _load(self) -> dict[str, str]:
        return load_json(self.state_file, default={})

    def get(self, agent_code: str) -> str:
        overrides = self._load()
        return (
            overrides.get(agent_code)
            or self._defaults.get(agent_code)
            or self._project_default
            or "claude-sonnet-4-6"
        )

    async def set(self, agent_code: str, model: str) -> None:
        async with LOCKS.get(self.lock_name):
            overrides = self._load()
            overrides[agent_code] = model
            atomic_save_json(self.state_file, overrides)

    def all(self) -> dict[str, str]:
        result = dict(self._defaults)
        result.update(self._load())
        return result
