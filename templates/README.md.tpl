# VEGA — [PROJECT_NAME]

This is a per-project VEGA deployment. It was scaffolded by the `vega` Claude Code plugin via `/vega-init` on [DEPLOY_DATE].

## What's here

```
[PROJECT_NAME]/vega/
├── framework/                    # Architecture docs (constitution)
│   ├── VEGA_Architecture_Framework_v4.md
│   ├── VEGA_Manifesto_v4.md
│   └── VEGA_[PROJECT_NAME]_Project_Addendum.md
├── agents/                       # The 9 agents — wiki, inbox, outbox per agent
│   ├── SG/, SA/, SE/, TG/, TA/, TE/, BR/, BTA/, SYS/
├── universal/                    # Cross-agent wiki (manifesto, U-rules, case index)
├── scope/                        # Source documents (ground truth)
├── test_models/                  # full/ and build/
├── artifacts/archive/            # Immutable artifact archive
├── cycles/active/                # Active conversation cycles (SCN, PROP, build Q&A)
├── op_backlog/                   # OP queue (pending / in_progress / resolved)
├── state/                        # Sequence counters, instance IDs, logs, model assignments
└── orchestrator/                 # Python orchestrator code (self-contained)
    ├── main.py
    ├── config.py
    └── ...
```

## Pre-flight

1. **Anthropic API key** — set `ANTHROPIC_API_KEY` in your shell env.
2. **Telegram bot** — create a bot via @BotFather, then set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_OP_CHAT_ID`.
3. **Verify models** — `orchestrator/config.py` lists the model IDs. Confirm against current Anthropic API docs.
4. **Place scope documents in `scope/`** — these become the ground truth for SG and SA.
5. **Confirm the Project Addendum** at `framework/VEGA_[PROJECT_NAME]_Project_Addendum.md`. Fill any `[PLACEHOLDER]` you didn't fill during init.

## First run (bootstrap)

```bash
cd vega/orchestrator
python -m pip install -r requirements.txt
python main.py --bootstrap path/to/initial_input.md
```

The bootstrap places `initial_input.md` in `agents/SG/inbox/`. SG executes, produces a PROP, and Telegram pings you for the AUTH exchange. After your `/approve` (or `/reject` / `/modify`), the SCN flows to SE, then back to SG, then PRO-SCOPE propagates to SA, TG, BR, and TA.

## Day-to-day

```bash
python main.py
```

Starts the event loop. Watch your Telegram for PROP and GOV notifications.

## OP command library

(Available in Telegram chat with the bot)

```
/status      System overview
/backlog     Pending PROP / GOV items
/agent <CODE>   Last execution, wiki size, inbox state
/history <ID>   Routing history of an artifact
/sys [instruction]   Trigger SYS audit (on-demand)
/wiki <CODE> Wiki summary
/thinking <ID>  Show the thinking blocks behind an artifact
/approve <ID> / /reject <ID> <reason> / /modify <ID> <instructions>
/build <msg> Forward to BR as BRQ-EXT
/expert <msg> Forward to SG as DE_IN
/cycles      Active conversation cycles
```

Full list is in the Orchestrator Spec §11.

## When something goes wrong

- **Agent stops producing valid output:** look at `state/execution_log.json` and the agent's `wiki/log.md`. If 3+ consecutive parse failures, Telegram pings you.
- **Routing key unknown:** orchestrator auto-creates a GOV-SYS-NNN and queues it for OP. Investigate before manually delivering the artifact.
- **Wiki replace threshold tripped:** SYS runs immediately. Read the GOV in `op_backlog/pending/` and decide.

## Reference

The four source documents in `framework/` are the authoritative reference. The Orchestrator Spec is the implementation blueprint; the Framework is the architecture; the Manifesto explains the *why*; CORTEX is the (optional, off-by-default) concept navigation add-on.

## Notes for [PROJECT_NAME]

[Anything specific the operator should remember about this project — links to Slack channels, location of source data, who to ping for the build team, etc.]
