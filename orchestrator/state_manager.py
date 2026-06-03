"""
Atomic file operations and shared-state locking.

Per Spec §16, shared state files (sequences, instance IDs, model assignments, logs,
artifact index, wiki replace counters) need protection from interleaved writes.
The orchestrator runs in a single asyncio event loop, so asyncio.Lock is sufficient.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any


# ─── Atomic file ops ─────────────────────────────────────────────────────────

def atomic_write(filepath: str | Path, content: str) -> None:
    """Write-temp-then-rename. Atomic on POSIX."""
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=filepath.parent, prefix=".tmp_", suffix=".write")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.replace(tmp, filepath)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_append(filepath: str | Path, content: str) -> None:
    """Append to a file. Not natively atomic — wrap in a lock if multiple writers exist."""
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "a") as f:
        f.write(content)


def atomic_save_json(filepath: str | Path, data: Any) -> None:
    """Pretty-print JSON to disk atomically."""
    atomic_write(filepath, json.dumps(data, indent=2, sort_keys=True))


def load_json(filepath: str | Path, default: Any = None) -> Any:
    """Read JSON or return default if missing/empty.

    Defensive enhancement (not spec-mandated): on JSON decode error, quarantine
    the corrupt file with a `.corrupt-<unix-ms>` suffix and return the default.
    This keeps the
    orchestrator alive across partial-write crashes (the most common cause of
    corruption) while preserving forensic evidence. The caller logs the event
    via its own context.
    """
    import time

    filepath = Path(filepath)
    if not filepath.exists() or filepath.stat().st_size == 0:
        return default if default is not None else {}
    try:
        with open(filepath) as f:
            return json.load(f)
    except (json.JSONDecodeError, ValueError) as e:
        quarantine = filepath.with_name(
            f"{filepath.name}.corrupt-{int(time.time() * 1000)}"
        )
        try:
            os.replace(filepath, quarantine)
        except OSError:
            pass
        print(f"[state_manager] load_json: quarantined corrupt {filepath} → "
              f"{quarantine.name} ({type(e).__name__}: {e})", flush=True)
        return default if default is not None else {}


# ─── Execution flags (cycle-internal exchange, Spec §6.4) ────────────────────
#
# Spec §6.4 / §10.2 / §12.3 use `flag_for_execution(agent)` as pseudocode to mean
# "trigger this agent to run on the next tick without minting/routing an
# artifact." Agent execution in this orchestrator is otherwise driven by
# inbox.has_unprocessed(); a cycle-internal exchange turn appends to the cycle's
# messages array (no artifact) and must still cause the partner agent to run.
# These helpers back that flag with a small persisted queue the main loop drains
# each tick. Each flag carries the cycle id so the executor runs the right cycle.

_EXECUTION_FLAGS = "execution_flags.json"


def flag_for_execution(state_dir: str | Path, agent: str, cycle_id: str) -> None:
    """Queue `agent` to run its cycle `cycle_id` on the next tick (Spec §6.4).

    Safe to call repeatedly — duplicate (agent, cycle_id) pairs are collapsed.
    Single event loop → the load-modify-write below has no await and cannot
    interleave with the main loop's drain.
    """
    path = Path(state_dir) / _EXECUTION_FLAGS
    flags = load_json(path, default=[])
    if not isinstance(flags, list):
        flags = []
    entry = {"agent": agent, "cycle_id": cycle_id}
    if entry not in flags:
        flags.append(entry)
        atomic_save_json(path, flags)


def drain_execution_flags(state_dir: str | Path) -> list[dict[str, str]]:
    """Return and clear all pending execution flags (main loop, once per tick)."""
    path = Path(state_dir) / _EXECUTION_FLAGS
    flags = load_json(path, default=[])
    if not isinstance(flags, list):
        flags = []
    if flags:
        atomic_save_json(path, [])
    return flags


# ─── Lock registry ───────────────────────────────────────────────────────────

class LockRegistry:
    """One named asyncio.Lock per shared resource. Per Spec §16.1."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    def get(self, name: str) -> asyncio.Lock:
        # The dict insertion below is sync and safe even when called from
        # multiple coroutines: Python's GIL makes single dict ops atomic, and
        # asyncio runs coroutines on a single thread (no preemption between
        # `name not in self._locks` and the assignment). If we ever moved off
        # asyncio (e.g. to thread-per-agent), this would need an asyncio.Lock
        # or threading.Lock around the read-modify-write.
        if name not in self._locks:
            self._locks[name] = asyncio.Lock()
        return self._locks[name]


# Module-level singleton — single process, single event loop.
LOCKS = LockRegistry()
