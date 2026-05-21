"""
SYS scheduling decision functions.

Extracted from main.py so they're importable without pulling in a
project-specific config module. Pure (no I/O, all inputs explicit).

Per Spec §13.
"""

from __future__ import annotations

from datetime import datetime, timezone


def should_fire_sys(
    now: datetime,
    schedule: str,
    daily_time: str,
    execution_count: int,
    execution_threshold: int,
    last_sys_run: datetime | None,
) -> bool:
    """Pure decision function — Spec §13.

    Daily mode: fire only if the wall-clock minute matches AND last run is
    older than 23 hours (de-dupes within the matching minute under fast
    polling).

    every_N_executions mode: fire when count is a non-zero multiple of the
    threshold.

    Pure — no I/O. All inputs explicit. Directly testable.
    """
    if schedule == "daily":
        if not is_daily_time(daily_time, now):
            return False
        if last_sys_run is not None:
            elapsed = (now - last_sys_run).total_seconds()
            if elapsed < 23 * 3600:
                return False
        return True
    if schedule == "every_N_executions":
        return execution_count > 0 and (
            execution_count % execution_threshold == 0
        )
    return False


def is_daily_time(target: str, now: datetime | None = None) -> bool:
    """True if `now` (UTC) is within the target minute (HH:MM)."""
    try:
        hh, mm = [int(x) for x in target.split(":")]
    except ValueError:
        return False
    if now is None:
        now = datetime.now(timezone.utc)
    return now.hour == hh and now.minute == mm
