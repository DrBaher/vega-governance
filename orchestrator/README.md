# VEGA Orchestrator

Python implementation of the orchestrator described in [`docs/VEGA_Orchestrator_Technical_Spec_v3.md`](../docs/VEGA_Orchestrator_Technical_Spec_v3.md).

Self-contained. ~3,000 LOC across 14 modules + 110 pytest tests anchored to specific spec sections.

## Module map

| Module | Responsibility | Key Spec sections |
|--------|---------------|-------------------|
| `main.py` | Event loop, `initialize_project()`, CLI | §13, §14 |
| `models.py` | `Artifact`, `WikiUpdate`, `LogEntry`, `Cycle`, YAML frontmatter | §7.1 |
| `router.py` | Routing table (Framework §2 mirror) + REJ resolution | §4 |
| `executor.py` | Anthropic API call, response parsing, malformed handling | §5, §15.2 |
| `cycle_manager.py` | 8 cycle types, two-certificate TCN, 40% compression | §6 |
| `wiki_manager.py` | Read order, UNIVERSAL exclusion filter, diff logging, replace threshold | §8 |
| `artifact_store.py` | Inbox priority sort, immutable archive, recent-since filter | §7.3, §7.4 |
| `op_backlog.py` | pending → in_progress → resolved tri-state, AUTH builder | §10.1 |
| `telegram_bot.py` | 25 OP commands, multi-turn PROP, SG round-trip | §10.2, §11 |
| `sequence_manager.py` | Document IDs, decision counters (D-/TD-/GD-), instance IDs | §9 |
| `state_manager.py` | Atomic write + asyncio lock registry | §16 |
| `scheduling.py` | Daily SYS watermark + every-N-executions | §13 |
| `framework_parser.py` | Section extraction, compose_system_prompt | §5.3, §14 step 2 |
| `cortex_script.py` | CORTEX placeholder (NotImplementedError stubs) | CORTEX add-on |

## Install

```bash
cd orchestrator
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Python 3.11+. Dependencies: `anthropic`, `python-telegram-bot`, `aiofiles`, `pyyaml`.

## Configuration

`config.py` is generated per-project. To use the orchestrator in isolation, copy `templates/config.py.tpl` (from the repo root's `templates/` directory) into `orchestrator/config.py` and fill in:

- `BASE_DIR` — the project's `vega/` deployment root
- `PROJECT_NAME` — used in addendum filename and Telegram greetings
- `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_OP_CHAT_ID` — from env vars by default

All other defaults match Spec §3.1.

## Commands

```bash
# Initialize directory tree + extract system prompts from ../docs/
.venv/bin/python main.py --init-only

# Bootstrap: init + place initial input in SG inbox + start the event loop
.venv/bin/python main.py --bootstrap path/to/initial.md

# Day-to-day: just run the event loop
.venv/bin/python main.py

# One-shot SYS audit
.venv/bin/python main.py --sys --audit-request "check propagation since last week"
```

## Tests

```bash
.venv/bin/python -m pytest -q          # 110 tests, ~1.2s
bash smoke_test.sh                      # End-to-end: scaffold + init + pytest
```

Test files map to spec sections:

| Test file | Asserts |
|-----------|---------|
| `test_routing.py` | Every Framework §2 interaction has a routing entry; AUTH-OP-SG route present |
| `test_auth_lifecycle.py` | AUTH archived + appears in routing_log + delivered to SG (Spec §7.2, §13) |
| `test_concurrency.py` | Concurrent gather routing produces no lost log entries (Spec §16) |
| `test_cycle_manager.py` | All 8 cycle types open/close correctly, incl. two-certificate TCN |
| `test_cycle_compression.py` | 40% threshold compresses + emits U-RC-08 log format (Spec §6.4) |
| `test_sys_scheduling.py` | Daily SYS fires at most once per window; every_N at correct multiples |
| `test_sys_inputs.py` | SYS inbox includes recent artifacts + execution log (Spec §5.6) |
| `test_system_prompt_missing.py` | Missing prompt raises loudly with operator hint (Spec §5.3) |
| `test_malformed_output.py` | No parseable blocks → counter + agent paused at 3+ strikes (Spec §15.2) |
| `test_initialize_project.py` | Framework parser produces prompts for all 9 agents (Spec §14 step 2) |
| `test_prompt_caching.py` | System prompt wrapped with cache_control when caching enabled (Spec §5.2) |
| `test_framework_extraction.py` | Section + decision row extraction from framework MD |
| `test_artifact.py`, `test_response_parsing.py`, `test_sequence_manager.py`, `test_state_manager.py`, `test_wiki_manager.py`, `test_cortex_placeholder.py`, `test_imports.py` | Module-level coverage of core functionality |

## OP command library (Telegram)

```
/status            System overview
/backlog           Pending PROP / GOV items
/pending           Active PROP exchanges
/agent <CODE>      Agent details (instance, model, last exec, wiki size, inbox)
/agents            List all agents
/history <ID>      Routing history for an artifact
/cycles            Active conversation cycles

