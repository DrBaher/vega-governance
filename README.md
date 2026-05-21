# VEGA — V-model Enabled Governance Architecture

**A multi-agent governance framework for software project scope, test, and build management.**

VEGA is an architecture where 9 specialized Claude agents govern the lifecycle of a software project. Each agent has a defined role — guardians analyze and decide, editors apply changes, auditors verify independently — organized in two mirrored V-model lanes (scope and test) connected through a build execution layer, with a System Auditor providing governance oversight.

The operator (human) makes all scope decisions through a single channel with the Scope Guardian, while everything else — finding propagation, change application, test model derivation, build verification — flows automatically between agents. Agent knowledge persists across sessions via per-agent wikis, and a shared UNIVERSAL wiki carries cross-agent rules.

The system runs on a Python orchestrator that executes each agent as a stateless Anthropic API call — no long-running sessions, no context saturation. The operator interacts via Telegram.

---

## Architecture

```
         ┌──────────────────────────────────────┐
         │            OPERATOR (OP)              │
         │     Telegram: /approve /reject        │
         └──────────┬──────────────┬─────────────┘
                    │              │
              PROP/AUTH        GOV (violations)
                    │              │
    ┌───────────────┴──┐    ┌──────┴──────┐
    │  SCOPE LANE       │    │  SYS        │
    │  SG ←→ SA         │    │  Governance │
    │  SG ←→ SE (edits) │    │  Oversight  │
    └────────┬──────────┘    └─────────────┘
             │ PRO-SCOPE
    ┌────────┴──────────┐
    │  TEST LANE         │
    │  TG ←→ TA          │
    │  TG ←→ TE (edits)  │
    └────────┬───────────┘
             │ PRO-TEST
    ┌────────┴──────────┐
    │  BUILD LAYER       │
    │  BR ←→ EXT (build) │
    │  BTA (validation)  │
    └────────────────────┘
```

### 9 Agents

| Agent | Code | Role |
|-------|------|------|
| Scope Guardian | SG | Analyzes findings, proposes dispositions to OP, issues scope changes |
| Scope Auditor | SA | Independent scope verification against domain standards |
| Scope Editor | SE | Applies scope changes precisely, reports issues via NOTE |
| Tests Guardian | TG | Derives test models from scope, triages failures |
| Tests Auditor | TA | Independent test audit — top-down and bottom-up from scope |
| Tests Editor | TE | Applies test changes, generates build-version test models |
| Build Rep | BR | Manages all external build interactions |
| Build Test Auditor | BTA | Formal pass/fail verification gate |
| System Auditor | SYS | Governance oversight, cross-agent knowledge propagation |

---

## Key Design Principles

- **Stateless execution.** Each agent invocation = one API call. Wiki is the memory. Context saturation is structurally impossible.
- **Separation of generation and evaluation.** The V-model's actor-critic pattern: generators don't evaluate their own output, evaluators don't generate what they evaluate.
- **One human decision channel.** OP decides through SG only. Everything else propagates automatically.
- **Five-layer authority.** Sources > Role definitions > Own wiki > UNIVERSAL > Session context.
- **Every rule has a trace.** If it's in the wiki, it's because something went wrong. The case index (19 U-RC entries) provides universal trace references.

---

## Documents

| Document | Description |
|----------|-------------|
| [Architecture Framework v4](docs/VEGA_Architecture_Framework_v4.md) | 9 agents, 31 decisions, interaction catalog, wiki protocol, V-model gates |
| [Manifesto v4](docs/VEGA_Manifesto_v4.md) | 8 principles, five-layer hierarchy, intellectual references |
| [Orchestrator Spec v3](docs/VEGA_Orchestrator_Technical_Spec_v3.md) | Stateless API execution, routing table, conversation cycles, Telegram interface |
| [CORTEX Addon v0.2 BETA](docs/VEGA_CORTEX_Addon_Specification_v0.2_BETA.md) | Optional concept topology: 4 navigation modes, externalized math, wiki-scale optimization |

---

## Quick Start

### Prerequisites

- Python 3.11+
- Anthropic API key
- Telegram bot token ([create one via BotFather](https://t.me/BotFather))

### Install

```bash
pip install vega-governance
```

### Initialize a Project

```bash
vega init --project-name "my-project" \
          --anthropic-key $ANTHROPIC_API_KEY \
          --telegram-token $TELEGRAM_BOT_TOKEN \
          --telegram-chat-id $TELEGRAM_CHAT_ID
```

This creates the directory structure, seeds agent wikis, extracts system prompts from the framework, and starts the orchestrator. Initial project scope goes into the SG inbox — the system takes it from there.

### Deploy with Claude Code

Alternatively, give Claude Code the full document set from `docs/` and it will build and deploy the orchestrator from the specification:

```bash
claude "Build the VEGA orchestrator from the specs in docs/"
```

---

## CORTEX Add-on

CORTEX (Compiled Orchestrated Role Topology for EXecution) adds concept-based wiki navigation for agents whose wikis grow beyond ~15 entries. It runs as an external script in the orchestrator pipeline — agents see ranked lists, not math. Deploys with `CORTEX_ENABLED=False` by default; activates per-agent automatically as wikis cross the threshold.

---

## Project-Specific Deployment

VEGA is project-agnostic. For a specific project, add:

1. **Project Addendum** — domain expert identity, document manifest, test decomposition levels, domain-specific knowledge. See [template](examples/project_addendum_template.md).
2. **Project Wiki Seeds** — role-specific knowledge from prior project sessions, layered on top of the general seeds.

---

## Architecture Origins

VEGA draws on:

- **Herbert Simon** — bounded rationality, satisficing, administrative behavior
- **Miller/Cowan** — cognitive limits (7±2 chunks, 4-slot working memory)
- **Fred Brooks** — essential vs accidental complexity, no silver bullet
- **Andrej Karpathy** — LLM knowledge compilation, persistent wiki pattern
- **Vannevar Bush** — Memex: personal curated knowledge with associative trails
- **V-Model (systems engineering)** — decomposition on the left, verification on the right, bottom-up

---

## License

[Apache License 2.0](LICENSE)

---

## Author

**Francisco Vega** — [GitHub](https://github.com/YOUR_USERNAME)

VEGA was designed and built through adversarial architectural review: every agent role, every interaction, every wiki protocol, every governance rule was challenged, broken, and rebuilt until it held. The framework exists because a human with bounded rationality needed a way to extend cognitive reach across a complex project without losing control.
