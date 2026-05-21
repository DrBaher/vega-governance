"""
Artifact YAML frontmatter round-trip and inbox priority sort.
"""

from models import Artifact, utcnow_iso
from artifact_store import Inbox


def test_artifact_roundtrip():
    a = Artifact(
        type="FND",
        sender="SA",
        sender_instance="SA-S001",
        sender_model="claude-sonnet-4-6",
        content="# Finding body",
        id="FND-SA-003",
        references=["PRO-SCOPE:v7.2"],
        priority="P1",
    )
    text = a.to_markdown()
    assert text.startswith("---")
    assert "id: FND-SA-003" in text
    a2 = Artifact.from_markdown(text)
    assert a2.id == a.id
    assert a2.type == a.type
    assert a2.references == a.references
    assert a2.priority == "P1"
    assert "Finding body" in a2.content


def test_inbox_priority_sort(tmp_agents_dir):
    inbox = Inbox(tmp_agents_dir / "SG")
    high = Artifact(type="PROP", sender="SG", priority="P0",
                    content="urgent", id="PROP-SG-001",
                    timestamp="2026-01-01T10:00:00Z")
    low = Artifact(type="PROP", sender="SG", priority="P3",
                   content="trivial", id="PROP-SG-002",
                   timestamp="2026-01-01T09:00:00Z")
    standard = Artifact(type="PROP", sender="SG", priority="P2",
                        content="std", id="PROP-SG-003",
                        timestamp="2026-01-01T11:00:00Z")
    for a in (low, high, standard):
        inbox.deliver(a)
    items = inbox.get_unprocessed()
    assert [a.id for a in items] == ["PROP-SG-001", "PROP-SG-003", "PROP-SG-002"]


def test_archive_immutable(tmp_path):
    from artifact_store import Archive
    arch = Archive(tmp_path / "archive")
    a = Artifact(type="FND", sender="SA", content="v1", id="FND-SA-001")
    arch.write(a)
    a.content = "v2 — should be ignored"
    arch.write(a)  # should not overwrite
    loaded = arch.load("FND-SA-001")
    assert "v1" in loaded.content
    assert "v2" not in loaded.content
