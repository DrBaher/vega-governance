# Agent Wiki Seeds

Persistent knowledge stores for each agent, extracted from 5 sessions spanning March–May 2026.

## Reliability

| Source | Agents covered | Reliability |
|--------|---------------|-------------|
| Scope Guardian extraction | SG | HIGH — dedicated role session, most recent |
| Scope Editor extraction | SE | HIGH — dedicated role session, most recent |
| Session 1 extraction | BR, TG, BTA (curated) | MEDIUM — multi-role session, filtered by role |
| Session 3 extraction | SA (audit methodology) | MEDIUM — pre-architecture, audit-focused rules |
| Session 2 extraction | (folded into universal) | LOW — oldest, pre-role-split |

## Structure

```
UNIVERSAL/          — Cross-agent rules and knowledge (all agents read this)
SG/                 — Scope Guardian wiki seed
SA/                 — Scope Auditor wiki seed
SE/                 — Scope Editor wiki seed
TG/                 — Tests Guardian wiki seed
TA/                 — Tests Auditor wiki seed (minimal seed)
TE/                 — Tests Editor wiki seed (minimal seed)
BR/                 — Build Rep wiki seed
BTA/                — Build Test Auditor wiki seed
```

## Usage

1. Each agent reads its own wiki directory at session start
2. Each agent also reads UNIVERSAL/cross_agent_rules.md
3. Agents update their own wiki when new rules/corrections emerge
4. Seeds are starting points — wikis grow through live operation
