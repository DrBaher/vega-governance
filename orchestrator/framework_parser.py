"""
Framework markdown parsing utilities.

Extracts sections by number (e.g., "5.1", "10") and D-ARCH decision rows from
the Framework v5 markdown. Used by:
  - telegram_bot._cmd_framework — show a section to OP on demand
  - main.initialize_project — extract role definitions to seed agent
    system_prompt.md files (Spec §14 step 2)
  - executor._load_framework_view — Spec v4 §5.3 + Framework v5 §12 tiered
    context loading per agent role (Guardian/SYS/Summary/Minimal)
"""

from __future__ import annotations

import re


_SECTION_HEAD_RE = re.compile(
    # Matches headings like:  ## 1. Title  /  ## 5.2 Title  /  ### §5.2.1 Title
    r"(?m)^(#{2,4})\s+§?\s*([0-9]+(?:\.[0-9]+)*)\.?\s+([^\n]+?)$"
)
_DECISION_ROW_RE = re.compile(
    r"(?m)^\|\s*(D-ARCH-\d+)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|"
)


def extract_section(text: str, section: str) -> str | None:
    """Return the body of a `## N` / `### N.M` heading until the next heading
    of the same-or-shallower depth, or — for D-ARCH-NNN — return the matching
    row from the decision log table.

    Returns None if the section cannot be found.
    """
    s = section.strip().lstrip("§").strip()

    # D-ARCH-NNN lookup
    if s.upper().startswith("D-ARCH-"):
        for m in _DECISION_ROW_RE.finditer(text):
            if m.group(1) == s.upper():
                return (
                    f"**{m.group(1)}** — {m.group(2).strip()}\n\n"
                    f"_Rationale:_ {m.group(3).strip()}"
                )
        return None

    # Numeric section lookup
    for m in _SECTION_HEAD_RE.finditer(text):
        depth_marks, num, title = m.group(1), m.group(2), m.group(3)
        if num == s:
            depth = len(depth_marks)
            tail = text[m.end():]
            next_match = re.search(rf"(?m)^#{{2,{depth}}}\s+", tail)
            body = tail[:next_match.start()] if next_match else tail
            return f"## {num} {title.strip()}\n{body.strip()}"
    return None


# Agent → Framework §5.X section mapping
AGENT_TO_SECTION = {
    "SG":  "5.1",
    "SA":  "5.2",
    "SE":  "5.3",
    "TG":  "5.4",
    "TA":  "5.5",
    "TE":  "5.6",
    "BR":  "5.7",
    "BTA": "5.8",
    "SYS": "5.9",
}


# ─── Tiered Framework Context (Framework v5 §12, Spec v4 §5.3) ───────────────
#
# Per-agent context view. Loading the full ~30k-token framework into every
# agent's context wastes tokens (SE/TE only need their role) and risks
# over-reasoning about governance instead of executing. The tiered view loads
# only what each agent needs:
#
#   Guardian (SG, TG)  — §1 + §2 + §9 + §10 + project addendum (~12k tokens)
#   SYS                — §1 + §2 + §5 + §10                     (~18k tokens)
#   Summary (SA/TA/BR/BTA) — Framework §12.1 prose summary       (~2k tokens)
#   Minimal (SE, TE)   — nothing (system prompt is enough)
#
AGENT_TIER: dict[str, str] = {
    "SG":  "guardian",
    "TG":  "guardian",
    "SYS": "sys",
    "SA":  "summary",
    "TA":  "summary",
    "BR":  "summary",
    "BTA": "summary",
    "SE":  "minimal",
    "TE":  "minimal",
}

# Sections loaded per tier. Order matters for prompt-cache stability.
TIER_SECTIONS: dict[str, list[str]] = {
    "guardian": ["1", "2", "9", "10"],
    "sys":      ["1", "2", "5", "10"],
    # summary tier is handled separately — pulls §12.1 as a single block.
    "summary":  ["12.1"],
    # minimal tier loads no framework context.
    "minimal":  [],
}


def load_framework_view(framework_text: str, agent_code: str,
                        addendum_text: str = "") -> str:
    """Return the tiered framework context for `agent_code`.

    Framework v5 §12. Empty string for the minimal tier. Guardians (SG/TG)
    additionally receive the Project Addendum (per §12.2): SG always, TG only
    if the project's test decomposition needs domain context — at the
    framework level we pass it for both and let the agent ignore irrelevant
    parts (cheaper than per-project conditional loading).
    """
    tier = AGENT_TIER.get(agent_code, "summary")
    sections = TIER_SECTIONS.get(tier, [])
    chunks: list[str] = []
    for sec in sections:
        body = extract_section(framework_text, sec)
        if body:
            chunks.append(body)
    if tier == "guardian" and addendum_text:
        chunks.append(f"## Project Addendum\n{addendum_text}")
    return "\n\n".join(chunks)

