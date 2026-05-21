"""
SYS scheduling — Spec §13.

Verifies that SYS fires AT MOST once per daily window (was ~6× per window
because _is_daily_time matched the whole minute and POLL_INTERVAL was 10s).
"""

from datetime import datetime, timedelta, timezone

from scheduling import should_fire_sys


def _utc(year=2026, month=5, day=21, hour=2, minute=0, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


# ─── daily mode ─────────────────────────────────────────────────────────────

def test_daily_fires_at_target_minute_when_no_prior_run():
    assert should_fire_sys(
        now=_utc(hour=2, minute=0),
        schedule="daily",
        daily_time="02:00",
        execution_count=0,
        execution_threshold=20,
        last_sys_run=None,
    ) is True


def test_daily_does_not_fire_outside_target_minute():
    assert should_fire_sys(
        now=_utc(hour=2, minute=1),   # 02:01 — past the target
        schedule="daily",
        daily_time="02:00",
        execution_count=0, execution_threshold=20,
        last_sys_run=None,
    ) is False


def test_daily_does_not_fire_again_within_23h_of_last_run():
    """The whole point — without this gate, SYS fires once per poll within
    the 02:00 minute (~6× with POLL_INTERVAL=10s)."""
    last = _utc(hour=2, minute=0, second=0)
    later = _utc(hour=2, minute=0, second=30)   # 30s later, same minute
    assert should_fire_sys(
        now=later,
        schedule="daily",
        daily_time="02:00",
        execution_count=0, execution_threshold=20,
        last_sys_run=last,
    ) is False


def test_daily_fires_again_after_23h():
    last = _utc(year=2026, month=5, day=20, hour=2, minute=0)
    later = _utc(year=2026, month=5, day=21, hour=2, minute=0)   # +24h
    assert should_fire_sys(
        now=later,
        schedule="daily",
        daily_time="02:00",
        execution_count=0, execution_threshold=20,
        last_sys_run=last,
    ) is True


# ─── every_N mode ──────────────────────────────────────────────────────────

def test_every_n_fires_at_multiples():
    for count in (20, 40, 60):
        assert should_fire_sys(
            now=_utc(),
            schedule="every_N_executions",
            daily_time="02:00",
            execution_count=count,
            execution_threshold=20,
            last_sys_run=None,
        ) is True


def test_every_n_does_not_fire_off_threshold():
    for count in (1, 19, 21, 39):
        assert should_fire_sys(
            now=_utc(),
            schedule="every_N_executions",
            daily_time="02:00",
            execution_count=count,
            execution_threshold=20,
            last_sys_run=None,
        ) is False


def test_every_n_does_not_fire_at_zero():
    assert should_fire_sys(
        now=_utc(),
        schedule="every_N_executions",
        daily_time="02:00",
        execution_count=0,
        execution_threshold=20,
        last_sys_run=None,
    ) is False


# ─── unknown schedule ──────────────────────────────────────────────────────

def test_unknown_schedule_never_fires():
    assert should_fire_sys(
        now=_utc(hour=2, minute=0),
        schedule="never",
        daily_time="02:00",
        execution_count=100,
        execution_threshold=20,
        last_sys_run=None,
    ) is False
