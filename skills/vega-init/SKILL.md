---
name: vega-init
description: Deploy VEGA (V-model Enabled Governance Architecture) for a new project. Scaffolds the 9-agent governance system — directory structure, wiki seeds, orchestrator code, Project Addendum, and config — based on a short Q&A with the operator. Use when the user wants to "set up VEGA", "initialize VEGA for X", "start a new VEGA project", or types /vega-init.
---

# VEGA Init

You are scaffolding a new VEGA deployment for a project. The plugin root is the directory containing this SKILL.md's grandparent — typically `~/.claude/plugins/vega-governance/` if the upstream repo was cloned with that name. Determine the plugin root from your environment (`$CLAUDE_PLUGIN_ROOT` if available; otherwise search `~/.claude/plugins/` for a directory containing `.claude-plugin/plugin.json` with `"name": "vega"`).

## Plugin layout (your inputs — at the plugin root)

```
<PLUGIN_ROOT>/
├── docs/                          # 4 source docs (Framework v4, Manifesto v4,
│                                  #  Orchestrator Spec v3, CORTEX Addon v0.2)
├── wiki_seeds/                    # General wiki seeds (9 agents + UNIVERSAL,
│                                  #  including 19 U-RC entries)
├── examples/
│   └── project_addendum_template.md   # The authoritative addendum template
├── orchestrator/                  # Python orchestrator (~3,000 LOC, 110 tests)
└── templates/
    ├── config.py.tpl              # Per-project orchestrator config
    └── README.md.tpl              # Per-project README
```

## Target layout (what you build)

Per Orchestrator Spec §2, scaffold this tree under `<TARGET_DIR>/vega/`:

```
<TARGET_DIR>/vega/
├── framework/                    # Reference docs + project addendum
├── agents/SG, SA, SE, TG, TA, TE, BR, BTA, SYS/
│   └── {wiki/, inbox/, outbox/, system_prompt.md}
├── universal/                    # manifesto.md, cross_agent_rules.md, case_index.md, log_template.md
├── scope/                        # Source documents (operator drops these in)
├── test_models/{full,build}/
├── artifacts/archive/
├── cycles/active/
├── op_backlog/{pending,in_progress,resolved}/
├── state/
├── orchestrator/                 # Python orchestrator (self-contained per project)
└── README.md                     # Bootstrap instructions
```

## Step 1 — Determine target directory

Ask the user where the VEGA deployment should live. Default suggestion: a `vega/` subdirectory of the current working directory. If they specify an absolute path, use that. **Never deploy on top of an existing `vega/` directory without confirmation.**

Check first:
```bash
test -e <TARGET>/vega && echo EXISTS || echo OK
```

If it exists, ask whether to abort, overwrite, or pick a different path.

## Step 2 — Ask the setup questions

Use `AskUserQuestion` to collect (group related questions into single AskUserQuestion calls — max 4 per call):

**Project identity**
- Project name (short, no spaces — used in filenames and addendum title)
- Domain (e.g., "medical laboratory codes", "financial reporting", "supply chain")
- Operator name and email (for the addendum)

**Scope documents**
- Where do the project's scope/spec documents currently live? (path)
- Are they ready to be copied to `scope/`, or will the operator add them later?

**Domain Expert**
- Assigned Domain Expert name + contact
- Domain area they're authoritative on
- At-launch communication channel (manual OP relay via Telegram is the default per Spec §12.2)

**Test decomposition**
- Use default L0–L3 (foundational data → sub-components → components → end-to-end)? Or define custom levels?
- If custom: collect level names and what each verifies

**Continuation vs. fresh start**
- Fresh project or continuing prior work?
- If continuing: collect sequence offsets (SCN, decisions D-/TD-/GD-, FND, DOC, etc.)

**Build interface**
- Build team name (or "internal/TBD")
- At-launch communication channel (OP relay default)

**CORTEX**
- Activate from day one (cold start) or hold off until wikis grow? Default off.

**Initial input**
- Path to the initial directive/spec for SG to ingest at bootstrap, or "to be drafted later"

## Step 3 — Scaffold the directory tree

```bash
mkdir -p <TARGET>/vega/{framework,universal,scope,test_models/{full,build},artifacts/archive,cycles/active,state,op_backlog/{pending,in_progress,resolved},orchestrator}
for agent in SG SA SE TG TA TE BR BTA SYS; do
  mkdir -p <TARGET>/vega/agents/$agent/{wiki,inbox,outbox}
done
```

## Step 4 — Copy reference docs to `framework/`

```bash
cp <PLUGIN_ROOT>/docs/VEGA_Architecture_Framework_v4.md <TARGET>/vega/framework/
cp <PLUGIN_ROOT>/docs/VEGA_Manifesto_v4.md <TARGET>/vega/framework/
cp <PLUGIN_ROOT>/docs/VEGA_Orchestrator_Technical_Spec_v3.md <TARGET>/vega/framework/
cp <PLUGIN_ROOT>/docs/VEGA_CORTEX_Addon_Specification_v0.2_BETA.md <TARGET>/vega/framework/
```

## Step 5 — Copy wiki seeds