AGENT_NAMES = {
    "SG":  "Scope Guardian",
    "SA":  "Scope Auditor",
    "SE":  "Scope Editor",
    "TG":  "Tests Guardian",
    "TA":  "Tests Auditor",
    "TE":  "Tests Editor",
    "BR":  "Build Rep",
    "BTA": "Build Test Auditor",
    "SYS": "System Auditor",
}


STANDARD_PREAMBLE = (
    "Read in detail, rigorously and thoroughly through each of the files. "
    "You need to know and understand every detail of the project — don't "
    "leave a single stone unturned. Ask if you have any questions or doubts, "
    "and don't assume, confabulate, or suppose — go to the bottom of each "
    "topic. Once you have properly understood the project, perform your own "
    "analysis. Use your logic, rigour, and your domain knowledge as well as "
    "product architecture and technology. Apply the relevant standards and "
    "conventions of the project's domain."
)


OUTPUT_FORMAT_BLOCK = """\
## Output Format

Emit your output as plain text. Use the following three block types for
structured outputs the orchestrator parses (Spec §5.3):

### ARTIFACT
type: <document type code, e.g. FND, SCN, PROP, DOC, VAL, REJ, ...>
id: <leave blank — orchestrator assigns>
references: <comma-separated list of referenced artifact IDs>
priority: <P0-P3, if applicable — SG PROP only>
ref_type: <type of referenced artifact, if this is a REJ>
certificate: <full | build, for VAL artifacts in test lane (Framework v5 §1).
              Closes tcn_application cycle on `build` only.
              Legacy "second" still accepted for archive replay.>
---
<artifact content as markdown>

### WIKI_UPDATE
file: <wiki file, e.g. process_rules.md>
action: <append | replace_section | new_entry>
section: <section name, required for replace_section>
justification: <why this change — mandatory for replace_section>
---
<content>

### LOG_ENTRY
---
<entry to append to log.md — be greppable, lead with event type>

Any text outside these blocks is treated as your reasoning / commentary and
not routed.
"""


def compose_system_prompt(
    framework_text: str,
    agent_code: str,
    project_name: str = "",
) -> str:
    """Spec §5.3 — assemble a full system_prompt.md for one agent by extracting
    its role definition (Framework §5.X) and combining with the standard
    preamble, file-locations note, and output format block.

    Raises ValueError if the role section cannot be extracted.
    """
    section_num = AGENT_TO_SECTION.get(agent_code)
    name = AGENT_NAMES.get(agent_code)
    if not section_num or not name:
        raise ValueError(f"Unknown agent code: {agent_code}")

    role_body = extract_section(framework_text, section_num)
    if not role_body:
        raise ValueError(
            f"Could not extract §{section_num} ({name}) from framework. "
            f"Verify framework/VEGA_Architecture_Framework_v6.md (or v5/v4) is intact."
        )

    project_line = f"Project: {project_name}\n" if project_name else ""

    return f"""\
# Role: {name} ({agent_code})
Instance: {agent_code}-S[NNN]
{project_line}
## Preamble
{STANDARD_PREAMBLE}

## Your Role (from Framework §{section_num})

{role_body}

## File Locations
- Your wiki: read in full at session start (SCHEMA, index, log, process_rules,
  reasoning_corrections, plus any role-specific pages). Update per the
  WIKI_UPDATE protocol when something matters.
- UNIVERSAL: contains manifesto, cross_agent_rules, case_index. Read at
  session start.
- Project Addendum: framework/VEGA_<Project>_Project_Addendum.md (project-
  specific decisions, document manifest, Domain Expert, test levels).
- Scope documents: per Spec §3.2 access table.

{OUTPUT_FORMAT_BLOCK}

## Authority Hierarchy (D-ARCH-030)
Sources > Role definition > Own wiki > UNIVERSAL > Session context.
On contradiction, higher layer wins. Flag the contradiction in log.md and
return; OP resolves.

## Session start protocol
1. Re-read your role above.
2. Read your wiki and UNIVERSAL.
3. Read the Project Addendum and any scope documents you have access to.
4. Process your inbox.

The orchestrator re-loads all of the above on every API call — you are
re-grounded structurally on every execution (Spec §5.5).
"""
