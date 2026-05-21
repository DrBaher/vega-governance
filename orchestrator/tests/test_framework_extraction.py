"""
/framework command — section extraction logic (Spec §11).

Used by the Telegram bot to quote a specific section of the Framework
document instead of just pointing at the file.
"""

from telegram_bot import _extract_section


SAMPLE_FRAMEWORK = """\
# VEGA Framework

## Table of Contents
1. Naming
2. Catalog
5. Roles
   5.1 Scope Guardian
   5.2 Scope Auditor
10. Decisions

## 1. Naming Convention

Some text here.

## 5. Role Definitions

Overview text.

### 5.1 Scope Guardian (SG)

#### Purpose
Analytical and recommendation role.

#### Inputs
List goes here.

### 5.2 Scope Auditor (SA)

#### Purpose
Independent verification.

## 10. Decision Log

| ID | Decision | Rationale |
|----|----------|-----------|
| D-ARCH-001 | Build Auditor removed | BTA provides formal verification |
| D-ARCH-020 | Three-layer architecture | Strict hierarchy |

## 11. Implementation Notes
"""


def test_extract_top_level_section():
    body = _extract_section(SAMPLE_FRAMEWORK, "1")
    assert body is not None
    assert "Naming Convention" in body
    assert "Some text here" in body
    # Doesn't bleed into §5
    assert "Role Definitions" not in body


def test_extract_subsection():
    body = _extract_section(SAMPLE_FRAMEWORK, "5.1")
    assert body is not None
    assert "Scope Guardian (SG)" in body
    assert "Analytical and recommendation" in body
    # Doesn't bleed into §5.2
    assert "Scope Auditor (SA)" not in body


def test_extract_d_arch_decision():
    body = _extract_section(SAMPLE_FRAMEWORK, "D-ARCH-020")
    assert body is not None
    assert "Three-layer architecture" in body
    assert "Strict hierarchy" in body


def test_extract_unknown_section_returns_none():
    assert _extract_section(SAMPLE_FRAMEWORK, "99.99") is None
    assert _extract_section(SAMPLE_FRAMEWORK, "D-ARCH-999") is None
