"""
Cycle compression at the 40% context threshold (Spec §6.4 + U-RC-08).

Spec quotes the log message format exactly:
    "CYCLE_COMPRESSION | {cycle.id} | Context at {usage:.0%}, compressed early turns
     (U-RC-08 warning: verify post-compression)"
"""

import json

from cycle_manager import CycleManager
from models import Artifact, Cycle


def test_compress_keeps_recent_summarizes_older(tmp_path):
    cm = CycleManager(tmp_path / "active", tmp_path / "archive")
    scn = Artifact(type="SCN", sender="SG", id="SCN-SG-001", content="x")
    cycle = cm.open_cycle("scn_application", scn, ["SG", "SE"], primary_agent="SG")

    # Seed 12 turns. With keep_recent=6, 6 should be summarized.
    for i in range(12):
        role = "user" if i % 2 == 0 else "assistant"
        cycle.messages.append({"role": role, "content": f"turn {i} content"})

    compressed = cm.compress_early_turns(cycle, keep_recent=6)
    assert compressed == 6

    # After compression: 1 summary message + 6 recent = 7 total
    assert len(cycle.messages) == 7
    assert cycle.messages[0]["role"] == "user"
    assert "CYCLE COMPRESSION" in cycle.messages[0]["content"]
    assert "U-RC-08" in cycle.messages[0]["content"]
    # Recent turns preserved verbatim
    assert cycle.messages[1]["content"] == "turn 6 content"
    assert cycle.messages[-1]["content"] == "turn 11 content"


def test_compress_no_op_when_few_turns(tmp_path):
    cm = CycleManager(tmp_path / "active", tmp_path / "archive")
    scn = Artifact(type="SCN", sender="SG", id="SCN-SG-002", content="x")
    cycle = cm.open_cycle("scn_application", scn, ["SG", "SE"], primary_agent="SG")
    cycle.messages = [{"role": "user", "content": "only one"}]
    compressed = cm.compress_early_turns(cycle, keep_recent=6)
    assert compressed == 0
    assert len(cycle.messages) == 1   # unchanged


def test_compress_persists_to_disk(tmp_path):
    cm = CycleManager(tmp_path / "active", tmp_path / "archive")
    scn = Artifact(type="SCN", sender="SG", id="SCN-SG-003", content="x")
    cycle = cm.open_cycle("scn_application", scn, ["SG", "SE"], primary_agent="SG")
    for i in range(10):
        cycle.messages.append({"role": "user", "content": f"turn {i}"})
    cm.compress_early_turns(cycle, keep_recent=4)

    # Reload from disk and verify
    files = list((tmp_path / "active").glob("*.json"))
    assert len(files) == 1
    on_disk = json.loads(files[0].read_text())
    # 1 summary + 4 recent = 5 messages
    assert len(on_disk["messages"]) == 5
    assert "CYCLE COMPRESSION" in on_disk["messages"][0]["content"]
