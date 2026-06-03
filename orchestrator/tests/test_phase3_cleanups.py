"""
Phase 3 — LOW cleanup fixes (audit NEW-4/5/14 + prompt Phase 3 table).
"""

from __future__ import annotations

from pathlib import Path

from artifact_store import Archive
from models import CYCLE_TYPES, DOCUMENT_TYPES, make_gov


def test_make_gov_ids_are_collision_safe():
    """NEW-4 — two auto-GOVs minted in the same ms get distinct ids."""
    ids = {make_gov(reason=f"r{i}").id for i in range(50)}
    assert len(ids) == 50
    assert all(i.startswith("GOV-SYS-AUTO-") for i in ids)


def test_constants_cover_v5_types():
    """NEW-5 — CYCLE_TYPES has de_qa + gov_exchange; DOCUMENT_TYPES has REQ."""
    assert {"de_qa", "gov_exchange"} <= CYCLE_TYPES
    assert "REQ" in DOCUMENT_TYPES


def test_archive_collision_is_logged_to_file(tmp_path):
    """NEW-14 — a genuine collision (same id, different content) is persisted to a
    SYS-discoverable governance log, not only stdout."""
    from models import Artifact
    archive = Archive(tmp_path / "artifacts" / "archive")
    a1 = Artifact(type="GOV", sender="SYS", content="first", id="GOV-SYS-001")
    archive.write(a1)
    a2 = Artifact(type="GOV", sender="SYS", content="DIFFERENT", id="GOV-SYS-001")
    archive.write(a2)   # collision — on-disk (first) preserved, collision logged
    log = tmp_path / "artifacts" / "archive_collisions.log"
    assert log.exists()
    body = log.read_text()
    assert "GOV-SYS-001" in body and "GOVERNANCE" in body
    # On-disk version is still the first one (immutability).
    assert "first" in (tmp_path / "artifacts" / "archive" / "GOV-SYS-001.md").read_text()