/sys [instruction] Trigger SYS audit (one-shot)
/wiki <CODE>       Wiki file summary
/log <CODE> [N]    Last N log entries
/thinking <ID>     Thinking blocks behind an artifact

/pause <CODE> / /resume <CODE>
/rotate <CODE>     New instance ID
/retry <CODE>      Re-execute
/model <CODE> <model> / /models

/approve <ID> / /reject <ID> <reason> / /modify <ID> <instructions>

/build <msg>       Forward External Build's reply to BR as BRQ
/expert <msg>      Forward Domain Expert's reply to SG as DE_IN

/routing <TYPE>    Where artifact type routes
/decisions [prefix] Decision counter (D / TD / GD)
/framework <§>     Extract a framework section
/help
```

Free text during an active PROP exchange continues the dialogue with SG.

If `TELEGRAM_BOT_TOKEN` is empty, the bot prints notifications to stdout instead — useful for local development without a real bot.

## Design choices worth flagging for review

- **AUTH ID via `SequenceManager.next_id("OP", "AUTH")`** rather than string-splicing the PROP ID (Spec §9). Each AUTH gets its own counter; collisions impossible across types.
- **Cycle compression at 40% calls `cycle.compress_early_turns()`** AND emits the exact spec log format including the `(U-RC-08 warning: verify post-compression)` reference. Older turns become a single summary message that explicitly warns the agent not to trust its own summary.
- **SYS audit input composition** includes (1) recent artifacts since `last_sys_run`, (2) execution log filtered since same watermark, (3) all agent wikis, (4) all agent log.md files, (5) framework excerpt, (6) optional audit_request. Without artifacts + execution log, SYS audits become wiki-only — Spec §5.6 lines 587-588 mandate the full set.
- **System prompt generation in `initialize_project()`** uses `framework_parser.compose_system_prompt(framework_text, agent_code, project_name)` to extract Framework §5.X into a §5.3-shaped prompt. Idempotent: existing files are preserved (operator customization wins). Combined with loud failure on missing prompt (Spec §5.3), there's no silent quality degradation.
- **Prompt caching** wraps both the system prompt (Spec §5.2) and the static user content (wiki + UNIVERSAL + scope) with `cache_control: {"type": "ephemeral"}`. SG/TG hot-path agents cache-hit on the second call within the TTL.
- **`atomic_write` uses tempfile + `os.replace`** (atomic on POSIX); `atomic_append` is wrapped in the appropriate `asyncio.Lock` only for shared-state files per Spec §16.1's table. Per-agent files (log.md) don't need locks — one execution per agent at a time.

## Known limitations

- The orchestrator is single-machine, single-process. For multi-tenant deployments, run multiple instances with separate `BASE_DIR`s.
- CORTEX is intentionally a placeholder (`NotImplementedError` stubs). Implement per `docs/VEGA_CORTEX_Addon_Specification_v0.2_BETA.md` if you want concept-based wiki navigation.
- The Telegram bot is intentionally thin — no chat history beyond the file-system OP backlog. All state lives on disk.
