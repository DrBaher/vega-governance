"""
SYS inbox composition — Spec §5.6.

SYS must receive:
  - Recent artifacts (since last_sys_run)
  - Recent execution log entries (since last_sys_run)
  - All agent wikis
  - All agent log.md files
  - The framework (truncated)
  - Optional audit_request

Without the artifact + execution log inputs, SYS cannot audit cross-agent flow.
"""

import json
from pathlib import Path

from artifact_store import Archive, ArtifactStore
from models import Artifact


def test_archive_get_recent_filters_by_timestamp(tmp_path):
    arch = Archive(tmp_path / "archive")
    older = Artifact(type="FND", sender="SA", content="old",
                     id="FND-SA-001", timestamp="2026-05-20T10:00:00Z")
    newer = Artifact(type="FND", sender="SA", content="new",
                     id="FND-SA-002", timestamp="2026-05-21T10:00:00Z")
    arch.write(older)
    arch.write(newer)

    all_artifacts = arch.get_recent(since=None)
    assert len(all_artifacts) == 2

    since_yesterday = arch.get_recent(since="2026-05-20T15:00:00Z")
    assert len(since_yesterday) == 1
    assert since_yesterday[0].id == "FND-SA-002"

    since_future = arch.get_recent(since="2026-05-22T10:00:00Z")
    assert since_future == []


def test_sys_inbox_includes_recent_artifacts_and_exec_log(tmp_path):
    """Direct test of _format_sys_inbox — confirms artifacts and execution log
    entries are included in the inbox text fed to SYS."""
    from executor import AgentExecutor

    # Minimal stub — _format_sys_inbox doesn't need most of the executor's deps.
    class StubExecutor:
        _format_sys_inbox = AgentExecutor._format_sys_inbox

    artifacts = [
        Artifact(type="FND", sender="SA", id="FND-SA-003", content="finding body",
                 timestamp="2026-05-21T09:00:00Z"),
        Artifact(type="SCN", sender="SG", id="SCN-SG-003", content="scn body",
                 timestamp="2026-05-21T10:00:00Z"),
    ]
    exec_log = [
        {"timestamp": "2026-05-21T09:00:00Z", "execution_id": "EXEC-000001",
         "agent": "SA", "instance": "SA-S001",
         "artifacts_produced": ["FND-SA-003"], "wiki_updates": []},
        {"timestamp": "2026-05-21T10:00:00Z", "execution_id": "EXEC-000002",
         "agent": "SG", "instance": "SG-S001",
         "artifacts_produced": ["SCN-SG-003"], "wiki_updates": ["process_rules.md: append"]},
    ]

    inbox = StubExecutor._format_sys_inbox(
        StubExecutor(),
        all_wikis={"SG": "wiki body"},
        all_logs={"SG": "log body"},
        framework="(framework excerpt)",
        audit_request="check FND-SA-003 against scope",
        recent_artifacts=artifacts,
        recent_execution_log=exec_log,
        since="2026-05-20T00:00:00Z",
    )

    # Audit window
    assert "Artifacts and executions since: 2026-05-20T00:00:00Z" in inbox
    # Audit request
    assert "check FND-SA-003 against scope" in inbox
    # Framework
    assert "(framework excerpt)" in inbox
    # Artifacts section present
    assert "Recent artifacts" in inbox
    assert "FND-SA-003" in inbox
    assert "SCN-SG-003" in inbox
    # Execution log section present
    assert "Recent execution log" in inbox
    assert "EXEC-000001" in inbox
    assert "EXEC-000002" in inbox
    # Agent wikis + logs
    assert "Agent wiki: SG" in inbox
    assert "Agent log.md: SG" in inbox


def test_sys_inbox_first_run_includes_full_history_note(tmp_path):
    """When since is None (first SYS run), the inbox advertises that."""
    from executor import AgentExecutor

    inbox = AgentExecutor._format_sys_inbox(
        object.__new__(AgentExecutor),
        all_wikis={}, all_logs={}, framework="",
        audit_request=None,
        recent_artifacts=[], recent_execution_log=[],
        since=None,
    )
    assert "First SYS run — full history included" in inbox
