# VEGA — V-model Enabled Governance Architecture

**A multi-agent governance framework for software project scope, test, and build management.**

VEGA is an architecture where 9 specialized Claude agents govern the lifecycle of a software project. Each agent has a defined role — guardians analyze and decide, editors apply changes, auditors verify independently — organized in two mirrored V-model lanes (scope and test) connected through a build execution layer, with a System Auditor providing governance oversight.

Four external roles interact with the system through scoped MCP connections and Telegram: the Operator (scope decisions), Admin Operator (governance and role management), Domain Expert (domain knowledge directly with the Scope Guardian), and External Build team (build interaction directly with the Build Rep). Each role sees only their domain's tools. Scope changes always flow through the Scope Guardian → Operator approval gate, regardless of who initiated them.

The system runs on a Python orchestrator that executes each agent as a stateless Anthropic API call — no long-running sessions, no context saturation.

---

## Architecture

```
    ┌────────┐ ┌──────────┐ ┌────────┐ ┌─────────┐
    │   OP   │ │ Admin OP │ │   DE   │ │   EXT   │
    │ (scope)│ │ (govern) │ │(domain)│ │ (build) │
    └───┬────┘ └────┬─────┘ └───┬────┘ └────┬────┘
        │           │           │            │
   PROP/AUTH    GOV/roles    DE_OUT/IN    BRP/BRQ
        │           │           │            │
    ┌───┴───┐  ┌────┴────┐  ┌──┴──┐    ┌───┴───┐
    │  SG   │  │   SYS   │  │ SG  │    │  BR   │
    │       ├──┤         │  │     │    │       │
    └───┬───┘  └─────────┘  └─────┘    └───┬───┘
        │                                   │
    ┌───┴────────────────────────────────┐  │
    │  SA ── SE (scope lane)             │  │
    │  TG ── TA ── TE (test lane)        │  │
    │  BTA (build test auditor)          ├──┘
    └────────────────────────────────────┘
```

### 9 Agents + 4 External Roles

| Agent | Code | Role |
|-------|------|------|
| Scope Guardian | SG | Analyzes findings, proposes dispositions to OP, issues scope changes |
| Scope Auditor | SA | Independent scope verification against domain standards |
| Scope Editor | SE | Applies scope changes precisely, reports issues via NOTE |
| Tests Guardian | TG | Derives test models from scope, triages failures |
| Tests Auditor | TA | Independent test audit — top-down and bottom-up from scope |
| Tests Editor | TE | Applies test changes, generates build-version test models |
| Build Rep | BR | Manages all external build interactions, tracks partial responses |
| Build Test Auditor | BTA | Formal pass/fail verification gate |
| System Auditor | SYS | Governance oversight, cross-agent knowledge propagation, role event audit |

| Role | Channel | Scope |
|------|---------|-------|
| **OP** (Operator) | SG via PROP/AUTH | Scope decisions. Read visibility into DE and EXT channels. |
| **Admin OP** | SYS via GOV | Governance, role management (2FA), agent configuration. Full system visibility. |
| **DE** (Domain Expert) | SG directly | Domain expertise — respond to questions, initiate observations. |
| **EXT** (External Build) | BR directly | Build interaction — submit results, ask questions. |

---

## Key Design Principles

- **Separation of generation and evaluation.** Generators don't evaluate their own output; evaluators don't generate what they evaluate. The V-model's actor-critic pattern.
- **One human decision channel for scope.** OP decides through SG only. Everything else — finding propagation, change application, test model derivation, build verification — flows automatically between agents. SYS reports governance violations to Admin OP.
- **Non-blocking.** No agent blocks on external responses. All external roles have unpredictable response times. Agents log pending items and continue.
- **Five-layer authority.** Sources > Role definitions > Own wiki > UNIVERSAL > Session context.
- **Every rule has a trace.** If it's in the wiki or UNIVERSAL, it's because something went wrong — each rule links to the case that created it (see UNIVERSAL/case_index.md).
- **Role-based external access.** Each role sees only their domain's tools. Token-scoped MCP, 2FA for role management, invite-activate onboarding with break-glass recovery.

---

## Documents

| Document | Description |
|----------|-------------|
| [Architecture Framework v6](docs/VEGA_Architecture_Framework_v6.md) | 9 agents, 4 roles, 40 decisions, interaction catalog, wiki protocol, V-model gates, tiered context views, role-based access system |
| [Manifesto v4](docs/VEGA_Manifesto_v4.md) | 8 principles, five-layer hierarchy, intellectual references |
| [Orchestrator Spec v5](docs/VEGA_Orchestrator_Technical_Spec_v5.md) | Stateless API execution, routing table, conversation cycles, role-scoped MCP + Telegram, RoleManager, 2FA, break-glass recovery |
| [CORTEX Addon v0.2 BETA](docs/VEGA_CORTEX_Addon_Specification_v0.2_BETA.md) | Optional concept topology: 4 navigation modes, externalized math, wiki-scale optimization |

---

## Quick Start

### Prerequisites

- Python 3.11+
- Anthropic API key
- Telegram bot token ([create one via BotFather](https://t.me/BotFather))

### Deploy with Claude Code

Give Claude Code the full document set from `docs/` and it will build the orchestrator from the specification:

```bash
cd vega-governance
claude "Build the VEGA orchestrator from the specs in docs/"
```

The Orchestrator Spec is the implementation blueprint; the other documents provide architectural context.

---

## CORTEX Add-on

CORTEX (Compiled Orchestrated Role Topology for EXecution) adds concept-based wiki navigation for agents whose wikis grow beyond ~15 entries. It runs as an external script — agents see ranked lists, not math. Deploys with `CORTEX_ENABLED=False` by default; activates per-agent automatically as wikis cross the threshold.

---

## Project-Specific Deployment

VEGA is project-agnostic. For a specific project, add:

1. **Project Addendum** — domain expert identity, document manifest, test decomposition levels. See [template](examples/project_addendum_template.md).
2. **Project Wiki Seeds** — role-specific knowledge layered on top of the general seeds.

---

## Architecture Origins

- **Herbert Simon** — bounded rationality, satisficing
- **Miller/Cowan** — cognitive limits (7±2, 4-slot working memory)
- **Fred Brooks** — essential vs accidental complexity
- **Andrej Karpathy** — LLM knowledge compilation, persistent wiki pattern
- **Vannevar Bush** — Memex: personal curated knowledge with associative trails
- **V-Model (systems engineering)** — decomposition left, verification right

---

## License

[Apache License 2.0](LICENSE)

---

## Author

**Francisco Vega** — [GitHub](https://github.com/FreeValley)
