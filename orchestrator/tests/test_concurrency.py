"""
Concurrency tests for Spec §16.1 — shared-state writers must hold asyncio.Lock.

These tests use asyncio.gather to force concurrent routing through the router and
concurrent wiki updates, then assert the resulting state files contain ALL the
expected entries (no lost writes from interleaved read-modify-write).
"""

import asyncio

import pytest

from artifact_store import ArtifactStore
from models import Artifact, LogEntry, WikiUpdate
from router import Router
from sequence_manager import InstanceManager, ModelAssignmentManager
from state_manager import load_json
from wiki_manager import WikiManager


@pytest.mark.asyncio
async def test_routing_log_no_lost_writes_under_concurrent_route(tmp_path):
    """Spec §16.1 — routing_log.json must be lock-protected.

    Concurrent routes through asyncio.gather must all appear in routing_log,
    no lost entries from interleaved read-modify-write.
    """
    state_dir = tmp_path / "state"; state_dir.mkdir()
    agents_dir = tmp_path / "agents"
    for code in ["SG", "SA"]:
        (agents_dir / code / "inbox").mkdir(parents=True)
        (agents_dir / code / "outbox").mkdir(parents=True)
    archive_dir = tmp_path / "artifacts" / "archive"; archive_dir.mkdir(parents=True)

    store = ArtifactStore(agents_dir, archive_dir)
    router = Router(store=store, state_dir=state_dir,
                    op_backlog=None, telegram_bot=None)

    # 20 concurrent FND-SA-NNN routings — all should land in routing_log.
    artifacts = [
        Artifact(type="FND", sender="SA", content=f"finding {i}",
                 id=f"FND-SA-{i:03d}")
        for i in range(1, 21)
    ]
    await asyncio.gather(*[router.route(a) for a in artifacts])

    routing_log = load_json(state_dir / "routing_log.json", default=[])
    assert isinstance(routing_log, list)
    logged_ids = {e.get("artifact_id") for e in routing_log}
    expected_ids = {f"FND-SA-{i:03d}" for i in range(1, 21)}
    missing = expected_ids - logged_ids
    assert not missing, f"Lost routing log entries: {missing}"


@pytest.mark.asyncio
async def test_wiki_replace_counter_no_undercount_under_concurrent_updates(tmp_path):
    """Spec §16.1 — wiki_replace_counters.json must be lock-protected."""
    state_dir = tmp_path / "state"; state_dir.mkdir()
    agents_dir = tmp_path / "agents"
    (agents_dir / "SG" / "wiki").mkdir(parents=True)
    universal_dir = tmp_path / "universal"; universal_dir.mkdir()

    wm = WikiManager(
        agents_dir=agents_dir, universal_dir=universal_dir, state_dir=state_dir,
        instance_lookup=lambda c: f"{c}-S001",
        model_lookup=lambda c: "claude-sonnet-4-6",
        replace_threshold_default=100,  # high so threshold doesn't fire mid-test
    )
    (agents_dir / "SG" / "wiki" / "process_rules.md").write_text(
        "## Rule 1\nOriginal body\n"
    )

    # 15 concurrent replace_section updates — counter must reach exactly 15.
    updates = [WikiUpdate(
        file="process_rules.md", action="replace_section",
        section="Rule 1", content=f"body v{i}", justification=f"reason {i}",
    ) for i in range(15)]
    await asyncio.gather(*[wm.apply_updates("SG", [u]) for u in updates])

    counters = load_json(state_dir / "wiki_replace_counters.json", default={})
    assert counters.get("SG") == 15, \
        f"Expected SG counter=15, got {counters.get('SG')} (race-induced undercount)"