```bash
for agent in SG SA SE TG TA TE BR BTA SYS; do
  cp -R <PLUGIN_ROOT>/wiki_seeds/$agent/. <TARGET>/vega/agents/$agent/wiki/
done
cp -R <PLUGIN_ROOT>/wiki_seeds/UNIVERSAL/. <TARGET>/vega/universal/
```

## Step 6 — Agent system prompts (usually automatic)

The orchestrator's `main.initialize_project()` automatically extracts role definitions from the framework and writes `agents/<CODE>/system_prompt.md` for each agent (Spec §14 step 2, implemented via `framework_parser.compose_system_prompt`). The generation is idempotent — existing files are not clobbered.

You typically don't need to do anything here. First bootstrap (`python main.py --bootstrap` or `python main.py --init-only`) generates the prompts from `framework/VEGA_Architecture_Framework_v4.md` §5.X for each agent.

Generate them manually now only if the operator wants to embed project-specific context not derivable from the framework, OR review/customize prompts before first bootstrap. The orchestrator's idempotent generator will preserve your versions.

## Step 7 — Copy orchestrator code

```bash
cp -R <PLUGIN_ROOT>/orchestrator/. <TARGET>/vega/orchestrator/
```

Copies the full Python orchestrator (~3,000 lines + 110 tests, self-contained) into the new project.

## Step 8 — Fill and write the Project Addendum

Read `<PLUGIN_ROOT>/examples/project_addendum_template.md` (the authoritative VEGA addendum template). Substitute the placeholders with the answers from Step 2. Write to:

```
<TARGET>/vega/framework/VEGA_<PROJECT_NAME>_Project_Addendum.md
```

Leave any placeholder the user didn't answer as `[TBD — fill in later]` so it's greppable.

## Step 9 — Fill and write `config.py`

Read `<PLUGIN_ROOT>/templates/config.py.tpl`. Substitute `[PROJECT_NAME]` and `[DEPLOY_DATE]`. Write to:

```
<TARGET>/vega/orchestrator/config.py
```

## Step 10 — Fill and write the per-project README

Read `<PLUGIN_ROOT>/templates/README.md.tpl`. Substitute placeholders. Write to:

```
<TARGET>/vega/README.md
```

## Step 11 — Initialize sequence counters

Write `<TARGET>/vega/state/sequences.json` with the offsets from Step 2 (or `{}` if all fresh). Example:

```json
{
  "SG:SCN": 0,
  "SG:DECISION": 0,
  "SG:PROP": 0,
  "SA:FND": 0,
  "SE:DOC": 0,
  "TG:TCN": 0,
  "TG:DECISION": 0,
  "TA:FND": 0,
  "SYS:GOV": 0,
  "SYS:DECISION": 0
}
```

Also write `<TARGET>/vega/state/instance_ids.json` with `{}` (instances auto-create on first execution).

## Step 12 — Place initial input in SG inbox (if provided)

If the user gave a path to initial input in Step 2:

```bash
cp <INITIAL_INPUT_PATH> <TARGET>/vega/agents/SG/inbox/INIT-OP-001.md
```

Prepend a YAML frontmatter block per Spec §7.1:

```yaml
---
id: INIT-OP-001
type: INIT
sender: OP
timestamp: <ISO8601>
status: unprocessed
---
```

Otherwise tell the user where to drop it later.

## Step 13 — Final report

Summarize what was scaffolded and present the bootstrap checklist:

1. Set `ANTHROPIC_API_KEY` in shell env
2. Create Telegram bot (BotFather) — set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_OP_CHAT_ID`
3. Verify model IDs in `orchestrator/config.py` against current Anthropic API docs
4. Drop scope documents into `scope/`
5. Review `framework/VEGA_<PROJECT_NAME>_Project_Addendum.md` — fill any `[TBD]`
6. Install Python deps: `cd vega/orchestrator && python -m venv .venv && .venv/bin/pip install -r requirements.txt`
7. Bootstrap: `.venv/bin/python main.py --bootstrap <path-to-initial-input>` — also auto-generates `agents/<CODE>/system_prompt.md` from the framework on first run
8. Then `.venv/bin/python main.py` for day-to-day

Show absolute paths so the user can navigate directly.

## Constraints and guardrails

- **Never overwrite existing scope documents.** If `<TARGET>/vega/scope/` is non-empty, stop and ask.
- **Never invent agent codes or document types.** Stick to the 9 agents and document types in Framework §1.
- **The framework documents in `<TARGET>/vega/framework/` are reference, not edit targets.** Project-specific overrides go in the Project Addendum.
- **Don't deploy a half-broken state.** If a step fails, report the error, halt, and tell the user what's missing.
- **All paths must be absolute** when reporting back to the user.
- **The orchestrator code lives at `<TARGET>/vega/orchestrator/`.** Each project gets its own self-contained instance with its own `.venv/`.

## What this skill does NOT do

- Start the orchestrator daemon (operator does this manually after pre-flight)
- Create the Telegram bot (operator does this via BotFather)
- Write Anthropic or Telegram credentials (operator sets env vars)
- Modify the source docs (the project addendum is the only project-specific document)
- Fetch or modify scope documents from a remote (the operator places them in `scope/`)
- Make architectural changes — VEGA is what the Framework says it is. Customization happens through the Project Addendum and the wikis.
