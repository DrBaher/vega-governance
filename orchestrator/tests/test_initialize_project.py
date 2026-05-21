"""
initialize_project — Spec §14 sequence.

Critical assertion: step 2 (extract system prompts from framework) actually
writes agents/<CODE>/system_prompt.md for every agent. Without this, the
post-Fix-7 orchestrator can't run — _load_system_prompt raises immediately.
"""

from pathlib import Path

import pytest

from framework_parser import (
    AGENT_NAMES,
    AGENT_TO_SECTION,
    compose_system_prompt,
    extract_section,
)


# Minimal valid framework — has §5.1 through §5.9 with the role names the
# parser expects to find via AGENT_TO_SECTION.
def _mini_framework() -> str:
    parts = ["# Framework v4\n\n## Table of Contents\n5. Role Definitions\n"]
    parts.append("## 5. Role Definitions\n\nOverview.\n")
    for code, section in AGENT_TO_SECTION.items():
        name = AGENT_NAMES[code]
        parts.append(
            f"\n### {section} {name} ({code})\n\n"
            f"#### Purpose\n{name} purpose.\n\n"
            f"#### Inputs\nList.\n"
        )
    parts.append("\n## 6. Wiki Schema\n\nWiki section content.\n")
    return "\n".join(parts)


def test_compose_system_prompt_includes_role_section():
    fw = _mini_framework()
    prompt = compose_system_prompt(fw, "SG", project_name="testproj")
    assert "# Role: Scope Guardian (SG)" in prompt
    assert "Project: testproj" in prompt
    # Standard preamble present
    assert "Read in detail" in prompt
    # Role body extracted from §5.1
    assert "5.1 Scope Guardian (SG)" in prompt
    assert "Scope Guardian purpose." in prompt
    # Output format block included
    assert "### ARTIFACT" in prompt
    assert "### WIKI_UPDATE" in prompt
    assert "### LOG_ENTRY" in prompt
    # Authority hierarchy present
    assert "D-ARCH-030" in prompt


def test_compose_system_prompt_all_agents():
    """Every agent code must produce a valid prompt with the right role body."""
    fw = _mini_framework()
    for code, name in AGENT_NAMES.items():
        prompt = compose_system_prompt(fw, code)
        assert f"# Role: {name} ({code})" in prompt, f"Failed for {code}"
        assert AGENT_TO_SECTION[code] in prompt, f"Section number missing for {code}"


def test_compose_system_prompt_unknown_agent_raises():
    fw = _mini_framework()
    with pytest.raises(ValueError, match="Unknown agent code"):
        compose_system_prompt(fw, "XYZ")


def test_compose_system_prompt_missing_section_raises():
    """If the framework doesn't have the expected section, raise with a clear hint."""
    fw = "# Framework\n\n## 1. Other\n\nUnrelated.\n"
    with pytest.raises(ValueError, match="Could not extract"):
        compose_system_prompt(fw, "SG")


def test_extract_section_shared_between_bot_and_parser():
    """Same function should produce the same result whether imported from
    framework_parser or telegram_bot (which re-exports it)."""
    from telegram_bot import _extract_section as bot_extract
    assert extract_section is bot_extract  # re-exported, not duplicated
