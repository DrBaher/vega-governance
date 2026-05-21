"""
Sequence manager: id generation, decision counters, instance rotation, concurrency.
"""

import asyncio
import pytest

from sequence_manager import SequenceManager, InstanceManager


@pytest.mark.asyncio
async def test_next_id_increments_per_agent_and_type(tmp_state_dir):
    sm = SequenceManager(tmp_state_dir)
    assert await sm.next_id("SA", "FND") == "FND-SA-001"
    assert await sm.next_id("SA", "FND") == "FND-SA-002"
    assert await sm.next_id("SG", "FND") == "FND-SG-001"
    assert await sm.next_id("SA", "SCN") == "SCN-SA-001"


@pytest.mark.asyncio
async def test_next_decision_prefixes(tmp_state_dir):
    sm = SequenceManager(tmp_state_dir)
    assert (await sm.next_decision("SG")).startswith("D-")
    assert (await sm.next_decision("TG")).startswith("TD-")
    assert (await sm.next_decision("SYS")).startswith("GD-")
    with pytest.raises(ValueError):
        await sm.next_decision("SE")


@pytest.mark.asyncio
async def test_next_id_concurrent_no_collisions(tmp_state_dir):
    sm = SequenceManager(tmp_state_dir)
    results = await asyncio.gather(
        *[sm.next_id("SG", "PROP") for _ in range(20)]
    )
    assert len(set(results)) == 20


def test_initialize_with_offsets(tmp_state_dir):
    sm = SequenceManager(tmp_state_dir)
    sm.initialize({"SG:SCN": 5, "SG:DECISION": 80})
    # Don't go backwards
    sm.initialize({"SG:SCN": 2})

    async def _check():
        nxt = await sm.next_id("SG", "SCN")
        assert nxt == "SCN-SG-006"
        d = await sm.next_decision("SG")
        assert d == "D-081"

    asyncio.run(_check())


def test_instance_manager_get_or_create(tmp_state_dir):
    im = InstanceManager(tmp_state_dir)
    assert im.get_or_create("SG") == "SG-S001"
    assert im.get_or_create("SG") == "SG-S001"  # idempotent


@pytest.mark.asyncio
async def test_instance_rotate(tmp_state_dir):
    im = InstanceManager(tmp_state_dir)
    im.get_or_create("SG")
    assert (await im.rotate("SG")) == "SG-S002"
    assert (await im.rotate("SG")) == "SG-S003"
