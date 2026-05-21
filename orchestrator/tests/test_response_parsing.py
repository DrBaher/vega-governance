"""
Executor: response parsing of ARTIFACT / WIKI_UPDATE / LOG_ENTRY blocks.
"""

from executor import _parse_blocks


SAMPLE = """\
Some prose from the agent...

### ARTIFACT
type: FND
priority: P1
references: PRO-SCOPE-001
---
# Cross-reference gap in §3.9

Body text describing the finding.

### WIKI_UPDATE
file: process_rules.md
action: append
---
PR-22: Always verify cross-refs before issuing a finding.

### LOG_ENTRY
---
Produced FND-SA-003 against PRO-SCOPE-001
"""


def test_parses_artifact_block():
    parsed = _parse_blocks(SAMPLE)
    assert len(parsed["ARTIFACT"]) == 1
    header, content = parsed["ARTIFACT"][0]
    assert header["type"] == "FND"
    assert header["priority"] == "P1"
    assert "Cross-reference gap" in content


def test_parses_wiki_update_block():
    parsed = _parse_blocks(SAMPLE)
    assert len(parsed["WIKI_UPDATE"]) == 1
    header, content = parsed["WIKI_UPDATE"][0]
    assert header["file"] == "process_rules.md"
    assert header["action"] == "append"
    assert "PR-22" in content


def test_parses_log_entry_block():
    parsed = _parse_blocks(SAMPLE)
    assert len(parsed["LOG_ENTRY"]) == 1
    _, content = parsed["LOG_ENTRY"][0]
    assert "Produced FND-SA-003" in content


def test_empty_text_returns_empty():
    parsed = _parse_blocks("Just some prose, no blocks.")
    assert parsed["ARTIFACT"] == []
    assert parsed["WIKI_UPDATE"] == []
    assert parsed["LOG_ENTRY"] == []
