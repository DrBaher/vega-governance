# V-model Enabled Governance Architecture (VEGA)
## Orchestrator — Technical Specification v5

**Author:** Francisco
**Date:** May 2026
**For:** Claude Code implementation
**References:** VEGA Architecture Framework v6.0 (all §-references point to this document)

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Directory Structure](#2-directory-structure)
3. [Configuration](#3-configuration)
4. [Routing Table](#4-routing-table)
5. [Agent Execution Model](#5-agent-execution-model)
6. [Conversation Cycles](#6-conversation-cycles)
7. [Artifact Store](#7-artifact-store)
8. [Wiki Manager](#8-wiki-manager)
9. [Sequence Manager](#9-sequence-manager)
10. [OP Backlog and Telegram Bot](#10-op-backlog-and-telegram-bot)
11. [OP Command Library](#11-op-command-library)
12. [External Interfaces](#12-external-interfaces)
13. [Main Event Loop](#13-main-event-loop)
14. [Initialization](#14-initialization)
15. [Error Handling](#15-error-handling)
16. [Concurrency and State Safety](#16-concurrency-and-state-safety)
17. [Monitoring and Observability](#17-monitoring-and-observability)
18. [Session Semantics Translation](#18-session-semantics-translation)
19. [Dependencies](#19-dependencies)
20. [Launch Checklist](#20-launch-checklist)
21. [Mapping to Framework References](#21-mapping-to-framework-references)

---

## 1. System Overview

The VEGA Orchestrator is a Python program that runs 9 governance agents as stateless Anthropic API calls, routes artifacts between them per the interaction catalog (Framework §2), manages conversation cycles for multi-turn interactions, and presents OP-requiring items via Telegram.

### 1.1 Design Principles

1. **Stateless execution.** Each agent invocation = one API call. No persistent server-side state. The wiki IS the memory.
2. **Conversation cycles.** Bounded multi-turn interactions (SCN application, OP exchanges, build Q&A) maintain a messages array across executions. The array is discarded when the cycle closes.
3. **Non-blocking.** OP items go to a backlog queue. All other agents continue working on their current state.
4. **File-system-based.** Wikis, artifacts, inboxes, outboxes — all files on disk. The file system is the database.
5. **Routing table from the architecture.** The interaction catalog (§2) is the routing configuration. No routing logic is invented.
6. **Role-based external access.** Four roles (OP, Admin OP, DE, EXT) interact via scoped MCP tokens + Telegram. Each role sees only their domain's tools. Unauthenticated MCP connections see only the access request lobby.
7. **Extended thinking enabled.** All agent API calls use extended thinking. Thinking blocks are stored in the execution log for OP review and SYS audit. The agent's reasoning is as visible as its output.

### 1.2 Components

```
┌──────────────────────────────────────────────────────┐
│                  VEGA Orchestrator                     │
│                                                        │
│  ┌─────────────┐  ┌─────────────┐  ┌───────────────┐ │
│  │   Router     │  │  Executor   │  │ Wiki Manager  │ │
│  │  (table §2)  │  │ (API calls  │  │ (read/write   │ │
│  │              │  │  + cycles)  │  │  + diff log)  │ │
│  └──────┬───────┘  └──────┬──────┘  └───────┬───────┘ │
│         │                 │                  │         │
│  ┌──────┴─────────────────┴──────────────────┴──────┐ │
│  │              Artifact Store                       │ │
│  │  (inbox / outbox / archive — immutable archive)   │ │
│  └──────────────────────┬────────────────────────────┘ │
│                         │                               │
│  ┌──────────────────────┴────────────────────────────┐ │
│  │              Cycle Manager                         │ │
│  │  (messages arrays per active cycle)                │ │
│  └──────────────────────┬────────────────────────────┘ │
│                         │                               │
│  ┌──────────────────────┴────────────────────────────┐ │
│  │              Backlog Queues                         │ │
│  │  OP backlog (PROP) + Admin backlog (GOV)            │ │
│  └──────────────────────┬────────────────────────────┘ │
└─────────────────────────┼──────────────────────────────┘
                          │
                 ┌────────┴────────┐
                 │  Telegram Bot    │
                 │  (OP interface)  │
                 └─────────────────┘
```

---

## 2. Directory Structure

```
vega/
├── orchestrator/
│   ├── main.py                  # Entry point — event loop
│   ├── router.py                # Routing table + routing logic
│   ├── executor.py              # Anthropic API agent execution
│   ├── cycle_manager.py         # Conversation cycle tracking
│   ├── wiki_manager.py          # Wiki read/write with diff logging
│   ├── artifact_store.py        # Inbox/outbox/archive management
│   ├── backlog.py               # Backlog queue management (OP + Admin OP)
│   ├── telegram_bot.py          # Telegram interface for OP
│   ├── config.py                # Configuration
│   ├── models.py                # Data models (Artifact, Agent, Cycle, etc.)
│   ├── sequence_manager.py      # Document ID + decision sequence counters
│   ├── state_manager.py         # Atomic state operations with locking
│   ├── mcp_server.py            # MCP server — role-scoped tools for all roles
│   ├── role_manager.py          # Role assignment, onboarding, 2FA, token management
│   └── cortex_script.py         # CORTEX computation (when CORTEX is active)
│
├── framework/
│   ├── VEGA_Architecture_Framework_v6.md
│   ├── VEGA_Manifesto_v4.md
│   └── VEGA_<Project>_Addendum.md        # (project-specific, if present)
│
├── agents/
│   ├── SG/
│   │   ├── system_prompt.md
│   │   ├── wiki/
│   │   │   ├── SCHEMA.md
│   │   │   ├── index.md
│   │   │   ├── log.md
│   │   │   ├── process_rules.md
│   │   │   ├── reasoning_corrections.md
│   │   │   └── [role_specific].md
│   │   ├── inbox/
│   │   └── outbox/
│   ├── SA/ ... SE/ ... TG/ ... TA/ ... TE/ ... BR/ ... BTA/ ... SYS/
│   │   (same structure per agent)
│
├── universal/
│   ├── manifesto.md
│   ├── cross_agent_rules.md
│   ├── case_index.md
│   └── log_template.md
│
├── scope/                       # Source documents (ground truth)
│
├── test_models/
│   ├── full/
│   └── build/
│
├── artifacts/
│   └── archive/                 # Immutable — never modified after write
│
├── cycles/
│   └── active/                  # Active conversation cycle threads
│       ├── SCN-SG-003.json      # Messages array for SCN cycle
│       ├── PROP-SG-005.json     # Messages array for PROP exchange
│       └── BR-EXT-scope-v7.json # Messages array for build Q&A
│
├── op_backlog/                   # OP items (PROP)
│   ├── pending/
│   ├── in_progress/
│   └── resolved/
│
├── admin_backlog/                # Admin OP items (GOV + role requests)
│   ├── pending/
│   ├── in_progress/
│   └── resolved/
│
├── config/
│   ├── roles.json                # Role assignments (hashed tokens, telegram IDs)
│   ├── recovery.hash             # Break-glass recovery key (hashed)
│   ├── notifications.json        # Per-role notification preferences
│   └── invites/                  # Pending invite codes (auto-cleaned on expiry)
│
└── state/
    ├── sequences.json
    ├── instance_ids.json
    ├── model_assignments.json
    ├── routing_log.json
    ├── execution_log.json
    ├── artifact_index.json       # Artifact ID → execution ID lookup (for /thinking)
    ├── wiki_replace_counters.json # Per-agent replace_section counts since last SYS run
    └── role_events.jsonl          # Append-only operational audit trail — role management + snapshot/restore events (SYS reads)
```

---

## 3. Configuration

### 3.1 config.py

```python
AGENTS = ["SG", "SA", "SE", "TG", "TA", "TE", "BR", "BTA", "SYS"]

# Default model — verify strings against API docs at deployment
AGENT_MODEL = "claude-sonnet-4-6"

# Per-agent model overrides (configurable via /model command at runtime)
AGENT_MODEL_OVERRIDES = {
    "SG": "claude-opus-4-7",
    "TG": "claude-opus-4-7",
    "SE": "claude-haiku-4-5",
    "TE": "claude-haiku-4-5",
}

# Extended thinking
EXTENDED_THINKING_ENABLED = True
# Thinking parameter shape is API-version-dependent:
#   Claude 4.x+: {"type": "adaptive"} with output_config.effort
#   Older models: {"type": "enabled", "budget_tokens": N}
# The orchestrator tries supported modes in order and falls back gracefully.
THINKING_BUDGET_TOKENS = 10000  # Used for legacy mode if needed

# Prompt caching — static content (system prompt, UNIVERSAL, wiki) is cached
# across executions of the same agent. Reduces input token cost significantly.
PROMPT_CACHING_ENABLED = True

MAX_TOKENS = 16000  # Per agent execution (output)

# Paths
BASE_DIR = "/path/to/vega"
AGENTS_DIR = f"{BASE_DIR}/agents"
UNIVERSAL_DIR = f"{BASE_DIR}/universal"
SCOPE_DIR = f"{BASE_DIR}/scope"
ARTIFACTS_DIR = f"{BASE_DIR}/artifacts/archive"
CYCLES_DIR = f"{BASE_DIR}/cycles/active"
OP_BACKLOG_DIR = f"{BASE_DIR}/op_backlog"
ADMIN_BACKLOG_DIR = f"{BASE_DIR}/admin_backlog"
STATE_DIR = f"{BASE_DIR}/state"

# Telegram
TELEGRAM_BOT_TOKEN = "..."
TELEGRAM_OP_CHAT_ID = "..."
TELEGRAM_EXCHANGE_ENABLED = False  # Freeform exchange disabled — use MCP vega_exchange

# MCP Server
MCP_ENABLED = True
MCP_PORT = 8420
# No single MCP_AUTH_TOKEN — tokens are per-role in config/roles.json

# Roles (stored in config/roles.json — hashed tokens, never plain text)
# Managed by Admin OP at runtime via /role commands
# See Framework §13 for role definitions and onboarding flow
ROLES_FILE = f"{BASE_DIR}/config/roles.json"
INVITE_EXPIRY = 24 * 60 * 60  # 24 hours
OTP_EXPIRY = 5 * 60           # 5 minutes

# Polling
POLL_INTERVAL = 10  # Seconds between inbox checks

# SYS scheduling
SYS_SCHEDULE = "daily"       # "daily" | "every_N_executions" | "hourly"
SYS_DAILY_TIME = "02:00"
SYS_EXECUTION_THRESHOLD = 20 # If "every_N_executions"

# Cycle context monitoring
CYCLE_CONTEXT_WARNING = 0.4  # Warn at 40% of context window

# Wiki replace_section monitoring
WIKI_REPLACE_THRESHOLD_DEFAULT = 10  # Trigger SYS review after N replaces between audits
WIKI_REPLACE_THRESHOLDS = {
    # Per-agent overrides, adjusted by OP based on SYS recommendations via GOV
    # Example: "SG": 15, "TA": 3
}

# CORTEX (add-on)
CORTEX_ENABLED = False
CORTEX_SCAN_THRESHOLD = 15

# Validated-State Snapshots (§7.5)
SNAPSHOT_ENABLED = True
SNAPSHOT_LOCAL_DIR = "/var/vega-snapshots"  # Outside orchestrator tree
SNAPSHOT_GIT_REMOTE = None                  # Optional — "origin" to push to git

# External Build
EXT_BUILD_ENABLED = True
```

### 3.2 Agent Access Permissions

| Agent | Reads | Writes |
|-------|-------|--------|
| SG | own wiki, universal/, scope/, framework/ (guardian view §12.2), inbox/ | own wiki, outbox/ |
| SA | own wiki, universal/, scope/, framework/ (summary §12.1), inbox/ | own wiki, outbox/ |
| SE | own wiki, universal/, scope/, inbox/ | own wiki, outbox/, scope/ (only via DOC) |
| TG | own wiki, universal/, scope/, framework/ (guardian view §12.2), inbox/, test_models/full/ | own wiki, outbox/, test_models/full/ |
| TA | own wiki, universal/, scope/, framework/ (summary §12.1), inbox/, test_models/full/ | own wiki, outbox/ |
| TE | own wiki, universal/, inbox/, test_models/full/, test_models/build/ | own wiki, outbox/, test_models/full/, test_models/build/ |
| BR | own wiki, universal/, scope/, framework/ (summary §12.1), inbox/, test_models/build/ | own wiki, outbox/ |
| BTA | own wiki, universal/, framework/ (summary §12.1), inbox/, test_models/full/ | own wiki, outbox/ |
| SYS | ALL wikis (read), universal/, framework/ (SYS view §12.3), artifacts/archive/, ALL log.md, execution_log.json, role_events.jsonl, snapshots/ | own wiki, universal/ (write), outbox/ |

---

## 4. Routing Table

Derived exhaustively from Framework §2. Single source of truth.

### 4.1 Agent-to-Agent Routes

Routing key: `(sender, type)` for most artifacts. `(sender, type, ref_type)` for response artifacts (REJ).

```python
ROUTING_TABLE = {
    # Scope Lane (§2.1)
    ("SA", "FND"):          [{"to": "SG"}],
    ("SG", "REJ", "FND"):   [{"to": "SA"}],     # S2: rejecting SA finding
    ("SG", "REJ", "DEV"):   [{"to": "BR"}],     # Flow 7: rejecting BR deviation
    ("SG", "REJ", "ESC"):   [{"to": "TG"}],     # Rejecting TG escalation
    ("SG", "SCN"):          [{"to": "SE"}],
    ("SE", "DOC"):          [{"to": "SG"}],
    ("SE", "NOTE"):         [{"to": "SG"}],
    ("SG", "VAL"):          [{"to": "SE"}],
    ("SG", "REV"):          [{"to": "SE"}],
    ("SG", "PRO-SCOPE"):    [{"to": "SA"}, {"to": "BR"}, {"to": "TG"}, {"to": "TA"}],
    ("SG", "SUM"):          [{"to": "ARCHIVE"}],  # Exchange summary — archived only

    # Test Lane (§2.2)
    ("TG", "TCN"):          [{"to": "TE"}],
    ("TE", "DOC"):          [{"to": "TG"}],
    ("TE", "NOTE"):         [{"to": "TG"}],
    ("TG", "VAL"):          [{"to": "TE"}],
    ("TG", "REV"):          [{"to": "TE"}],
    ("TG", "PRO-TEST-FULL"):  [{"to": "TA"}, {"to": "BTA"}],
    ("TG", "PRO-TEST-BUILD"): [{"to": "BR"}],
    ("TA", "FND"):          [{"to": "TG"}],
    ("TG", "REJ", "FND"):   [{"to": "TA"}],     # T7: rejecting TA finding

    # Cross-Lane (§2.3)
    ("TG", "ESC"):          [{"to": "SG"}],

    # Build Layer (§2.4)
    ("BR", "TSR"):          [{"to": "BTA"}],
    ("BTA", "TFR"):         [{"to": "TG"}],
    ("BTA", "VR"):          [{"to": "BR"}],
    ("BR", "DEV"):          [{"to": "SG"}],
    ("TG", "TRI"):          [{"to": "BR"}],

    # External Build (§2.5) — routed for archive + log
    ("BR", "BRP"):          [{"to": "EXT"}],
    ("EXT", "BRQ"):         [{"to": "BR"}],

    # Domain Expert (§2.6) — routed through SG
    ("SG", "DE_OUT"):       [{"to": "DE"}],
    ("DE", "DE_IN"):        [{"to": "SG"}],

    # Operator Request (§2.1 S13)
    ("OP", "REQ"):          [{"to": "SG"}],

    # AUTH from OP (§7.2) — archived immutably and delivered to SG
    ("OP", "AUTH"):         [{"to": "SG"}],

    # Backlog-bound routes (non-blocking, delivered to human role backlogs)
    # Router checks for "backlog" key — if present, routes to backlog + notify.
    # If absent, delivers to agent inbox. One table, branch on entry shape.
    ("SG", "PROP"):         [{"to": "OP", "backlog": "op_backlog/pending",
                              "notify_role": "OP", "exchange_mode": True,
                              "exchange_partner": "SG"}],
    ("SYS", "GOV"):         [{"to": "ADMIN_OP", "backlog": "admin_backlog/pending",
                              "notify_role": "ADMIN_OP", "exchange_mode": True,
                              "exchange_partner": "SYS"}],
}
```

### 4.2 Routing Rules

1. When an agent produces an artifact in its outbox, the router picks it up.
2. Router reads `type`, `sender`, and (for response types) `ref_type` from the artifact metadata.
3. For each recipient: copies artifact to `agents/{recipient}/inbox/`.
4. Archives to `artifacts/archive/`. **Archive is immutable — never modified after write.**
5. Appends to `state/routing_log.json`.
6. If the routing entry has a `backlog` key: copies to the specified backlog directory, sends Telegram notification to the specified role (best-effort — see §10.2). Does NOT deliver to agent inbox.
7. If no `backlog` key: delivers to agent inbox (`agents/{recipient}/inbox/`). For EXT/DE-bound types: see §12 External Interfaces.
8. **Unknown routing key** (type+sender not in table): log as governance violation, auto-create GOV with timestamp-based ID (`GOV-SYS-AUTO-<epoch_ms>`) — the AUTO prefix distinguishes from SYS-minted GOV. Route GOV to Admin OP backlog (`admin_backlog`). Do not deliver artifact. SYS can re-issue with a proper sequence ID during its next audit if needed. Auto-generated GOV artifacts remain in the archive permanently (immutable). When SYS re-issues, the new GOV-SYS-NNN references the auto-GOV in its references field.

### 4.4 REJ Routing Resolution

REJ artifacts include a `references` field pointing to the original artifact being rejected. The router reads the referenced artifact's type to determine ref_type:

```python
def resolve_rej_recipient(artifact):
    ref_id = artifact.references[0]
    ref_artifact = artifact_store.load_from_archive(ref_id)
    ref_type = ref_artifact.type  # "FND", "DEV", or "ESC"
    routing_key = (artifact.sender, "REJ", ref_type)
    return ROUTING_TABLE[routing_key]
```

This is the only place the router reads an archived artifact. All other routing is pure table lookup.

---

## 5. Agent Execution Model

### 5.1 Execution Trigger

**`flag_for_execution(agent_code, cycle_id=None)`** — marks an agent for execution on the next tick. Two use cases:

1. **Cycle-turn trigger:** A human role sends a message in an exchange → the partner agent needs to execute to respond. Called with `cycle_id` for traceability (which cycle triggered the execution). Example: OP sends exchange message → `flag_for_execution("SG", cycle_id=cycle.id)`.

2. **Direct execution (/run):** Admin OP wants an agent to execute immediately (changed model, updated wiki, wants to see the effect). Called without `cycle_id` — no cycle context, normal inbox execution. Example: `/run SG` → `flag_for_execution("SG")`.

The agent itself doesn't need to know why it was flagged. It reads its inbox and processes whatever's there — cycle context comes from the inbox content, not the flag. `cycle_id` is for operational tracing only.

```python
async def check_and_execute(agent_code: str):
    inbox = get_inbox(agent_code)
    if not inbox.has_unprocessed():
        return

    # Determine if this execution is part of an active cycle
    cycle = cycle_manager.get_active_cycle(agent_code, inbox.peek())
    
    # Build the API call
    system_prompt = load_system_prompt(agent_code)
    wiki_content = wiki_manager.read_all(agent_code)
    universal_content = wiki_manager.read_universal(agent_code)  # Filters excluded_for per D-ARCH-031
    scope_docs = load_scope_docs(agent_code)
    inbox_items = inbox.get_unprocessed()

    # Execute with extended thinking + prompt caching
    model = get_model(agent_code)
    
    # Structure content for prompt caching: static content first (cacheable),
    # dynamic content last (changes per execution)
    if PROMPT_CACHING_ENABLED:
        messages = build_cached_messages(
            static_content=wiki_content + "\n\n" + universal_content + "\n\n" + scope_docs,
            dynamic_content=format_inbox(inbox_items),
            cycle=cycle
        )
    elif cycle:
        messages = cycle.build_continuation(inbox_items)
    else:
        messages = build_fresh_messages(
            wiki=wiki_content,
            universal=universal_content,
            inbox=inbox_items,
            scope=scope_docs,
            instance_id=get_instance_id(agent_code)
        )

    response = await anthropic_client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        thinking=get_thinking_config(model),  # API-version-dependent; see §3.1
        system=system_prompt,
        messages=messages
    )

    # Parse response — extract thinking + text + structured blocks
    thinking_blocks, artifacts, wiki_updates, log_entries = parse_response(
        response, agent_code
    )

    # Record consultation (which wiki entries were in the API call)
    consultation_record = build_consultation_record(
        agent_code, wiki_content, inbox_items
    )

    # Apply wiki updates (with diff logging)
    threshold_tripped = wiki_manager.apply_updates(agent_code, wiki_updates)
    # threshold_tripped is returned in the execution result (see below).
    # The main loop collects it from all agents in the tick and fires SYS
    # once after the gather — deduplicating multiple threshold trips into
    # a single audit run. No inline execute_sys (avoids re-entrancy).

    # Append log entries
    for entry in log_entries:
        wiki_manager.append_log(agent_code, entry)

    # Update cycle if active
    if cycle:
        cycle.append_turn(messages[-1], response)
        cycle_manager.check_context_usage(cycle)  # 40% warning

    # Log execution (including thinking blocks and model)
    execution_id = log_execution(agent_code, model, inbox_items, artifacts,
                  wiki_updates, thinking_blocks, consultation_record)

    # Update artifact index (for /thinking retrieval)
    for artifact in artifacts:
        artifact_index.add(artifact.id, execution_id, agent_code)

    # Place artifacts in outbox
    for artifact in artifacts:
        assign_metadata(artifact, agent_code, model)
        place_in_outbox(agent_code, artifact)

    # Mark inbox items as processed
    inbox.mark_processed(inbox_items)

    # CORTEX post-execution (if active)
    if CORTEX_ENABLED:
        cortex_script.post_execution(
            agent_code, log_entries, consultation_record, wiki_updates
        )

    return {"threshold_tripped": threshold_tripped}
```

### 5.2 Prompt Caching

Static content (system prompt, wiki, UNIVERSAL, scope) is identical across executions for the same agent. The API's cache_control marker allows caching this content, reducing input token cost on subsequent calls.

```python
def build_cached_messages(static_content, dynamic_content, cycle=None):
    """Structure messages with static content first (cached) + dynamic last."""
    if cycle:
        # Cycle continuation: static cached, cycle history, new turn
        messages = []
        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": static_content,
                 "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": "Cycle context follows."}
            ]
        })
        # Add prior cycle turns
        for turn in cycle.messages:
            messages.append(turn)
        # Add new inbox items as latest turn
        messages.append({
            "role": "user",
            "content": dynamic_content
        })
        return messages
    else:
        # Fresh execution: static cached + dynamic
        return [{
            "role": "user",
            "content": [
                {"type": "text", "text": static_content,
                 "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": dynamic_content}
            ]
        }]
```

Cache hits are highest for agents that execute frequently with stable wikis (SG, BR). First call per agent caches; subsequent calls within the cache TTL pay reduced input cost on the static portion.

### 5.3 System Prompt Composition

Each agent's `system_prompt.md` embeds:

```markdown
# Role: [Agent Name] ([Code])
Instance: [ROLE]-S[NNN]

## Preamble
[Standard preamble from framework]

## Your Role
[Full role definition from Framework §5.X]

## Document Type Codes
[From Framework §1 — the artifact type vocabulary]

## Your Interaction Catalog
[Subset of Framework §2 — rows where this agent is sender or recipient]
```

Additional context loaded per execution (cacheable via prompt caching):

```markdown
## Framework View (tiered per Framework §12)
- SG, TG: §1 + §2 + §9 + §10 + project addendum (~12k tokens)
- SYS: §1 + §2 + §5 + §10 + project addendum (~20k tokens)
- SA, TA, BR, BTA: Framework Summary from §12.1 + project addendum (~4k tokens)
- SE, TE: none (system prompt is self-contained)

## UNIVERSAL
[manifesto.md, cross_agent_rules.md, case_index.md]

## Scope Documents (budget-aware, classification priority)
[Project addendum classifies docs as spec or management]
- Full corpus (spec + management): SG, SYS
- Spec-tier only: SA, TG, TA, BR, BTA
- Task-scoped: SE, TE (only docs targeted by SCN/TCN, plus manifest)
When the scope corpus exceeds the agent's available context, spec docs 
load first. Omitted docs are listed in a manifest (name + version) so 
the agent knows what exists beyond its loaded set.

## Your Wiki
[All wiki pages for this agent]
```

**Context loading order:**
1. System prompt (role + type codes + interaction catalog)
2. Framework view (tiered per §12)
3. UNIVERSAL (filtered per D-ARCH-031)
4. Scope documents (budget-aware, classification priority)
5. Agent wiki (all pages)
6. Inbox items (unprocessed)
7. Referenced archived artifacts (resolved from inbox, budget-aware)
8. Cycle messages (if continuing a cycle)

**Referenced artifact resolution (SC-5, §7.4 archive → agent grounding):**

When inbox items carry `references` to archived artifacts, the orchestrator resolves those references and injects the bodies as read-only context. This ensures the receiving agent can read the artifact it's acting on (e.g., SG receiving AUTH that references PROP — SG needs the PROP body to write the exchange summary).

Resolution rules:
- Direct references only (no recursive resolution)
- Self-references and IDs already in the inbox are skipped
- Budget is computed per execution: `min(REF_TOTAL_MAX_CEILING, model_context_limit - used_tokens - RESPONSE_RESERVE)` where `used_tokens` includes all prior loading steps (1–6)
- Priority: inbox order × reference order (first reference of first item = highest priority)
- Per-artifact cap: `REF_ARTIFACT_MAX_CHARS` (default 32K chars, truncated with archive path)
- Total budget exceeded: remaining references listed as omitted with archive paths
- `REF_TOTAL_MAX_CEILING` (default 96K chars) and `RESPONSE_RESERVE` (default 16K tokens) are deployment constants
- An agent that cannot complete its task due to omitted references produces a NOTE identifying the dependency, rather than proceeding with incomplete information

**Scope document loading (SC-5 extension, budget-aware):**

Scope documents follow the same dynamic budget principle as reference resolution. The project addendum classifies each scope document as `spec` (core specifications agents implement against) or `management` (tracking, overview, strategy, history).

Loading by agent tier:
- Full corpus (spec + management): SG, SYS
- Spec-tier only: SA, TG, TA, BR, BTA
- Task-scoped: SE, TE — orchestrator parses inbox SCN/TCN for targeted documents and loads those in full, plus a document manifest (name + version for all docs)

If an agent cannot complete its task due to missing scope context, it produces a NOTE identifying the needed document rather than proceeding with incomplete information.

The system prompt is self-contained for artifact production (the agent knows its types and routes). Framework context is loaded per the tiered model in Framework §12.

The system prompt also includes the output format specification:

```markdown
## Output Format

### ARTIFACT
type: [document type code]
id: [leave blank — orchestrator assigns]
references: [list of referenced artifact IDs]
priority: [P0-P3, if applicable — SG PROP only]
ref_type: [type of referenced artifact, if this is a REJ]
---
[artifact content]

### WIKI_UPDATE
file: [wiki file to update]
action: append | replace_section | new_entry
section: [section name, if replace_section]
justification: [why this change — mandatory for replace_section]
---
[content]

### LOG_ENTRY
---
[log.md entry to append]
```

### 5.4 Response Parsing

```python
def parse_response(response, agent_code):
    thinking_blocks = []
    text_content = []

    for block in response.content:
        if block.type == "thinking":
            thinking_blocks.append(block.thinking)
        elif block.type == "text":
            text_content.append(block.text)

    full_text = "\n".join(text_content)
    artifacts = extract_blocks(full_text, "ARTIFACT")
    wiki_updates = extract_blocks(full_text, "WIKI_UPDATE")
    log_entries = extract_blocks(full_text, "LOG_ENTRY")

    # Assign metadata to artifacts
    for artifact in artifacts:
        if not artifact.id:
            artifact.id = sequence_manager.next_id(agent_code, artifact.type)
        artifact.sender = agent_code
        artifact.sender_instance = get_instance_id(agent_code)
        artifact.sender_model = get_model(agent_code)
        artifact.timestamp = datetime.utcnow().isoformat()

    return thinking_blocks, artifacts, wiki_updates, log_entries
```

### 5.5 Re-grounding Is Structural

Every API call starts fresh with the full system prompt + wiki + UNIVERSAL. The agent is re-grounded on EVERY execution. The framework's "every 3 cycles" and "40% context" re-grounding rules are satisfied by design for individual executions.

The **one place context accumulates** is within conversation cycles (§6). The 40% rule applies there — see §6.4.

### 5.6 SYS Execution

SYS runs on schedule (configurable) or on-demand via /sys:

```python
async def execute_sys(audit_request=None):
    all_artifacts = artifact_store.get_recent(since=last_sys_run)
    all_wikis = wiki_manager.read_all_agents()
    all_logs = wiki_manager.read_all_logs()
    execution_log = load_execution_log(since=last_sys_run)
    universal = wiki_manager.read_universal()
    role_events = load_role_events(since=last_sys_run)
    snapshot_manifest = load_latest_snapshot_manifest()  # SC-7: drift detection

    inbox_content = format_sys_inbox(
        artifacts=all_artifacts,
        wikis=all_wikis,
        logs=all_logs,
        execution_log=execution_log,
        framework=load_framework(),
        role_events=role_events,
        snapshot_manifest=snapshot_manifest,
        audit_request=audit_request
    )

    # Execute as normal agent
    response = await anthropic_client.messages.create(
        model=get_model("SYS"),
        max_tokens=MAX_TOKENS,
        thinking=get_thinking_config(get_model("SYS")),  # API-version-dependent
        system=load_system_prompt("SYS"),
        messages=[{"role": "user", "content": inbox_content}]
    )

    thinking_blocks, artifacts, wiki_updates, log_entries = parse_response(
        response, "SYS"
    )

    # SYS can write to UNIVERSAL — but validates governance changes first
    universal_updates = [u for u in wiki_updates if u.target == "universal"]
    own_updates = [u for u in wiki_updates if u.target == "own"]
    
    # Governance validation: SYS checks resolved GOV resolutions against
    # framework invariants before applying to UNIVERSAL (Framework §3,
    # UNIVERSAL Authority Boundary). If a resolution conflicts with an
    # invariant (removes agent responsibility, bypasses V-model gate,
    # merges roles, weakens scope protection), SYS produces a NEW GOV
    # back to Admin OP instead of applying. The resolution is NOT written.
    for update in universal_updates:
        wiki_manager.apply_universal_update(update)
    for update in own_updates:
        wiki_manager.apply_updates("SYS", [update])

    # GOV artifacts go to Admin OP backlog
    for artifact in artifacts:
        if artifact.type == "GOV":
            admin_backlog.add(artifact)
            await telegram_bot.notify(artifact, role="ADMIN_OP")

    log_execution("SYS", get_model("SYS"), [], artifacts,
                  wiki_updates, thinking_blocks, None)
```

**SYS provenance audit (SC-6).** SYS checks archive integrity: every artifact in `artifacts/archive/` must have EITHER a `routing_log.json` entry (normal pipeline) OR an `applied_via: import` marker in its frontmatter (bootstrap import per §14 Step 0, or INIT-OP-001). An artifact with neither is a provenance violation — GOV to Admin OP. During normal operation, `applied_via: import` is not a standard path. If exceptional circumstances require it (missed initialization artifact, system downtime during scope evolution), Admin OP may import via server CLI. Every such import triggers an automatic GOV to Admin OP for acknowledgment, and SYS flags it in the next audit. This is a recovery mechanism, not an operating mode.

**SYS scope drift detection (SC-7).** SYS reads the latest snapshot manifest from `snapshots/` and compares content hashes against live `scope/` files. Hash mismatch = scope drift since last approval. GOV to Admin OP with the specific files that differ. SYS also checks `role_events.jsonl` for `snapshot_failed` events — consecutive failures indicate degraded durability and trigger GOV to Admin OP.

**SYS scope vs governance classification.** When Admin OP requests evaluation via `/sys`, SYS classifies the request before producing GOV: governance (how agents operate within roles) → GOV path. Scope (what the project builds or specification changes) → SYS responds with redirect to OP → SG. Mixed → SYS splits the request. When writing resolved governance decisions to UNIVERSAL, SYS validates against framework invariants (§3 UNIVERSAL Authority Boundary) — a resolution that removes agent responsibilities, bypasses V-model gates, merges roles, or weakens scope protection is returned to Admin OP with the conflict identified, NOT applied.

---

## 6. Conversation Cycles

Bounded multi-turn interactions maintain a messages array across executions. The array provides conversational coherence. It is discarded when the cycle closes.

### 6.1 Cycle Definitions

| Cycle type | Code ID | Participants | Opens on | Closes on |
|-----------|---------|-------------|----------|-----------|
| SCN application | `scn_application` | SG ↔ SE | SCN-SG-NNN issued | VAL-SG-NNN issued |
| TCN application | `tcn_application` | TG ↔ TE | TCN-TG-NNN issued | VAL-TG-NNN with certificate=build |
| PROP exchange | `prop_exchange` | SG ↔ OP | PROP-SG-NNN issued | AUTH-OP-NNN issued |
| Triage | `triage` | TG (internal) | TFR-BTA-NNN received | TRI/ESC/TCN produced |
| Build scope Q&A | `build_scope_qa` | BR ↔ EXT | PRO-SCOPE arrives at BR | VR-BTA-NNN for this scope version, or next PRO-SCOPE |
| Build test Q&A | `build_test_qa` | BR ↔ EXT | PRO-TEST-BUILD arrives at BR | VR-BTA-NNN for this test version, or next PRO-TEST-BUILD |
| Build results | `build_results` | BR ↔ EXT | BRQ (results) received from EXT | VR-BTA-NNN relayed to EXT |
| Build remediation | `build_remediation` | BR ↔ EXT | TRI relayed to EXT | New results submitted by EXT |
| DE Q&A | `de_qa` | SG ↔ DE | DE_OUT-SG-NNN sent | DE_IN received for that question |
| GOV exchange | `gov_exchange` | SYS ↔ Admin OP | GOV-SYS-NNN or /sys | /resolve GOV-SYS-NNN [action] |

Code ID is used in `cycle.type`, filesystem paths (`cycles/active/{id}.json`), and log entries. Display names are for human-facing output (Telegram, `/cycles`).

### 6.2 Cycle Manager

```python
class CycleManager:
    def __init__(self):
        self.active_cycles_dir = CYCLES_DIR

    def get_active_cycle(self, agent_code, inbox_item) -> Cycle | None:
        """Find active cycle this inbox item belongs to, if any."""
        for cycle_file in os.listdir(self.active_cycles_dir):
            cycle = self._load(cycle_file)
            if cycle.involves(agent_code) and cycle.matches(inbox_item):
                return cycle
        return None

    def open_cycle(self, cycle_type, opening_artifact, participants):
        """Open a new cycle when a cycle-opening artifact is produced."""
        cycle = Cycle(
            type=cycle_type,
            id=f"{cycle_type}-{opening_artifact.id}",
            participants=participants,
            opening_artifact=opening_artifact.id,
            messages=[]
        )
        self._save(cycle)
        return cycle

    def close_cycle(self, cycle):
        """Close cycle — archive the messages array, delete active file."""
        archive_path = f"{ARTIFACTS_DIR}/cycles/{cycle.id}.json"
        save_json(archive_path, cycle.to_dict())
        os.remove(f"{self.active_cycles_dir}/{cycle.id}.json")

    def close_by_artifact(self, artifact_id: str):
        """Find and close the cycle that opened on this artifact."""
        for cycle_file in os.listdir(self.active_cycles_dir):
            cycle = self._load(cycle_file)
            if cycle.opening_artifact == artifact_id:
                self.close_cycle(cycle)
                return

    def get_active_by_artifact(self, artifact_id: str) -> "Cycle | None":
        """Find active cycle associated with this artifact (by opening or reference)."""
        for cycle_file in os.listdir(self.active_cycles_dir):
            cycle = self._load(cycle_file)
            if (cycle.opening_artifact == artifact_id or
                    artifact_id in getattr(cycle, 'references', [])):
                return cycle
        return None

    def get_cycle_by_participants(self, *agents) -> "Cycle | None":
        """Find active cycle involving all named agents."""
        agent_set = set(agents)
        for cycle_file in os.listdir(self.active_cycles_dir):
            cycle = self._load(cycle_file)
            if agent_set.issubset(set(cycle.participants)):
                return cycle
        return None

    def get_active_by_type(self, cycle_type: str) -> "Cycle | None":
        """Find active cycle of a given type."""
        for cycle_file in os.listdir(self.active_cycles_dir):
            cycle = self._load(cycle_file)
            if cycle.type == cycle_type:
                return cycle
        return None

    def list_active(self) -> list:
        """Return all active cycles."""
        cycles = []
        for f in os.listdir(self.active_cycles_dir):
            if f.endswith(".json"):
                cycles.append(self._load(f))
        return cycles

    def get_activity_summary(self, cycle_type: str) -> list:
        """Return summaries of recent cycles of a given type (active + archived)."""
        summaries = []
        # Active
        for cycle_file in os.listdir(self.active_cycles_dir):
            cycle = self._load(cycle_file)
            if cycle.type == cycle_type:
                summaries.append({
                    "id": cycle.id, "status": "active",
                    "turns": len(cycle.messages),
                    "opened": cycle.opened_at
                })
        # Recent archived (last 10)
        archive_dir = f"{ARTIFACTS_DIR}/cycles"
        if os.path.exists(archive_dir):
            archived = sorted(os.listdir(archive_dir), reverse=True)
            for f in archived[:20]:
                cycle = load_json(f"{archive_dir}/{f}")
                if cycle.get("type") == cycle_type:
                    summaries.append({
                        "id": cycle["id"], "status": "closed",
                        "turns": len(cycle.get("messages", [])),
                        "opened": cycle.get("opened_at")
                    })
                    if len(summaries) >= 10:
                        break
        return summaries

    def get_pending_for_role(self, role: str) -> list:
        """Return active cycles awaiting response from this role."""
        role_to_participants = {"DE": ["SG", "DE"], "EXT": ["BR", "EXT"]}
        participants = role_to_participants.get(role, [])
        pending = []
        for cycle_file in os.listdir(self.active_cycles_dir):
            cycle = self._load(cycle_file)
            if set(participants).issubset(set(cycle.participants)):
                # Check if last turn was from the other party (awaiting this role)
                if cycle.messages and cycle.messages[-1].get("sender") != role:
                    pending.append(cycle)
        return pending

    def get_history_for_role(self, role: str) -> list:
        """Return closed cycles involving this role."""
        role_to_participants = {"DE": ["SG", "DE"], "EXT": ["BR", "EXT"]}
        participants = role_to_participants.get(role, [])
        history = []
        archive_dir = f"{ARTIFACTS_DIR}/cycles"
        if os.path.exists(archive_dir):
            for f in sorted(os.listdir(archive_dir), reverse=True)[:20]:
                cycle = load_json(f"{archive_dir}/{f}")
                if set(participants).issubset(set(cycle.get("participants", []))):
                    history.append(cycle)
        return history

    def check_cycle_events(self, artifact):
        """Check if an artifact opens or closes a cycle."""
        # TCN cycle: only closes on build certificate
        if (artifact.type == "VAL" and artifact.sender == "TG" 
                and hasattr(artifact, 'certificate')):
            if artifact.certificate == "build":
                cycle = self.get_cycle_by_participants("TG", "TE")
                if cycle:
                    self.close_cycle(cycle)
            # full certificate doesn't close — TE still generates build version
            return

        # Build cycles: close on VR for this version, or superseded by new PRO-SCOPE/PRO-TEST-BUILD
        if artifact.type == "PRO-SCOPE" and artifact.recipient == "BR":
            old_cycle = self.get_active_by_type("build_scope_qa")
            if old_cycle:
                self.close_cycle(old_cycle)  # Superseded
            self.open_cycle("build_scope_qa", artifact, ["BR", "EXT"])
            return

        # General cycle close/open checks for other artifact types
        # ... (SCN opens SG↔SE, AUTH closes PROP, etc.)

    def check_context_usage(self, cycle):
        """Monitor cycle context growth — compress or warn if approaching limit."""
        total_tokens = estimate_tokens(cycle.messages)
        context_limit = get_model_context_limit(cycle.model)
        usage = total_tokens / context_limit

        if usage > CYCLE_CONTEXT_WARNING:
            # Option 1: Summarize older turns
            cycle.compress_early_turns()
            wiki_manager.append_log(
                cycle.primary_agent,
                f"CYCLE_COMPRESSION | {cycle.id} | Context at {usage:.0%}, "
                f"compressed early turns (U-RC-08 warning: verify post-compression)"
            )
            # Option 2 (alternative): Close cycle and open new one
            # with summary handoff
```

### 6.3 Cycle-Aware Execution

When an execution is part of a cycle, the messages array includes prior turns:

```python
class Cycle:
    def build_continuation(self, new_inbox_items):
        """Build messages array including full cycle history + new items."""
        messages = []
        
        # Prior turns (preserving conversational coherence)
        for turn in self.messages:
            messages.append(turn)
        
        # New turn
        messages.append({
            "role": "user",
            "content": format_cycle_turn(new_inbox_items)
        })
        
        return messages

    def append_turn(self, user_message, assistant_response):
        """Record this turn in the cycle."""
        self.messages.append(user_message)
        self.messages.append({
            "role": "assistant",
            "content": extract_text_content(assistant_response)
        })
        self._save()
```

Note: thinking blocks from prior turns are automatically excluded from context by the API — they don't inflate cycle context.

### 6.4 Exchange Turns Are Cycle-Internal

Exchange turns within a cycle (OP's questions during PROP, OP's follow-ups during GOV) are NOT standalone artifacts. They are turns in the cycle's messages array. Only cycle-opening and cycle-closing events produce formal artifacts (PROP, AUTH, SUM, GOV, SCN, VAL, etc.) that route through the routing table and archive independently.

The cycle archive (saved when the cycle closes) contains the complete messages array — the full dialogue is preserved for SYS audit. SUM-SG-NNN provides the compiled summary for PROP cycles.

To trigger agent execution from an exchange turn, the orchestrator adds the turn to the cycle's messages array and flags the agent for execution. No artifact is created or routed.

### 6.5 Cycle Context Monitoring

Within a long cycle (e.g., BR↔EXT scope Q&A with many turns), the messages array grows. The orchestrator monitors context usage via `CycleManager.check_context_usage(cycle)` (defined in §6.2). Called after each execution within a cycle at the 40% context threshold.

---

## 7. Artifact Store

### 7.1 Artifact Format on Disk

```markdown
---
id: FND-SA-003
type: FND
sender: SA
sender_instance: SA-S002
sender_model: claude-sonnet-4-6
timestamp: 2026-05-20T14:30:00Z
references:
  - PRO-SCOPE:v4.1
priority: P2
ref_type: null
status: unprocessed
---

# FND-SA-003: Cross-reference gap in §3.9

[artifact content]
```

**Thinking sidecar (SC-4).** When an agent execution produces artifacts with extended thinking, the thinking blocks are persisted as a sidecar file alongside the archived artifact:

```
artifacts/archive/FND-SA-003.md              ← artifact body (immutable)
artifacts/archive/FND-SA-003.thinking.md     ← thinking blocks (immutable)
```

Written once at archive time. Never loaded into any agent's per-call grounding (zero context cost). Read on-demand by OP/Admin OP via `vega_thinking(id)`. If no sidecar exists (agent had no thinking, or pre-sidecar artifact), `vega_thinking` falls back to `execution_log.json` via `artifact_index.json`.

### 7.2 AUTH and SUM — Split Artifacts

The OP↔SG exchange produces two immutable artifacts:

**AUTH-OP-NNN** — OP's direction. Created at issuance. Never modified.

```markdown
---
id: AUTH-OP-003
type: AUTH
sender: OP
recipient: SG
timestamp: 2026-05-20T15:45:00Z
references:
  - PROP-SG-003
disposition: approve | reject | modify
---
[OP's direction and any modifications]
```

**SUM-SG-NNN** — SG's exchange summary. Created by SG in execution after receiving AUTH. Never modified. References AUTH.

```markdown
---
id: SUM-SG-003
type: SUM
sender: SG
sender_instance: SG-S001
sender_model: claude-opus-4-7
timestamp: 2026-05-20T15:50:00Z
references:
  - AUTH-OP-003
  - PROP-SG-003
---
## Exchange Summary
- OP challenged: [what OP questioned]
- SG refined: [what changed]
- Key reasoning: [why this disposition]
- Modifications from original recommendation: [if any]
```

Both are archived immutably. Together they form the complete auditable record.

### 7.3 Inbox Management

```python
class Inbox:
    def has_unprocessed(self) -> bool:
        return any(f for f in self._list() if self._status(f) == "unprocessed")

    def get_unprocessed(self) -> List[Artifact]:
        """Return unprocessed artifacts sorted by priority then timestamp."""
        artifacts = [self._load(f) for f in self._list()
                     if self._status(f) == "unprocessed"]
        return sorted(artifacts, key=lambda a: (
            {"P0": 0, "P1": 1, "P2": 2, "P3": 3}.get(a.priority, 2),
            a.timestamp
        ))

    def mark_processed(self, artifacts):
        for a in artifacts:
            self._update_status(a.filename, "processed")
```

### 7.4 Archive

```python
def archive(artifact: Artifact, thinking_blocks: list = None):
    dest = f"{ARTIFACTS_DIR}/{artifact.id}.md"
    atomic_write(dest, artifact.to_markdown())
    # Archive is IMMUTABLE — no modification after write
    # SC-4: Persist thinking sidecar alongside artifact
    if thinking_blocks:
        thinking_dest = f"{ARTIFACTS_DIR}/{artifact.id}.thinking.md"
        atomic_write(thinking_dest, "\n\n".join(thinking_blocks))
```

**Bootstrap import provenance (SC-6).** Artifacts imported from pre-orchestrator work (§14 Step 0) and INIT-OP-001 carry `applied_via: import` in their frontmatter. During normal operation, every archived artifact must have a routing_log entry. SYS provenance audit: an artifact with NEITHER a routing_log entry NOR an `applied_via: import` marker is a provenance violation — GOV to Admin OP. Emergency imports during operation are possible via Admin OP server CLI but always trigger a GOV for acknowledgment — this is a recovery mechanism, not an operating mode.

### 7.5 Validated-State Snapshots (SC-7)

On each scope approval (AUTH with approve disposition), the orchestrator exports an immutable snapshot of the validated state to durable storage outside its working directory.

**Trigger:** Approve handler (`vega_approve` / `/approve`), after AUTH is issued and the cycle closes. One snapshot per approval. Reject and modify do not snapshot. Both MCP and Telegram approve paths trigger the snapshot.

**Contents — scope + triggering artifacts + verification hashes:**

```
{SNAPSHOT_LOCAL_DIR}/
└── SNAP-2026-06-04T103000Z-AUTH-OP-008/
    ├── manifest.json
    ├── scope/
    │   └── [all current scope docs at validated versions]
    └── artifacts/
        ├── [approved SCN]
        ├── [AUTH]
        └── [SUM]
```

No decision_log — decisions are audit history in agent wikis (SG `decisions.md`). They accumulate permanently and are never reverted.

**Manifest (verification anchor):**

```json
{
    "snapshot_id": "SNAP-{timestamp}-{auth_id}",
    "timestamp": "ISO-8601",
    "trigger": "{auth_id}",
    "approved_artifact": "{scn_id}",
    "scope_versions": {"doc_name": "vN.N", ...},
    "content_hashes": {"scope/doc_name.md": "sha256:...", ...}
}
```

Content hashes enable verification: compare live scope file hash against snapshot hash to confirm no drift — a local check, no routed request.

**Snapshot creation (`_write_snapshot`):**

```python
async def _write_snapshot(auth_artifact):
    """Write validated-state snapshot. Called from both MCP vega_approve 
    and Telegram /approve after AUTH issues."""
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    snap_id = f"SNAP-{timestamp}-{auth_artifact.id}"
    snap_dir = f"{SNAPSHOT_LOCAL_DIR}/{snap_id}"
    
    try:
        os.makedirs(snap_dir)
        # Scope docs
        shutil.copytree(SCOPE_DIR, f"{snap_dir}/scope")
        # Triggering artifacts from archive
        os.makedirs(f"{snap_dir}/artifacts")
        for ref_id in [auth_artifact.id] + (auth_artifact.references or []):
            src = f"{ARTIFACTS_DIR}/{ref_id}.md"
            if os.path.exists(src):
                shutil.copy(src, f"{snap_dir}/artifacts/")
        # Manifest with content hashes
        manifest = {
            "snapshot_id": snap_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "trigger": auth_artifact.id,
            "approved_artifact": (auth_artifact.references or [None])[0],
            "scope_versions": _extract_scope_versions(f"{snap_dir}/scope"),
            "content_hashes": _compute_content_hashes(snap_dir)
        }
        atomic_write(f"{snap_dir}/manifest.json", json.dumps(manifest))
        # Optional git push
        if SNAPSHOT_GIT_REMOTE:
            _git_push_snapshot(snap_dir, snap_id)
    except Exception as e:
        # Failure must not block routing — warn OP, log error
        log_error("snapshot_write", e)
        role_manager._log_event("snapshot_failed", 
            trigger=auth_artifact.id, error=str(e))
        await telegram_bot.send(
            role_manager.get_telegram_id("OP"),
            f"⚠️ Snapshot failed for {auth_artifact.id}: {e}. "
            f"AUTH issued normally. Snapshot durability degraded.")
```

**Target (deployment-configurable, see §3.1):**

Snapshots are written to `SNAPSHOT_LOCAL_DIR` (outside orchestrator tree). If `SNAPSHOT_GIT_REMOTE` is configured, the snapshot is also committed and pushed. Both targets attempted if configured. Neither blocks the approve flow.

**Failure handling (consistent with §10.2 notification resilience):** Snapshot export must not fail silently. If a write fails: warn OP, log error. The AUTH still issues — routing is never blocked by snapshot failure. SYS flags consecutive snapshot failures in its audit via `snapshot_failed` events in `role_events.jsonl`.

**Restoration (dual 2FA):**

Admin OP can restore scope to any snapshot via `vega_restore(snapshot_id)` (MCP), `/restore SNAP-id` (Telegram), or server CLI `vega restore --snapshot <id>`.

```
Restoration requires dual authorization:
1. Admin OP initiates → OTP to Admin OP's Telegram
2. Admin OP confirms → orchestrator sends consent request to OP
3. OP confirms → OTP to OP's Telegram
4. OP confirms → orchestrator executes restoration

Both must confirm. Either can block. Expiry: 15 minutes.
```

The dual requirement reflects that restoration reverses the effect of OP's prior scope approvals — Admin OP has the technical authority, OP has the scope authority. Neither acts alone.

**Dual 2FA flow (on RoleManager — 2FA only, not file operations):**

```python
def initiate_restore(self, admin_chat_id, snapshot_id):
    """Phase 1: Admin OP initiates."""
    otp = self._generate_otp()
    self._pending_restores[admin_chat_id] = {
        "snapshot_id": snapshot_id,
        "otp": otp,
        "expires": time.time() + OTP_EXPIRY,
        "phase": "admin_confirm"
    }
    return otp

def confirm_restore_admin(self, admin_chat_id, otp):
    """Phase 1 confirmed. Prepare Phase 2 (OP consent)."""
    pending = self._pending_restores.get(admin_chat_id)
    if not pending or pending["otp"] != otp or time.time() > pending["expires"]:
        return None
    snapshot_id = pending["snapshot_id"]
    op_telegram_id = self.get_telegram_id("OP")
    op_otp = self._generate_otp()
    del self._pending_restores[admin_chat_id]
    self._pending_restores[op_telegram_id] = {
        "snapshot_id": snapshot_id,
        "otp": op_otp,
        "expires": time.time() + OTP_EXPIRY,
        "phase": "op_consent"
    }
    self._log_event("restore_initiated", snapshot_id=snapshot_id)
    return op_telegram_id, op_otp

def confirm_restore_op(self, op_chat_id, otp):
    """Phase 2 confirmed. Return snapshot_id for execution."""
    pending = self._pending_restores.get(op_chat_id)
    if not pending or pending["otp"] != otp or time.time() > pending["expires"]:
        return None
    snapshot_id = pending["snapshot_id"]
    del self._pending_restores[op_chat_id]
    return snapshot_id
```

Note: Three separate pending-action dicts, each with one code path: `_pending_role_actions` (assign/modify/revoke, keyed by chat_id), `_pending_activations` (invite activation, keyed by invite_code), `_pending_restores` (scope restoration, keyed by chat_id).

**Restoration procedure (standalone function — not on RoleManager):**

```python
async def restore_scope(snapshot_id, executor, telegram_bot, role_manager):
    """§7.5 restoration. Called after both 2FA confirmations."""
    snap_dir = f"{SNAPSHOT_LOCAL_DIR}/{snapshot_id}"

    # 1. Pause all agents
    executor.pause()

    # 2. Back up current scope
    backup = f"{SNAPSHOT_LOCAL_DIR}/pre-restore-{int(time.time())}"
    os.makedirs(backup)
    shutil.copytree(SCOPE_DIR, f"{backup}/scope")

    # 3. Replace scope/
    shutil.rmtree(SCOPE_DIR)
    shutil.copytree(f"{snap_dir}/scope", SCOPE_DIR)

    # 4. Log
    role_manager._log_event("restore_executed", snapshot_id=snapshot_id)

    # 5. SYS audit BEFORE resuming — identify wiki-vs-scope inconsistencies
    await executor.execute_sys(audit_request=
        f"Post-restoration audit: scope restored to {snapshot_id}. "
        f"Identify wiki entries and decisions that reference "
        f"post-snapshot scope changes now inconsistent with "
        f"the restored scope. Produce GOV for each inconsistency.")

    # 6. Notify — agents stay paused until Admin OP reviews SYS findings
    op_id = role_manager.get_telegram_id("OP")
    admin_id = role_manager.get_telegram_id("ADMIN_OP")
    await telegram_bot.send(admin_id,
        f"✅ Scope restored to {snapshot_id}. SYS audit complete — "
        f"review GOV findings before resuming agents. Use /resume when ready.")
    await telegram_bot.send(op_id,
        f"Scope restored to {snapshot_id}. Use /verify to confirm. "
        f"Re-evaluate post-snapshot changes via normal PROP→AUTH.")

    # Agents remain paused. Admin OP reviews SYS GOVs, then /resume.
```

Agents do NOT auto-resume. Admin OP reviews SYS findings, resolves inconsistencies, then explicitly resumes. This prevents agents from working with stale knowledge.

**Post-restore:** The archive retains all post-snapshot artifacts (SCNs, AUTHs, SUMs). Their effects on the live scope are gone but the artifacts and reasoning are preserved. Agent wikis are untouched — SYS flags any wiki entries now inconsistent with the restored scope. OP re-evaluates which post-snapshot changes to re-apply through the normal PROP→AUTH cycle.

**New tools:**

| Tool | Role | Description |
|------|------|-------------|
| `vega_verify()` | OP, Admin OP | Compare live scope hashes against last snapshot manifest. Returns per-doc match/mismatch + snapshot ID. |
| `vega_snapshots()` | OP, Admin OP | List available snapshots (id, timestamp, trigger, scope versions). |
| `vega_restore(snapshot_id)` | Admin OP only | Initiate scope restoration. Requires dual 2FA (Admin OP + OP consent). |

**`role_events.jsonl` schema extension:** Add `snapshot_failed`, `restore_initiated`, `restore_executed` to the action enum.

**CORTEX re-indexing:** Scope restoration triggers re-indexing (scope/ content changed). See CORTEX §18.3.

---

## 8. Wiki Manager

### 8.1 Read Operations

```python
class WikiManager:
    def read_all(self, agent_code: str) -> str:
        """Read all wiki files for an agent. Returns content + list of entry IDs."""
        wiki_dir = f"{AGENTS_DIR}/{agent_code}/wiki"
        content = []
        entries_included = []

        # Read in specified order
        read_order = ["SCHEMA.md", "index.md", "log.md",
                      "process_rules.md", "reasoning_corrections.md"]
        for filename in read_order:
            filepath = f"{wiki_dir}/{filename}"
            if os.path.exists(filepath):
                file_content = read_file(filepath)
                content.append(f"## {filename}\n{file_content}")
                entries_included.extend(extract_entry_ids(file_content))

        # Read remaining role-specific files
        for f in sorted(os.listdir(wiki_dir)):
            if f.endswith(".md") and f not in read_order:
                file_content = read_file(f"{wiki_dir}/{f}")
                content.append(f"## {f}\n{file_content}")
                entries_included.extend(extract_entry_ids(file_content))

        return "\n\n".join(content), entries_included

    def read_universal(self, agent_code: str = None) -> str:
        """Read all UNIVERSAL wiki files. Filter exclusions if agent_code provided."""
        content = []
        for f in ["manifesto.md", "cross_agent_rules.md", "case_index.md"]:
            filepath = f"{UNIVERSAL_DIR}/{f}"
            if os.path.exists(filepath):
                file_content = read_file(filepath)
                if agent_code and f == "cross_agent_rules.md":
                    file_content = self._filter_exclusions(file_content, agent_code)
                content.append(f"## {f}\n{file_content}")
        return "\n\n".join(content)

    def _filter_exclusions(self, content: str, agent_code: str) -> str:
        """Remove rules where this agent is in the excluded_for list.
        
        UNIVERSAL rules can carry an excluded_for field per D-ARCH-031:
        
            ## U-RULE-XX: [Title]
            excluded_for: [BR, TE]
            gov_reference: GOV-SYS-005
            ---
            [Rule content]
        
        Format is strict — fields must appear in this exact order:
        excluded_for, then gov_reference, then --- separator, then content.
        SYS is the only UNIVERSAL writer and must produce this format.
        The orchestrator's exclusion filter depends on this structure.
        
        Excluded sections are replaced with a note:
            [Rule excluded for BR — pending GOV-SYS-005 resolution]
        
        Structural guarantee: the agent never sees the contradicting rule content.
        """
        # Parse markdown sections, check excluded_for field
        # If agent_code is in excluded_for list:
        #   replace section content with exclusion notice
        # Return filtered content
        ...
```

### 8.2 Write Operations with Diff Logging

```python
    def apply_updates(self, agent_code: str, updates: List[WikiUpdate]):
        """Apply wiki updates and return whether SYS threshold was tripped."""
        threshold_tripped = False
        for update in updates:
            filepath = f"{AGENTS_DIR}/{agent_code}/wiki/{update.file}"

            if update.action == "replace_section":
                # Capture before state for diff logging
                before = read_section(filepath, update.section)
                replace_section(filepath, update.section, update.content)
                after = update.content

                # Log the diff with justification
                self.append_log(agent_code,
                    f"WIKI_REPLACE | {update.file} § {update.section} | "
                    f"Justification: {update.justification or 'NONE PROVIDED'} | "
                    f"Before: {before[:200]}... | After: {after[:200]}..."
                )
                if not update.justification:
                    self.append_log(agent_code,
                        f"WARNING | replace_section without justification — "
                        f"flagged for SYS review"
                    )

                # Check replace threshold
                if self._check_replace_threshold(agent_code):
                    threshold_tripped = True

            elif update.action == "append":
                append_to_file(filepath, update.content)
                self.append_log(agent_code,
                    f"WIKI_APPEND | {update.file} | {update.content[:200]}..."
                )

            elif update.action == "new_entry":
                append_to_file(filepath, f"\n\n{update.content}")
                self.append_log(agent_code,
                    f"WIKI_NEW_ENTRY | {update.file} | {update.content[:100]}..."
                )

        return threshold_tripped

    def _check_replace_threshold(self, agent_code: str):
        """Track replace_section count per agent between SYS runs.
        Trigger SYS review if threshold exceeded."""
        counters = load_json(f"{STATE_DIR}/wiki_replace_counters.json")
        count = counters.get(agent_code, 0) + 1
        counters[agent_code] = count
        atomic_save_json(f"{STATE_DIR}/wiki_replace_counters.json", counters)

        threshold = WIKI_REPLACE_THRESHOLDS.get(
            agent_code, WIKI_REPLACE_THRESHOLD_DEFAULT
        )
        if count >= threshold:
            self.append_log(agent_code,
                f"THRESHOLD | {count} replace_sections since last SYS audit — "
                f"flagged for immediate SYS review"
            )
            return True  # Signal to main loop: trigger SYS
        return False

    def reset_replace_counters(self):
        """Called after each SYS run."""
        atomic_save_json(f"{STATE_DIR}/wiki_replace_counters.json", {})

    def append_log(self, agent_code: str, entry: str):
        filepath = f"{AGENTS_DIR}/{agent_code}/wiki/log.md"
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
        instance = get_instance_id(agent_code)
        model = get_model(agent_code)
        formatted = f"## [{timestamp}] {instance} ({model}) | {entry}\n"
        atomic_append(filepath, formatted)

    def apply_universal_update(self, update: WikiUpdate):
        """SYS writes to UNIVERSAL."""
        filepath = f"{UNIVERSAL_DIR}/{update.file}"
        if update.action == "replace_section":
            before = read_section(filepath, update.section)
            replace_section(filepath, update.section, update.content)
            # Log diff — UNIVERSAL changes affect all agents
            self.append_log("SYS",
                f"UNIVERSAL_REPLACE | {update.file} § {update.section} | "
                f"Before: {before[:200]}... | After: {update.content[:200]}..."
            )
        elif update.action in ("append", "new_entry"):
            atomic_append(filepath, f"\n\n{update.content}")
            self.append_log("SYS",
                f"UNIVERSAL_APPEND | {update.file} | {update.content[:200]}..."
            )

    def read_log(self, agent_code: str, n: int = 10) -> str:
        """Return last N log entries for one agent, parsed by ## headers."""
        filepath = f"{AGENTS_DIR}/{agent_code}/wiki/log.md"
        content = read_file(filepath)
        entries = content.split("\n## ")
        last_n = entries[-n:] if len(entries) > n else entries
        return "\n## ".join(last_n)
```

---

## 9. Sequence Manager

### 9.1 Document ID Generation

```python
class SequenceManager:
    def __init__(self):
        self.state_file = f"{STATE_DIR}/sequences.json"
        self._lock = asyncio.Lock()

    async def next_id(self, agent_code: str, doc_type: str) -> str:
        async with self._lock:
            sequences = self._load()
            key = f"{agent_code}:{doc_type}"
            sequences[key] = sequences.get(key, 0) + 1
            seq = sequences[key]
            self._atomic_save(sequences)
            return f"{doc_type}-{agent_code}-{seq:03d}"

    async def next_decision(self, agent_code: str) -> str:
        prefix = {"SG": "D", "TG": "TD", "SYS": "GD"}[agent_code]
        async with self._lock:
            sequences = self._load()
            key = f"{agent_code}:DECISION"
            sequences[key] = sequences.get(key, 0) + 1
            seq = sequences[key]
            self._atomic_save(sequences)
            return f"{prefix}-{seq:03d}"
```

### 9.2 Instance ID Management

```python
class InstanceManager:
    def __init__(self):
        self.state_file = f"{STATE_DIR}/instance_ids.json"

    def get_or_create(self, agent_code: str) -> str:
        instances = self._load()
        if agent_code not in instances:
            instances[agent_code] = 1
            self._atomic_save(instances)
        return f"{agent_code}-S{instances[agent_code]:03d}"

    def rotate(self, agent_code: str):
        instances = self._load()
        instances[agent_code] = instances.get(agent_code, 0) + 1
        self._atomic_save(instances)
        return self.get_or_create(agent_code)
```

---

## 10. OP Backlog and Telegram Bot

### 10.1 Backlog Queues

Two separate queues, one per decision role:

- `op_backlog/` — PROP items for OP (scope decisions)
- `admin_backlog/` — GOV items + role access requests for Admin OP (governance)

Each queue has the same lifecycle: pending → in_progress → resolved. No filtering needed — each role's queue is self-contained.

```python
class Backlog:
    def __init__(self, base_dir):
        self.base_dir = base_dir  # op_backlog/ or admin_backlog/

    def add(self, artifact: Artifact):
        dest = f"{self.base_dir}/pending/{artifact.id}.md"
        atomic_write(dest, artifact.to_markdown())

    def list_pending(self) -> list:
        """Return all pending items, sorted by priority (if present)."""
        items = []
        for f in os.listdir(f"{self.base_dir}/pending"):
            if f.endswith(".md"):
                items.append(load_artifact(f"{self.base_dir}/pending/{f}"))
        return sorted(items, key=lambda a: getattr(a, 'priority', 'P3'))

    def get_in_progress(self) -> Artifact | None:
        """Return the currently active exchange item, or None."""
        files = os.listdir(f"{self.base_dir}/in_progress")
        if files:
            return load_artifact(f"{self.base_dir}/in_progress/{files[0]}")
        return None

    def start_exchange(self, artifact_id: str):
        """Move item from pending to in_progress (OP/Admin OP engaging)."""
        pending = f"{self.base_dir}/pending/{artifact_id}.md"
        if os.path.exists(pending):
            move(pending, f"{self.base_dir}/in_progress/{artifact_id}.md")

    def resolve(self, artifact_id: str, resolution=None):
        """Resolve a backlog item. Handles items from either pending 
        (quick decision, no discussion) or in_progress (after exchange).
        For PROP: resolution is AUTH artifact (already routed by handler). 
        For GOV: resolution is text (stored with item, SYS reads in 
        cycle archive at next execution)."""
        in_progress = f"{self.base_dir}/in_progress/{artifact_id}.md"
        pending = f"{self.base_dir}/pending/{artifact_id}.md"
        source = in_progress if os.path.exists(in_progress) else pending
        move(source, f"{self.base_dir}/resolved/{artifact_id}.md")
        if isinstance(resolution, str) and resolution:
            # GOV resolution — append text to resolved file
            atomic_append(f"{self.base_dir}/resolved/{artifact_id}.md",
                f"\n---\nResolution: {resolution}\n")

op_backlog = Backlog(OP_BACKLOG_DIR)
admin_backlog = Backlog(ADMIN_BACKLOG_DIR)
```

### 10.2 Telegram Bot

**Notification resilience:** Telegram notification failures must not break artifact routing or archival. The routing pipeline completes archive, deliver, and log before attempting notification. If notification fails: retry once as plain text (no markdown formatting). If still fails, log the notification failure and continue. The artifact is accessible via /backlog regardless. Messages exceeding 4096 characters are truncated with a `[truncated — use /history <id> for full content]` suffix.

**Readiness barrier:** After starting the Telegram bot polling task, the orchestrator must await bot readiness before sending the startup greeting or any other message. Readiness means the bot's polling connection is established and send() will reach Telegram, not shim to stdout.

```python
class TelegramBot:
    async def notify(self, artifact: Artifact, role: str = "OP"):
        chat_id = role_manager.get_telegram_id(role)
        if not chat_id:
            return

        priority_emoji = {"P0": "🔴", "P1": "🟠", "P2": "🟡", "P3": "🟢"}
        emoji = priority_emoji.get(artifact.priority, "🟡")

        summary = self._extract_summary(artifact)

        # Include thinking highlights if available
        thinking = self._get_thinking_summary(artifact)
        thinking_section = f"\n💭 *SG reasoning:* {thinking}\n" if thinking else ""

        # Adapt actions to role
        if role == "OP":
            actions = (
                f"Reply to discuss, or:\n"
                f"/approve {artifact.id}\n"
                f"/reject {artifact.id} [reason]\n"
                f"/modify {artifact.id} [instructions]"
            )
        elif role == "ADMIN_OP":
            actions = (
                f"Review and:\n"
                f"/resolve {artifact.id} [action]"
            )
        else:
            actions = ""

        msg = (
            f"{emoji} **{artifact.id}** ({artifact.priority})\n"
            f"From: {artifact.sender} ({artifact.sender_model})\n"
            f"{summary}\n"
            f"{thinking_section}\n"
            f"{actions}"
        )
        await self.send(chat_id, msg)

    async def handle_message(self, message):
        """Dispatch by role based on Telegram chat ID."""
        chat_id = message.chat.id
        role = role_manager.get_role_by_telegram(chat_id)
        
        if role is None:
            await self.send(chat_id,
                "Access not configured. Use MCP to request access "
                "via vega_request_access().")
            return
        
        text = message.text
        
        # Read commands — shared across roles, scoped by visibility
        if text.startswith(("/status", "/agent", "/history", "/wiki",
                           "/log", "/thinking", "/cycles", "/agents",
                           "/routing", "/decisions", "/framework",
                           "/scope", "/verify", "/snapshots")):
            await self.handle_read_command(chat_id, text, role)
            return
        
        # Role-specific handlers
        handlers = {
            "OP": self.handle_op_message,
            "ADMIN_OP": self.handle_admin_op_message,
            "DE": self.handle_de_message,
            "EXT": self.handle_ext_message,
        }
        await handlers[role](chat_id, text)

    async def handle_op_message(self, chat_id, text):
        """OP: scope decisions via SG."""
        if text.startswith("/approve"):
            artifact_id = self._extract_id(text)
            auth = create_auth(artifact_id, "approve")
            await router.route(auth)
            op_backlog.resolve(artifact_id, auth)
            cycle_manager.close_by_artifact(artifact_id)
            # SC-7: snapshot on approve (both MCP and Telegram)
            if SNAPSHOT_ENABLED:
                await _write_snapshot(auth)
            await self.send(chat_id, f"✅ AUTH issued for {artifact_id}.")

        elif text.startswith("/reject"):
            artifact_id, reason = self._extract_id_and_text(text)
            auth = create_auth(artifact_id, "reject", reason=reason)
            await router.route(auth)
            op_backlog.resolve(artifact_id, auth)
            cycle_manager.close_by_artifact(artifact_id)
            await self.send(chat_id, f"❌ AUTH issued for {artifact_id}")

        elif text.startswith("/modify"):
            artifact_id, instructions = self._extract_id_and_text(text)
            auth = create_auth(artifact_id, "modify", modifications=instructions)
            await router.route(auth)
            op_backlog.resolve(artifact_id, auth)
            cycle_manager.close_by_artifact(artifact_id)
            await self.send(chat_id, f"✏️ AUTH issued with modifications for {artifact_id}")

        elif text.startswith("/request"):
            content = text[9:].strip()
            req = create_artifact(type="REQ", sender="OP", content=content)
            req.id = await sequence_manager.next_id("OP", "REQ")
            await router.route(req)
            await self.send(chat_id, f"📨 {req.id} submitted to SG.")

        elif text.startswith("/backlog"):
            items = op_backlog.list_pending()
            await self.send(chat_id, format_backlog(items))

        elif text.startswith("/de_activity"):
            summaries = cycle_manager.get_activity_summary("de_qa")
            await self.send(chat_id, format_activity(summaries))

        elif text.startswith("/ext_activity"):
            summaries = cycle_manager.get_activity_summary("build_scope_qa")
            await self.send(chat_id, format_activity(summaries))

        elif text == "APPROVE" and role_manager.has_pending_restore(chat_id):
            # OP consent for restore (§7.5 dual 2FA Phase 2)
            snapshot_id = role_manager.confirm_restore_op(chat_id, otp=None)
            if snapshot_id:
                await restore_scope(snapshot_id, executor, self, role_manager)
            else:
                await self.send(chat_id, "Confirmation failed or expired.")

        elif text == "REJECT" and role_manager.has_pending_restore(chat_id):
            del role_manager._pending_restores[chat_id]
            admin_id = role_manager.get_telegram_id("ADMIN_OP")
            await self.send(admin_id, "❌ OP rejected the restoration request.")
            await self.send(chat_id, "Restoration rejected.")

        else:
            # Freeform text during PROP exchange (cycle-internal, §6.4)
            # Disabled by default — use MCP vega_exchange for substantive exchanges.
            # Admin OP can enable via TELEGRAM_EXCHANGE_ENABLED when MCP unavailable.
            if not TELEGRAM_EXCHANGE_ENABLED:
                await self.send(chat_id,
                    "Exchange available via MCP only. Use vega_exchange "
                    "in your Claude session. Quick commands (/approve, "
                    "/reject, /modify, /request) always work here.")
                return
            current = op_backlog.get_in_progress()
            if not current:
                # Check pending — first engagement starts the exchange
                pending_items = op_backlog.list_pending()
                if pending_items:
                    current = pending_items[0]
                    op_backlog.start_exchange(current.id)
            if current:
                cycle = cycle_manager.get_active_cycle("SG", current)
                if cycle:
                    cycle.append_turn(text, "OP")
                    flag_for_execution("SG", cycle_id=cycle.id)
                    await self.send(chat_id, "↩️ Forwarded to SG.")
            else:
                await self.send(chat_id, "No active exchange. Use /pending.")

    async def handle_admin_op_message(self, chat_id, text):
        """Admin OP: governance + role management + agent control."""
        if text.startswith("/resolve"):
            gov_id, action = self._extract_id_and_text(text)
            admin_backlog.resolve(gov_id, action)
            cycle_manager.close_by_artifact(gov_id)
            await self.send(chat_id, f"✅ GOV {gov_id} resolved.")

        elif text.startswith("/role assign"):
            params = self._parse_role_params(text)
            otp = role_manager.initiate_assign(
                chat_id, params.name, params.telegram_id,
                params.role, params.project)
            await self.send(chat_id,
                f"⚠️ Role change: assign {params.role} to {params.name}\n"
                f"Reply APPROVE to confirm here\n"
                f"Or use code {otp} to confirm via MCP\n"
                f"Expires in 5 minutes.")

        elif text.startswith("/role modify"):
            params = self._parse_role_params(text)
            otp = role_manager.initiate_modify(
                chat_id, params.role, params.changes)
            await self.send(chat_id,
                f"⚠️ Role modify: {params.role}\n"
                f"Changes: {params.changes}\n"
                f"Reply APPROVE or use code {otp}")

        elif text.startswith("/role revoke"):
            params = self._parse_role_params(text)
            otp = role_manager.initiate_revoke(
                chat_id, params.role, params.name)
            await self.send(chat_id,
                f"⚠️ Role revocation: {params.role} from {params.name}\n"
                f"Reply APPROVE or use code {otp}")

        elif text == "APPROVE":
            if role_manager.has_pending_restore(chat_id):
                # Restore Phase 1 — Admin OP confirmed
                op_data = role_manager.confirm_restore_admin(chat_id, otp=None)
                if op_data:
                    op_telegram_id, op_otp = op_data
                    snapshot_id = role_manager._pending_restores[op_telegram_id]["snapshot_id"]
                    await self.send(op_telegram_id,
                        f"⚠️ Scope restoration requested by Admin OP.\n"
                        f"Target: {snapshot_id}\n"
                        f"Reply APPROVE to consent or REJECT to block.\n"
                        f"Code for MCP: {op_otp}\n"
                        f"Expires in 15 minutes.")
                    await self.send(chat_id, "✅ Your confirmation received. "
                        "Waiting for OP consent.")
                else:
                    await self.send(chat_id, "Confirmation failed or expired.")
            elif role_manager.has_pending_action(chat_id):
                # Role action (assign/modify/revoke)
                role_manager.confirm_pending(chat_id)
                await self.send(chat_id, "✅ Role action confirmed.")

        elif text.startswith("/sys"):
            instruction = text[5:].strip() or None
            await executor.execute_sys(audit_request=instruction)
            await self.send(chat_id, "🔍 SYS audit triggered.")

        elif text.startswith("/pause"):
            code = text[7:].strip()
            agent_control.pause(code)
            await self.send(chat_id, f"⏸️ {code} paused.")

        elif text.startswith("/resume"):
            code = text[8:].strip()
            agent_control.resume(code)
            await self.send(chat_id, f"▶️ {code} resumed.")

        elif text.startswith("/rotate"):
            code = text[8:].strip()
            new_id = instance_manager.rotate(code)
            await self.send(chat_id, f"🔄 {code} rotated to {new_id}.")

        elif text.startswith("/model"):
            code, model = text[7:].strip().split(None, 1)
            model_manager.set(code, model)
            await self.send(chat_id, f"🔧 {code} model → {model}.")

        elif text.startswith("/run"):
            code = text[5:].strip().upper()
            if code not in AGENTS:
                await self.send(chat_id, f"Unknown agent: {code}")
                return
            flag_for_execution(code)
            await self.send(chat_id, f"🚀 {code} queued for immediate execution.")

        elif text.startswith("/backlog"):
            # Admin OP sees both backlogs
            op_items = op_backlog.list_pending()
            admin_items = admin_backlog.list_pending()
            await self.send(chat_id, format_all_backlogs(op_items, admin_items))

        elif text.startswith("/config_notify"):
            role, channel, mode = self._parse_notify_config(text)
            notification_manager.set(role, channel, mode)
            await self.send(chat_id, f"🔔 {role}.{channel} → {mode}")

        else:
            # Freeform text during GOV exchange (cycle-internal, §6.4)
            current = admin_backlog.get_in_progress()
            if not current:
                pending_items = admin_backlog.list_pending()
                if pending_items:
                    current = pending_items[0]
                    admin_backlog.start_exchange(current.id)
            if current:
                cycle = cycle_manager.get_active_cycle("SYS", current)
                if cycle:
                    cycle.append_turn(text, "ADMIN_OP")
                    flag_for_execution("SYS", cycle_id=cycle.id)
                    await self.send(chat_id, "↩️ Forwarded to SYS.")
            else:
                await self.send(chat_id, "No active GOV exchange.")

    async def handle_de_message(self, chat_id, text):
        """DE: domain expertise directly with SG."""
        if text.startswith("/de_respond"):
            artifact_id, response = self._extract_id_and_text(text)
            de_in = create_artifact(type="DE_IN", sender="DE", 
                                    content=response, references=[artifact_id])
            de_in.id = await sequence_manager.next_id("DE", "DE_IN")
            await router.route(de_in)
            await self.send(chat_id, f"✅ {de_in.id} sent to SG.")

        elif text.startswith("/de_observe"):
            observation = text[12:].strip()
            de_in = create_artifact(type="DE_IN", sender="DE",
                                    content=observation)
            de_in.id = await sequence_manager.next_id("DE", "DE_IN")
            await router.route(de_in)
            await self.send(chat_id, f"📝 Observation {de_in.id} sent to SG.")

        elif text.startswith("/de_pending"):
            pending = cycle_manager.get_pending_for_role("DE")
            await self.send(chat_id, format_pending(pending))

        elif text.startswith("/de_history"):
            history = cycle_manager.get_history_for_role("DE")
            await self.send(chat_id, format_history(history))

    async def handle_ext_message(self, chat_id, text):
        """EXT: build interaction directly with BR."""
        if text.startswith("/ext_submit"):
            results = text[12:].strip()
            brq = create_artifact(type="BRQ", sender="EXT", content=results)
            brq.id = await sequence_manager.next_id("EXT", "BRQ")
            await router.route(brq)
            await self.send(chat_id, f"✅ {brq.id} submitted to BR.")

        elif text.startswith("/ext_ask"):
            question = text[9:].strip()
            brq = create_artifact(type="BRQ", sender="EXT", content=question)
            brq.id = await sequence_manager.next_id("EXT", "BRQ")
            await router.route(brq)
            await self.send(chat_id, f"❓ {brq.id} sent to BR.")

        elif text.startswith("/ext_pending"):
            pending = cycle_manager.get_pending_for_role("EXT")
            await self.send(chat_id, format_pending(pending))

        elif text.startswith("/ext_history"):
            history = cycle_manager.get_history_for_role("EXT")
            await self.send(chat_id, format_history(history))

    # --- Read commands (shared across roles, same data sources as MCP read tools) ---

    async def handle_read_command(self, chat_id, text, role):
        """Handle Telegram read commands. Uses the same data sources as
        the MCP read-tool contracts (SC-1). Formats for Telegram with
        4000-char truncation + 'use vega_history for full content' suffix."""
        cmd = text.split()[0]
        args = text[len(cmd):].strip()

        if cmd == "/status":
            data = format_status(self._read_status())
        elif cmd == "/agent":
            data = format_agent(self._read_agent(args))
        elif cmd == "/history":
            artifact = artifact_store.load_archived(args)
            routing = routing_log.get_entries(args)
            data = (f"**{args}**\n\n{artifact.content if artifact else 'Not found'}"
                    f"\n\n---\nRouting: {format_routing(routing)}")
        elif cmd == "/thinking":
            data = self._read_thinking(args)
        elif cmd == "/wiki":
            data = format_wiki_map(wiki_manager.read_all(args))
        elif cmd == "/log":
            parts = args.split()
            code, n = parts[0], int(parts[1]) if len(parts) > 1 else 10
            data = wiki_manager.read_log(code, n)
        elif cmd == "/cycles":
            if args:
                cycle = cycle_manager.get_active_by_artifact(args)
                if cycle:
                    data = format_cycle_messages(cycle.messages)
                else:
                    data = f"No active cycle for {args}."
            else:
                data = format_cycles(cycle_manager.list_active())
        elif cmd == "/scope":
            data = self._read_scope(args)
        elif cmd == "/verify":
            data = format_verify(self._read_verify())
        elif cmd == "/snapshots":
            data = format_snapshots(self._read_snapshots())
        elif cmd in ("/agents", "/routing", "/decisions", "/framework"):
            data = self._read_reference(cmd, args)
        else:
            data = f"Unknown command: {cmd}"

        await self.send(chat_id, truncate(data, 4000))

    # --- Agent response forwarding (push notifications per role) ---

    async def forward_to_role(self, artifact, role):
        """Forward agent response to the appropriate role's Telegram."""
        chat_id = role_manager.get_telegram_id(role)
        if not chat_id:
            return
        
        thinking = self._get_thinking_summary(artifact)
        thinking_section = f"\n💭 Thinking: {thinking}\n" if thinking else ""

        msg = (
            f"💬 **{artifact.sender} response** (re: {artifact.references[0]})\n\n"
            f"{artifact.content_summary}\n"
            f"{thinking_section}"
        )
        await self.send(chat_id, msg)
```

### 10.3 Role Manager

```python
import hashlib
import secrets
import time
import json

class RoleManager:
    def __init__(self, roles_file, invites_dir, events_file, recovery_file):
        self.roles_file = roles_file          # config/roles.json
        self.invites_dir = invites_dir        # config/invites/
        self.events_file = events_file        # state/role_events.jsonl
        self.recovery_file = recovery_file    # config/recovery.hash
        self._pending_role_actions = {}       # chat_id → {action, params, otp, expires}
        self._pending_activations = {}        # invite_code → {action, invite, otp, expires}
        self._pending_restores = {}           # chat_id → {snapshot_id, otp, expires} (§7.5)

    # --- Token management ---

    def _hash_token(self, token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def verify_token(self, token: str) -> dict | None:
        """Verify MCP token, return role info or None."""
        token_hash = self._hash_token(token)
        roles = self._load_roles()
        for role_name, role_data in roles.items():
            if role_data.get("token_hash") == token_hash:
                return {"role": role_name, **role_data}
        return None

    def get_role_by_telegram(self, chat_id: str) -> str | None:
        """Look up role by Telegram chat ID."""
        roles = self._load_roles()
        for role_name, role_data in roles.items():
            if str(role_data.get("telegram_id")) == str(chat_id):
                return role_name
        return None

    def get_telegram_id(self, role: str) -> str | None:
        roles = self._load_roles()
        return roles.get(role, {}).get("telegram_id")

    # --- Role assignment (2FA required) ---

    def initiate_assign(self, chat_id, name, telegram_id, role, project):
        """Start role assignment — generates OTP, stores pending action."""
        otp = self._generate_otp()
        self._pending_role_actions[chat_id] = {
            "action": "assign",
            "params": {"name": name, "telegram_id": telegram_id, 
                       "role": role, "project": project},
            "otp": otp,
            "expires": time.time() + OTP_EXPIRY
        }
        return otp

    def confirm_pending(self, chat_id, otp=None):
        """Confirm pending role action. OTP required if confirming via MCP.
        Returns True/False. Restore flow uses separate methods."""
        pending = self._pending_role_actions.get(chat_id)
        if not pending or time.time() > pending["expires"]:
            self._log_event("2fa_expired", chat_id=chat_id)
            return False
        if otp and otp != pending["otp"]:
            self._log_event("2fa_failed", chat_id=chat_id)
            return False
        
        # Execute the action
        action = pending["action"]
        params = pending["params"]
        
        if action == "assign":
            self._execute_assign(params)
        elif action == "revoke":
            self._execute_revoke(params)
        elif action == "modify":
            self._execute_modify(params)
        
        del self._pending_role_actions[chat_id]
        return True

    def has_pending_action(self, chat_id) -> bool:
        pending = self._pending_role_actions.get(chat_id)
        return pending is not None and time.time() <= pending["expires"]

    # --- Invite / Activation (D-ARCH-040) ---

    def create_invite(self, role, telegram_id, name):
        """Generate invite code, store in invites/ directory."""
        code = f"VEGA-{role}-{secrets.token_hex(3).upper()}"
        invite = {
            "code": code,
            "role": role,
            "telegram_id": telegram_id,
            "name": name,
            "created": time.time(),
            "expires": time.time() + INVITE_EXPIRY
        }
        atomic_write(f"{self.invites_dir}/{code}.json", json.dumps(invite))
        self._log_event("invite_created", role=role, target=name,
                        telegram_id=telegram_id)
        return code

    def activate_invite(self, invite_code) -> dict | None:
        """Validate invite code, return invite data or None."""
        path = f"{self.invites_dir}/{invite_code}.json"
        if not os.path.exists(path):
            return None
        invite = json.loads(read_file(path))
        if time.time() > invite["expires"]:
            os.remove(path)
            return None
        return invite

    def complete_activation(self, invite_code, otp_verified=True):
        """Generate permanent token, store hashed, invalidate invite."""
        invite = self.activate_invite(invite_code)
        if not invite or not otp_verified:
            return None
        
        # Generate permanent token
        token = f"vega_{secrets.token_hex(32)}"
        token_hash = self._hash_token(token)
        
        # Store in roles.json
        roles = self._load_roles()
        roles[invite["role"]] = {
            "name": invite["name"],
            "telegram_id": invite["telegram_id"],
            "token_hash": token_hash,
            "assigned_at": time.time()
        }
        self._save_roles(roles)
        
        # Invalidate invite
        os.remove(f"{self.invites_dir}/{invite_code}.json")
        
        # Log
        self._log_event("role_activated", role=invite["role"],
                        target=invite["name"],
                        telegram_id=invite["telegram_id"])
        
        # Return token (shown once, never stored in plain text)
        return token

    # --- Revocation ---

    def initiate_revoke(self, chat_id, role, name):
        otp = self._generate_otp()
        self._pending_role_actions[chat_id] = {
            "action": "revoke",
            "params": {"role": role, "name": name},
            "otp": otp,
            "expires": time.time() + OTP_EXPIRY
        }
        return otp

    def _execute_revoke(self, params):
        roles = self._load_roles()
        if params["role"] in roles:
            del roles[params["role"]]
            self._save_roles(roles)
            self._log_event("role_revoked", role=params["role"],
                            target=params["name"])

    # --- Modification (update telegram_id or name, token unchanged) ---

    def initiate_modify(self, chat_id, role, changes):
        otp = self._generate_otp()
        self._pending_role_actions[chat_id] = {
            "action": "modify",
            "params": {"role": role, "changes": changes},
            "otp": otp,
            "expires": time.time() + OTP_EXPIRY
        }
        return otp

    def _execute_modify(self, params):
        roles = self._load_roles()
        role = params["role"]
        if role not in roles:
            return
        changes = params["changes"]
        if "telegram_id" in changes:
            roles[role]["telegram_id"] = changes["telegram_id"]
        if "name" in changes:
            roles[role]["name"] = changes["name"]
        self._save_roles(roles)
        self._log_event("role_modified", role=role,
                        changes=changes)

    # --- Break-glass recovery (§13.6) ---

    def verify_recovery_key(self, key: str) -> bool:
        stored_hash = read_file(self.recovery_file).strip()
        return self._hash_token(key) == stored_hash

    def execute_recovery(self, key, new_admin_telegram_id):
        """Reset Admin OP credentials via server CLI."""
        if not self.verify_recovery_key(key):
            return False
        # Revoke all Admin OP tokens
        roles = self._load_roles()
        if "ADMIN_OP" in roles:
            del roles["ADMIN_OP"]
            self._save_roles(roles)
        # Generate new invite for Admin OP
        code = self.create_invite("ADMIN_OP", new_admin_telegram_id, "recovery")
        self._log_event("recovery_executed", 
                        new_telegram_id=new_admin_telegram_id)
        return code

    # --- Scope restoration 2FA (§7.5) ---
    # Uses _pending_restores dict (separate from _pending_role_actions and _pending_activations).
    # Actual file operations in standalone restore_scope() function — 
    # RoleManager handles only the 2FA flow.

    def initiate_restore(self, admin_chat_id, snapshot_id):
        """Phase 1: Admin OP initiates."""
        otp = self._generate_otp()
        self._pending_restores[admin_chat_id] = {
            "snapshot_id": snapshot_id,
            "otp": otp,
            "expires": time.time() + OTP_EXPIRY,
        }
        return otp

    def confirm_restore_admin(self, admin_chat_id, otp):
        """Phase 1 confirmed. Prepare Phase 2 (OP consent)."""
        pending = self._pending_restores.get(admin_chat_id)
        if not pending or pending["otp"] != otp or time.time() > pending["expires"]:
            return None
        snapshot_id = pending["snapshot_id"]
        op_telegram_id = self.get_telegram_id("OP")
        op_otp = self._generate_otp()
        del self._pending_restores[admin_chat_id]
        self._pending_restores[op_telegram_id] = {
            "snapshot_id": snapshot_id,
            "otp": op_otp,
            "expires": time.time() + OTP_EXPIRY,
        }
        self._log_event("restore_initiated", snapshot_id=snapshot_id)
        return op_telegram_id, op_otp

    def confirm_restore_op(self, op_chat_id, otp):
        """Phase 2 confirmed. Return snapshot_id for execution."""
        pending = self._pending_restores.get(op_chat_id)
        if not pending or pending["otp"] != otp or time.time() > pending["expires"]:
            return None
        snapshot_id = pending["snapshot_id"]
        del self._pending_restores[op_chat_id]
        return snapshot_id

    def has_pending_restore(self, chat_id) -> bool:
        pending = self._pending_restores.get(chat_id)
        return pending is not None and time.time() <= pending["expires"]

    # --- Audit trail (read by SYS per §5.9 responsibility 6) ---

    # role_events.jsonl schema — one JSON object per line:
    # {
    #   "timestamp": "2026-05-28T10:00:00Z",  (ISO-8601, always Z-suffix)
    #   "action": "assign|revoke|modify|invite_created|role_activated|
    #              recovery_executed|2fa_failed|2fa_expired|
    #              snapshot_failed|restore_initiated|restore_executed",
    #   "actor_role": "ADMIN_OP",              (who initiated — null for self-service activation)
    #   "actor_telegram_id": "12345",           (initiator's telegram)
    #   "target_role": "DE",                    (role being assigned/modified/revoked)
    #   "target_name": "Hassan",                (display name)
    #   "target_telegram_id": "67890",          (assignee's telegram)
    #   "project": "LOINC Resolver",            (if multi-project)
    #   "result": "success|failed|pending"      (outcome)
    # }
    # SYS flags: off-hours changes, rapid assign/revoke cycles, 
    # repeated 2fa_failed, recovery_executed events.

    def _log_event(self, action, **kwargs):
        """Append to role_events.jsonl — immutable audit trail."""
        event = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "action": action,
            **kwargs
        }
        atomic_append(self.events_file, json.dumps(event) + "\n")

    # --- Internals ---

    def _generate_otp(self) -> str:
        return str(secrets.randbelow(900000) + 100000)  # 6-digit

    def _load_roles(self) -> dict:
        return load_json(self.roles_file, default={})

    def _save_roles(self, roles):
        atomic_write(self.roles_file, json.dumps(roles, indent=2))

    def _execute_assign(self, params):
        # Create invite → send to assignee's Telegram
        code = self.create_invite(
            params["role"], params["telegram_id"], params["name"]
        )
        telegram_bot.send(params["telegram_id"],
            f"You've been approved as {params['role']} for {params['project']}.\n"
            f"Your invite code: {code}\n"
            f"Configure MCP with this code and say 'activate my VEGA role.'\n"
            f"Expires in 24 hours.")
```

---

## 11. Command Library

```python
COMMANDS = {
    # === OP COMMANDS ===
    # STATUS
    "/help":     "Show all available commands for your role",
    "/status":   "System overview: agent states, inbox counts, pending items",
    "/backlog":  "List pending PROP items with priorities",
    "/agent":    "Usage: /agent [code] — Agent details",
    "/history":  "Usage: /history [artifact-id] — Full routing history",
    "/pending":  "List active PROP exchanges awaiting response",
    "/cycles":   "List active conversation cycles",

    # SCOPE DECISIONS
    "/approve":  "Usage: /approve [artifact-id] — Approve PROP",
    "/reject":   "Usage: /reject [artifact-id] [reason]",
    "/modify":   "Usage: /modify [artifact-id] [instructions]",
    "/request":  "Usage: /request [message] — Submit REQ-OP-NNN to SG",

    # MONITORING
    "/wiki":     "Usage: /wiki [code] — Agent wiki summary",
    "/log":      "Usage: /log [code] [N] — Last N log entries",
    "/thinking": "Usage: /thinking [artifact-id] — Show thinking blocks",

    # CROSS-CHANNEL VISIBILITY (read-only)
    "/de_activity":  "DE↔SG exchange summaries",
    "/ext_activity": "EXT↔BR exchange summaries",

    # SCOPE DOCUMENTS (SC-3)
    "/scope":    "Usage: /scope [doc] — List scope docs, or show full content of one",

    # VERIFICATION (SC-7)
    "/verify":   "Compare live scope hashes against last validated snapshot",
    "/snapshots":"List available validated-state snapshots",

    # REFERENCE
    "/agents":   "List all agents with roles",
    "/routing":  "Usage: /routing [type] — Show where artifact type routes to",
    "/decisions":"Usage: /decisions [prefix] — Recent decisions (D-, TD-, GD-)",
    "/framework":"Usage: /framework [section] — Quick-reference a framework section",

    # === ADMIN OP COMMANDS (in addition to all OP read commands) ===
    # GOVERNANCE
    "/sys":      "Usage: /sys [instruction] — Trigger SYS audit",
    "/resolve":  "Usage: /resolve [GOV-id] [action] — Close GOV exchange",

    # ROLE MANAGEMENT (2FA required)
    "/role":     "Usage: /role assign|modify|revoke [params] — Manage roles (2FA)",
    "/roles":    "List current role assignments",

    # AGENT CONTROL
    "/pause":    "Usage: /pause [code] — Pause agent",
    "/resume":   "Usage: /resume [code] — Resume agent",
    "/rotate":   "Usage: /rotate [code] — Rotate instance ID",
    "/run":      "Usage: /run [code] — Execute agent immediately (Admin OP)",
    "/model":    "Usage: /model [code] [model-string] — Change agent model",
    "/models":   "Show current model assignments",
    "/config_notify": "Usage: /config_notify [role] [channel] [push|pull]",

    # RECOVERY (SC-7, dual 2FA: Admin OP initiates, OP consents)
    "/restore":  "Usage: /restore [SNAP-id] — Restore scope to snapshot (dual 2FA)",

    # === DE COMMANDS ===
    "/de_respond":  "Usage: /de_respond [artifact-id] [response]",
    "/de_observe":  "Usage: /de_observe [message] — Initiate domain observation",
    "/de_history":  "DE↔SG exchange history",
    "/de_pending":  "Outstanding DE_OUT awaiting response",

    # === EXT COMMANDS ===
    "/ext_submit":  "Usage: /ext_submit [results] — Submit test results",
    "/ext_ask":     "Usage: /ext_ask [question] — Ask BR a question",
    "/ext_history": "BR↔EXT exchange history",
    "/ext_pending": "Outstanding BRP awaiting response",
}
```

Commands are filtered by Telegram chat ID — each person sees only their role's commands via `/help`.

---

**Note for OP: Not all work goes through SG.**

- Scope changes → SG (via /request or process findings from agents)
- Framework/architecture changes → OP edits directly, ask Admin OP to /sys verify consistency
- Governance concerns → Admin OP channel (/sys for audit)
- Agent configuration → Admin OP channel (/model, /rotate, /pause, /resume)
- Role management → Admin OP channel (/role — 2FA required)

SG governs scope documents. OP governs the framework.

---

## 12. External Interfaces

### 12.1 Interface Model — Telegram + MCP per Role

Every role has two interface channels:

**Telegram (push).** Notifications arrive on the person's phone. Quick actions via structured commands. Role-specific Telegram conversations. Optional shared Telegram group for notification-only broadcasts.

**MCP Server (pull + substantive work).** One HTTP endpoint. Token determines which tools are visible — each role sees only their domain's tools. Unauthenticated connections see only `vega_request_access()`.

```
┌─────────────────────────────────────────────────────────┐
│                    Orchestrator                          │
│                  (shared internals)                      │
└───┬──────────┬──────────┬──────────┬──────────┬─────────┘
    │          │          │          │          │
┌───┴───┐ ┌───┴───┐ ┌───┴───┐ ┌───┴───┐ ┌───┴──────┐
│Telegram│ │ MCP   │ │ MCP   │ │ MCP   │ │ MCP      │
│ Bot    │ │ OP    │ │Admin  │ │ DE    │ │ EXT      │
│(push)  │ │ tools │ │OP     │ │ tools │ │ tools    │
│        │ │       │ │ tools │ │       │ │          │
└────────┘ └───────┘ └───────┘ └───────┘ └──────────┘
     │          │          │          │          │
   all roles   OP      Admin OP      DE        EXT
```

**Deployment:** Same process as orchestrator (shared Python objects), HTTP endpoint. Claude sessions connect remotely. Any MCP-compatible client works — tokens authenticate the connection, not the client software. HTTPS mandatory for production.

**Mediation note:** Messages submitted via MCP may be paraphrased by the mediating Claude/LLM session. The cycle archive records what was sent to the agent, not what the person typed. For word-exact intent: instruct the LLM "send exactly this: ..." or use Telegram. SYS should treat MCP-sourced exchange turns as role-directed but potentially mediated.

### 12.2 MCP Tools by Role

**Lobby (no token — unauthenticated):**
```python
vega_request_access(name, telegram_id, role, project)  # Request role assignment
```

**OP tools (scope decisions):**
```python
# Scope
vega_approve(artifact_id)
vega_reject(artifact_id, reason)
vega_modify(artifact_id, instructions)
vega_exchange(artifact_id, message)    # Multi-turn SG↔OP dialogue
vega_request(message)                   # Submit REQ-OP-NNN to SG
vega_backlog()                          # Pending PROP items
vega_scope()                            # List scope docs (SC-3)
vega_scope(doc)                         # Full content of a scope document (SC-3)

# Monitoring
vega_status()
vega_agent(code)
vega_cycles()                           # List active cycles
vega_cycles(artifact_id)                # Read cycle messages for an exchange
vega_history(artifact_id)               # Artifact body + routing trail (SC-2)
vega_thinking(artifact_id)
vega_wiki(code)
vega_log(code, n)

# Verification (SC-7)
vega_verify()                           # Compare live scope hashes vs last snapshot
vega_snapshots()                        # List available validated-state snapshots

# Cross-channel visibility (read-only)
vega_de_activity()                      # DE↔SG exchange summaries
vega_ext_activity()                     # EXT↔BR exchange summaries
```

**Admin OP tools (governance + role management):**
```python
# Governance
vega_sys(instruction)                   # Trigger SYS audit
vega_resolve(gov_id, action)            # Close GOV exchange
vega_exchange(artifact_id, message)     # Multi-turn SYS↔Admin OP dialogue

# Role management (2FA required — see Framework §13.4)
vega_assign_role(name, telegram_id, role, project)
vega_modify_role(role, telegram_id, changes)
vega_revoke_role(role, telegram_id)
vega_roles()                            # List current assignments
vega_activate_role(invite_code)         # Used by invitee during onboarding

# Agent control
vega_model(code, model)
vega_rotate(code)
vega_pause(code)
vega_resume(code)

# Notification config
vega_config_notifications(role, channel, mode)  # push/pull per role

# Full monitoring (all OP tools plus)
vega_status()
vega_backlog()                          # All backlogs (OP + Admin OP)
vega_agent(code)
vega_cycles()
vega_cycles(artifact_id)                # Read cycle messages for an exchange
vega_history(artifact_id)               # Artifact body + routing trail (SC-2)
vega_thinking(artifact_id)
vega_wiki(code)
vega_log(code, n)
vega_scope()                            # List scope docs
vega_scope(doc)                         # Full content of a scope document

# Verification and recovery (SC-7)
vega_verify()                           # Compare live scope hashes vs last snapshot
vega_snapshots()                        # List available snapshots
vega_restore(snapshot_id)               # Restore scope to snapshot (dual 2FA: Admin OP + OP)
```

**DE tools (domain expertise):**
```python
vega_de_respond(artifact_id, response)  # Respond to DE_OUT
vega_de_observe(message)                # Initiate domain observation to SG
vega_de_history()                       # Own DE↔SG exchange history
vega_de_pending()                       # Outstanding DE_OUT awaiting response
vega_scope()                            # List scope docs
vega_scope(doc)                         # Full content of a scope document
```

**EXT tools (build interaction):**
```python
vega_ext_submit(results)                # Submit test results / build report
vega_ext_ask(question)                  # Ask BR a question
vega_ext_history()                      # Own BR↔EXT exchange history
vega_ext_pending()                      # Outstanding BRP awaiting response
vega_scope()                            # List scope docs
vega_scope(doc)                         # Full content of a scope document
```

### 12.3 MCP Server Implementation

```python
from aiohttp import web
import json

# Tool definitions per role — each role sees only their tools
ROLE_TOOLS = {
    None: ["vega_request_access"],  # Lobby — no token
    "_PENDING": ["vega_activate_role"],  # Invite holder, not yet activated
    "OP": [
        "vega_approve", "vega_reject", "vega_modify", "vega_request",
        "vega_exchange", "vega_backlog", "vega_status", "vega_agent",
        "vega_cycles", "vega_history", "vega_thinking", "vega_wiki",
        "vega_log", "vega_scope", "vega_de_activity", "vega_ext_activity",
        "vega_verify", "vega_snapshots",
    ],
    "ADMIN_OP": [
        "vega_sys", "vega_resolve", "vega_exchange",
        "vega_assign_role", "vega_modify_role", "vega_revoke_role",
        "vega_roles", "vega_activate_role",
        "vega_model", "vega_rotate", "vega_pause", "vega_resume", "vega_run",
        "vega_config_notifications",
        "vega_status", "vega_backlog", "vega_agent", "vega_cycles",
        "vega_history", "vega_thinking", "vega_wiki", "vega_log",
        "vega_scope", "vega_verify", "vega_snapshots", "vega_restore",
    ],
    "DE": [
        "vega_de_respond", "vega_de_observe",
        "vega_de_history", "vega_de_pending",
        "vega_scope",
    ],
    "EXT": [
        "vega_ext_submit", "vega_ext_ask",
        "vega_ext_history", "vega_ext_pending",
        "vega_scope",
    ],
}
```

**Read-tool contracts (SC-1).** Read tools are pure queries — they modify no state, create no artifacts, and route nothing. The contract below defines the data source, return content, and role access for every read tool. The implementer may dispatch via a shared `_handle_read_tool` method or per-tool methods — the contract is the same either way.

| Tool | Data source | Returns | Roles |
|------|-----------|---------|-------|
| `vega_status()` | Inboxes, backlogs, active cycles, SYS watermark | System overview: per-agent inbox count, pending PROP/GOV counts, active cycle count, last SYS run | OP, Admin OP |
| `vega_agent(code)` | execution_log, wiki dir, model_assignments, inbox | Agent detail: last execution, model, instance ID, wiki size, inbox count | OP, Admin OP |
| `vega_cycles()` | Active cycles directory | List: id, type, participants, turn count, opened_at | OP, Admin OP |
| `vega_cycles(artifact_id)` | Active cycle messages array | Full cycle messages for the exchange anchored to this artifact. Enables MCP-driven exchanges without switching to Telegram. | OP, Admin OP |
| `vega_history(id)` | `artifacts/archive/{id}.md` (primary) + `routing_log.json` (secondary) | **Full artifact body** from archive + routing metadata. Body is primary; routing is supplementary. | OP, Admin OP |
| `vega_thinking(id)` | `artifacts/archive/{id}.thinking.md` (sidecar, SC-4); fallback: `execution_log.json` via `artifact_index.json` | Agent thinking blocks. Empty if none. | OP, Admin OP |
| `vega_wiki(code)` | `agents/{code}/wiki/` | File map: filename → size | OP, Admin OP |
| `vega_log(code, n)` | `agents/{code}/wiki/log.md` | Last N log entries | OP, Admin OP |
| `vega_scope()` | `scope/` directory | List: doc name, version, size, last modified | OP, Admin OP, DE, EXT |
| `vega_scope(doc)` | `scope/{doc}` | Full document content (no truncation) | OP, Admin OP, DE, EXT |
| `vega_backlog()` | `op_backlog/pending/` or `admin_backlog/pending/` | Pending items: id, type, priority, sender, timestamp | OP (own), Admin OP (all) |
| `vega_de_activity()` | Active + archived `de_qa` cycles | DE↔SG exchange summaries | OP |
| `vega_ext_activity()` | Active + archived `build_*` cycles | EXT↔BR exchange summaries | OP |
| `vega_de_pending()` | Active cycles involving DE | Cycles awaiting DE response | DE |
| `vega_ext_pending()` | Active cycles involving EXT | Cycles awaiting EXT response | EXT |
| `vega_de_history()` | Archived DE↔SG cycles | Recent closed DE exchanges | DE |
| `vega_ext_history()` | Archived EXT↔BR cycles | Recent closed EXT exchanges | EXT |
| `vega_verify()` | Last snapshot manifest + live `scope/` | Per-doc hash match/mismatch + snapshot ID | OP, Admin OP |
| `vega_snapshots()` | `snapshots/` directory | Available snapshots: id, timestamp, trigger | OP, Admin OP |
| `vega_restore(id)` | `snapshots/{id}/` | Initiate restoration (dual 2FA) | Admin OP only |
| `vega_roles()` | `config/roles.json` | Role assignments: role → name + telegram_id (no hashes) | Admin OP |

**SC-2 disambiguation: "history" vs "routing trail."** `vega_history(id)` returns the **artifact body** (primary, from the immutable archive) and **routing metadata** (secondary, from `routing_log.json`). These are clearly labelled in the response. The artifact body is what OP needs for decision-making. The routing metadata is supplementary context showing how the artifact moved through the system. If the archive has the artifact but the routing log doesn't (e.g., bootstrap import per SC-6), the body is still returned (`found: true, routing: []`). OP read-path guarantee: OP must always have a path to read the full body of any archived artifact.

```python
class MCPServer:
    def __init__(self, role_manager, router, op_backlog, admin_backlog,
                 executor, cycle_manager, sequence_manager, wiki_manager):
        self.role_manager = role_manager
        self.router = router
        self.op_backlog = op_backlog
        self.admin_backlog = admin_backlog
        self.executor = executor
        self.cycles = cycle_manager
        self.sequences = sequence_manager
        self.wiki = wiki_manager
        self.app = web.Application()
        self.app.router.add_post("/mcp", self.handle_mcp)

    async def start(self):
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", MCP_PORT)
        await site.start()

    # --- Authentication + role resolution ---

    def _authenticate(self, request) -> dict | None:
        """Extract bearer token, verify, return role info.
        Returns None for lobby (no token).
        Returns role dict for valid permanent tokens.
        Returns _PENDING dict for valid invite codes.
        Raises 401 for invalid tokens."""
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return None  # Lobby — unauthenticated
        token = auth_header[7:]
        # Check permanent token first
        role_info = self.role_manager.verify_token(token)
        if role_info:
            return role_info
        # Check invite code
        invite = self.role_manager.activate_invite(token)
        if invite:
            return {"role": "_PENDING", "invite_code": token, "invite": invite}
        raise web.HTTPUnauthorized(text="Invalid token")

    # --- Tool filtering ---

    def _get_available_tools(self, role: str | None) -> list:
        """Return tool definitions visible to this role."""
        tool_names = ROLE_TOOLS.get(role, [])
        return [TOOL_DEFINITIONS[name] for name in tool_names
                if name in TOOL_DEFINITIONS]

    # --- MCP protocol handler ---

    async def handle_mcp(self, request):
        """Main MCP endpoint — handles list_tools and call_tool."""
        role_info = self._authenticate(request)
        role = role_info["role"] if role_info else None
        body = await request.json()

        if body.get("method") == "tools/list":
            tools = self._get_available_tools(role)
            return web.json_response({"tools": tools})

        elif body.get("method") == "tools/call":
            tool_name = body["params"]["name"]
            arguments = body["params"].get("arguments", {})

            # Verify tool is available to this role
            allowed = ROLE_TOOLS.get(role, [])
            if tool_name not in allowed:
                raise web.HTTPForbidden(
                    text=f"Tool {tool_name} not available for role {role}")

            # Dispatch to handler
            result = await self._dispatch(tool_name, arguments, role_info)
            return web.json_response({"content": [{"type": "text", "text": result}]})

        return web.json_response({"error": "Unknown method"}, status=400)

    # --- Tool dispatch ---

    async def _dispatch(self, tool_name, args, role_info):
        """Route tool call to orchestrator operation."""
        role = role_info["role"] if role_info else None

        # Lobby
        if tool_name == "vega_request_access":
            return await self._handle_request_access(args)

        # OP tools
        elif tool_name == "vega_approve":
            auth = create_auth(args["artifact_id"], "approve")
            await self.router.route(auth)
            self.op_backlog.resolve(args["artifact_id"], auth)
            self.cycles.close_by_artifact(args["artifact_id"])
            # SC-7: snapshot on approve
            if SNAPSHOT_ENABLED:
                await self._write_snapshot(auth)
            return f"AUTH issued for {args['artifact_id']}."

        elif tool_name == "vega_reject":
            rej = create_artifact(type="AUTH", sender="OP",
                content=f"REJECTED: {args['reason']}",
                references=[args["artifact_id"]],
                disposition="reject")
            rej.id = await self.sequences.next_id("OP", "AUTH")
            await self.router.route(rej)
            self.op_backlog.resolve(args["artifact_id"])
            self.cycles.close_by_artifact(args["artifact_id"])
            return f"Rejected {args['artifact_id']}."

        elif tool_name == "vega_modify":
            mod = create_artifact(type="AUTH", sender="OP",
                content=f"MODIFY: {args['instructions']}",
                references=[args["artifact_id"]],
                disposition="modify")
            mod.id = await self.sequences.next_id("OP", "AUTH")
            await self.router.route(mod)
            # Cycle stays open — SG revises and produces new PROP
            return f"Modification requested for {args['artifact_id']}."

        elif tool_name == "vega_request":
            req = create_artifact(type="REQ", sender="OP", content=args["message"])
            req.id = await self.sequences.next_id("OP", "REQ")
            await self.router.route(req)
            return f"{req.id} submitted to SG."

        elif tool_name == "vega_exchange":
            # Cycle-internal exchange turn (§6.4)
            cycle = self.cycles.get_active_by_artifact(args["artifact_id"])
            if cycle:
                cycle.append_turn(args["message"], role)
                partner = "SG" if role == "OP" else "SYS"
                flag_for_execution(partner, cycle_id=cycle.id)
                return f"Message added to {cycle.id}. {partner} will respond."
            return "No active exchange for this artifact."

        elif tool_name == "vega_backlog":
            if role == "ADMIN_OP":
                items = self.op_backlog.list_pending() + self.admin_backlog.list_pending()
            else:
                items = self.op_backlog.list_pending()
            return format_backlog(items)

        # Admin OP tools
        elif tool_name == "vega_resolve":
            self.admin_backlog.resolve(args["gov_id"], args["action"])
            self.cycles.close_by_artifact(args["gov_id"])
            return f"GOV {args['gov_id']} resolved."

        elif tool_name == "vega_assign_role":
            otp = self.role_manager.initiate_assign(
                role_info["telegram_id"],
                args["name"], args["telegram_id"], args["role"], args["project"])
            return f"2FA required. Code {otp} sent to your Telegram. Enter it here to confirm."

        elif tool_name == "vega_modify_role":
            otp = self.role_manager.initiate_modify(
                role_info["telegram_id"], args["role"], args["changes"])
            return f"2FA required. Code {otp} sent to your Telegram. Enter it here to confirm."

        elif tool_name == "vega_revoke_role":
            otp = self.role_manager.initiate_revoke(
                role_info["telegram_id"], args["role"], args.get("name", ""))
            return f"2FA required. Code {otp} sent to your Telegram. Enter it here to confirm."

        elif tool_name == "vega_activate_role":
            # Two-phase activation (D-ARCH-040)
            if "otp" in args and args["otp"]:
                # Phase 2: verify OTP, complete activation, return permanent token
                pending = self.role_manager._pending_activations.get(args["invite_code"])
                if not pending or time.time() > pending["expires"]:
                    return "Expired. Request a new invite from Admin OP."
                if args["otp"] != pending["otp"]:
                    self.role_manager._log_event("2fa_failed",
                        invite_code=args["invite_code"])
                    return "Invalid code. Try again."
                token = self.role_manager.complete_activation(args["invite_code"])
                if token:
                    return (f"✅ Role activated.\n"
                            f"Your permanent token: {token}\n"
                            f"Update your MCP connection settings with this token.\n"
                            f"This is the only time it will be shown.")
                return "Activation failed."
            else:
                # Phase 1: validate invite, send OTP to invitee's Telegram
                invite = self.role_manager.activate_invite(args["invite_code"])
                if not invite:
                    return "Invalid or expired invite code."
                otp = self.role_manager._generate_otp()
                await telegram_bot.send(invite["telegram_id"],
                    f"Activation code: {otp}")
                self.role_manager._pending_activations[args["invite_code"]] = {
                    "action": "activate", "invite": invite,
                    "otp": otp, "expires": time.time() + OTP_EXPIRY
                }
                return "Activation code sent to your Telegram. Enter it here."

        elif tool_name == "vega_sys":
            instruction = args.get("instruction")
            await self.executor.execute_sys(audit_request=instruction)
            return "SYS audit triggered."

        elif tool_name == "vega_pause":
            self.executor.pause(args.get("agent_code"))
            return f"Agent {args.get('agent_code', 'all')} paused."

        elif tool_name == "vega_resume":
            self.executor.resume(args.get("agent_code"))
            return f"Agent {args.get('agent_code', 'all')} resumed."

        elif tool_name == "vega_rotate":
            self.executor.rotate_instance(args["agent_code"])
            return f"Instance rotated for {args['agent_code']}."

        elif tool_name == "vega_model":
            self.executor.set_model(args["agent_code"], args["model"])
            return f"Model for {args['agent_code']} set to {args['model']}."

        elif tool_name == "vega_run":
            flag_for_execution(args["agent_code"])
            return f"{args['agent_code']} queued for immediate execution."

        # DE tools
        elif tool_name == "vega_de_respond":
            de_in = create_artifact(type="DE_IN", sender="DE",
                                    content=args["response"],
                                    references=[args["artifact_id"]])
            de_in.id = await self.sequences.next_id("DE", "DE_IN")
            await self.router.route(de_in)
            return f"{de_in.id} sent to SG."

        elif tool_name == "vega_de_observe":
            de_in = create_artifact(type="DE_IN", sender="DE",
                                    content=args["message"])
            de_in.id = await self.sequences.next_id("DE", "DE_IN")
            await self.router.route(de_in)
            return f"Observation {de_in.id} sent to SG."

        # EXT tools
        elif tool_name == "vega_ext_submit":
            brq = create_artifact(type="BRQ", sender="EXT",
                                  content=args["results"])
            brq.id = await self.sequences.next_id("EXT", "BRQ")
            await self.router.route(brq)
            return f"{brq.id} submitted to BR."

        elif tool_name == "vega_ext_ask":
            brq = create_artifact(type="BRQ", sender="EXT",
                                  content=args["question"])
            brq.id = await self.sequences.next_id("EXT", "BRQ")
            await self.router.route(brq)
            return f"Question {brq.id} sent to BR."

        # Read-only tools (shared across roles, scoped by visibility)
        elif tool_name in ("vega_status", "vega_agent", "vega_cycles",
                           "vega_history", "vega_thinking", "vega_wiki",
                           "vega_log", "vega_scope",
                           "vega_de_activity", "vega_ext_activity",
                           "vega_de_history", "vega_de_pending",
                           "vega_ext_history", "vega_ext_pending",
                           "vega_roles", "vega_config_notifications"):
            return await self._handle_read_tool(tool_name, args, role)

        # SC-7: Verification and recovery
        elif tool_name == "vega_verify":
            manifest = self._load_latest_snapshot_manifest()
            if not manifest:
                return "No snapshots available."
            mismatches = []
            for path, expected in manifest["content_hashes"].items():
                live = sha256(read_file(path))
                if live != expected:
                    mismatches.append({"file": path, "expected": expected, "live": live})
            return {
                "snapshot_id": manifest["snapshot_id"],
                "timestamp": manifest["timestamp"],
                "status": "MATCH" if not mismatches else "DRIFT",
                "mismatches": mismatches
            }

        elif tool_name == "vega_snapshots":
            snapshots = []
            for d in sorted(os.listdir(SNAPSHOT_LOCAL_DIR), reverse=True):
                m = load_json(f"{SNAPSHOT_LOCAL_DIR}/{d}/manifest.json")
                snapshots.append({
                    "id": m["snapshot_id"], "timestamp": m["timestamp"],
                    "trigger": m["trigger"], "scope_versions": m["scope_versions"]
                })
            return snapshots

        elif tool_name == "vega_restore":
            # Dual 2FA: Admin OP initiates → OTP → OP consents → OTP → execute
            snapshot_id = args["snapshot_id"]
            otp = self.role_manager.initiate_restore(
                role_info["telegram_id"], snapshot_id)
            return (f"Restoration initiated for {snapshot_id}. "
                    f"2FA code sent to your Telegram. "
                    f"After your confirmation, OP will be asked to consent.")

        return f"Unknown tool: {tool_name}"

    async def _handle_request_access(self, args):
        """Lobby tool — unauthenticated role request."""
        # Validate required fields
        for field in ("name", "telegram_id", "role", "project"):
            if field not in args:
                return f"Missing required field: {field}"
        if args["role"] not in ("OP", "DE", "EXT"):
            return f"Invalid role: {args['role']}. Must be OP, DE, or EXT."
        # Notify Admin OP
        admin_telegram = self.role_manager.get_telegram_id("ADMIN_OP")
        if admin_telegram:
            await telegram_bot.send(admin_telegram,
                f"📋 Role request:\n"
                f"Name: {args['name']}\n"
                f"Role: {args['role']}\n"
                f"Project: {args['project']}\n"
                f"Telegram: {args['telegram_id']}\n\n"
                f"Reply APPROVE or use code via MCP")
        return "Request submitted. Admin will review."
```

### 12.4 Telegram Commands by Role

Telegram commands mirror MCP tools but in structured syntax. Role is determined by Telegram chat ID (from roles.json).

**OP:** `/approve`, `/reject`, `/modify`, `/request`, `/backlog`, `/status`, `/agent`, `/history`, `/thinking`, `/wiki`, `/log`, `/cycles`

**Admin OP:** `/sys`, `/resolve`, `/role assign|modify|revoke`, `/roles`, `/model`, `/rotate`, `/pause`, `/resume`, `/config_notify`, plus all OP read commands

**DE:** `/de_respond`, `/de_observe`, `/de_history`, `/de_pending`

**EXT:** `/ext_submit`, `/ext_ask`, `/ext_history`, `/ext_pending`

Unrecognized Telegram chat IDs receive: "Access not configured. Use MCP to request access via vega_request_access()."

### 12.5 External Build (via BR — direct)

EXT interacts with BR directly through their own MCP + Telegram. No OP relay. BR produces BRP → EXT receives notification → EXT responds via `vega_ext_submit()` or `vega_ext_ask()` → orchestrator creates BRQ-EXT artifact, routes through router to BR.

OP has configurable read visibility into EXT↔BR exchanges.

### 12.6 Domain Expert (via SG — direct)

DE interacts with SG directly through their own MCP + Telegram. No OP relay. SG produces DE_OUT → DE receives notification → DE responds via `vega_de_respond()` → orchestrator creates DE_IN artifact, routes through router to SG.

OP has configurable read visibility into DE↔SG exchanges.

### 12.7 Scope Governance Unchanged

Direct DE and EXT access does not bypass scope governance. DE input triggers SG analysis → PROP → OP validates via AUTH. EXT findings flow through BR → SG → OP. The PROP→AUTH cycle is the gate — who initiates the input doesn't change who approves the output.

---

## 13. Main Event Loop

```python
async def main():
    # Initialize components
    config = load_config()
    sequence_manager = SequenceManager()
    instance_manager = InstanceManager()
    wiki_manager = WikiManager()
    artifact_store = ArtifactStore()
    cycle_manager = CycleManager()
    op_backlog = Backlog(OP_BACKLOG_DIR)
    admin_backlog = Backlog(ADMIN_BACKLOG_DIR)
    # ROUTING_TABLE is a module-level constant (§4.1).
    # Router reads it directly; dependencies are injected for I/O operations
    # (archiving, backlog delivery, notifications, cycle management).
    router = Router(store=artifact_store, state_dir=STATE_DIR,
                    op_backlog=op_backlog, admin_backlog=admin_backlog,
                    telegram_bot=telegram_bot, cycles=cycle_manager)
    telegram_bot = TelegramBot()
    executor = AgentExecutor(wiki_manager, sequence_manager,
                             instance_manager, cycle_manager)

    # Start Telegram bot + await readiness (§10.2 readiness barrier)
    asyncio.create_task(telegram_bot.start_polling())
    await telegram_bot.await_readiness()  # Poll until bot is connected

    execution_count = 0

    while True:
      try:
        # 1. Route: check all agent outboxes
        for agent_code in AGENTS:
            outbox_items = artifact_store.get_outbox(agent_code)
            for artifact in outbox_items:
                # Check if this opens or closes a cycle
                cycle_manager.check_cycle_events(artifact)
                # Route
                await router.route(artifact)
                artifact_store.archive(artifact)
                artifact_store.clear_from_outbox(agent_code, artifact)

        # 2. Execute: agents with unprocessed inbox items
        tasks = []
        for agent_code in AGENTS:
            if agent_code == "SYS":
                continue  # SYS on schedule
            inbox = artifact_store.get_inbox(agent_code)
            if inbox.has_unprocessed():
                tasks.append(executor.execute(agent_code))

        if tasks:
            execution_results = await asyncio.gather(*tasks)
            execution_count += len(tasks)
        else:
            execution_results = []

        # 3. SYS scheduling
        if should_run_sys(execution_count):
            await executor.execute_sys()
            wiki_manager.reset_replace_counters()

        # 3a. Wiki replace threshold — after-tick dedup.
        # Each agent execution returns threshold_tripped in its result.
        # Collect across all results; fire SYS once if any tripped.
        # This deduplicates multiple threshold crossings into one SYS run
        # and avoids re-entrancy (SYS doesn't execute inside another agent).
        if any(r and r.get("threshold_tripped") for r in execution_results):
            await executor.execute_sys(audit_request=
                "Wiki replace threshold exceeded. "
                "Review recent replace_section activity.")
            wiki_manager.reset_replace_counters()

        # 4. CORTEX periodic maintenance (if active)
        if CORTEX_ENABLED and should_run_cortex_maintenance(execution_count):
            for agent_code in AGENTS:
                cortex_script.periodic_maintenance(agent_code)

      except Exception as e:
        # Crash-resilient: a bad tick must not kill the orchestrator.
        # Log error, notify OP, continue to next tick.
        log_error("main_loop", e)
        await telegram_bot.send(
            role_manager.get_telegram_id("ADMIN_OP"),
            f"⚠️ Tick failed — {type(e).__name__}: {e}")

      await asyncio.sleep(POLL_INTERVAL)


def should_run_sys(execution_count):
    if SYS_SCHEDULE == "daily":
        return is_daily_time()
    elif SYS_SCHEDULE == "every_N_executions":
        return execution_count % SYS_EXECUTION_THRESHOLD == 0
    return False
```

---

## 14. Initialization

Normal operation from the first moment. No special init mode.

```python
async def initialize_project(initial_input_path: str, import_dir: str = None):
    """One-time project setup."""

    # 0. (Optional) Import pre-orchestrator artifacts (SC-6)
    # For projects with prior history, import existing artifacts
    # with applied_via: import provenance. Run once at initialization —
    # import is not available during normal operation.
    if import_dir and os.path.exists(import_dir):
        for artifact_file in sorted(os.listdir(import_dir)):
            artifact = load_artifact(f"{import_dir}/{artifact_file}")
            artifact.frontmatter["applied_via"] = "import"
            artifact_store.archive(artifact)
        # Set sequence counters after highest imported ID
        sequence_manager.initialize_from_archive(artifact_store)

    # 1. Create directory structure
    create_directory_structure()

    # 2. Extract system prompts from framework
    for agent_code in AGENTS:
        role_def = extract_role_definition(
            "framework/VEGA_Architecture_Framework_v6.md", agent_code
        )
        save_system_prompt(agent_code, role_def)

    # 3. Seed wikis (if seed package provided)
    for agent_code in AGENTS:
        seed_path = f"wiki_seeds/{agent_code}/"
        if os.path.exists(seed_path):
            copy_wiki_seed(seed_path, f"agents/{agent_code}/wiki/")

    # 4. Copy UNIVERSAL
    copy_universal("wiki_seeds/UNIVERSAL/", "universal/")

    # 5. Initialize sequences
    sequence_manager.initialize({
        # Project-specific offsets (e.g., LOINC: SG SCN at 3, decisions at 80)
    })

    # 6. Initialize instance IDs
    for agent_code in AGENTS:
        instance_manager.get_or_create(agent_code)

    # 7. Place initial input in SG inbox ONLY
    # SG establishes baseline, produces PROP → OP validates → SG propagates
    # All other agents receive scope through normal PRO-SCOPE propagation
    initial_artifact = create_artifact(
        type="INIT",
        sender="OP",
        content=read_file(initial_input_path)
    )
    initial_artifact.id = "INIT-OP-001"
    initial_artifact.frontmatter["applied_via"] = "import"  # SC-6: no routing_log at init
    artifact_store.archive(initial_artifact)  # Manual archive (router not running yet)
    place_in_inbox("SG", initial_artifact)

    # 8. Start normal operation
    await telegram_bot.send(
        "🚀 VEGA initialized.\n"
        f"Agents: {', '.join(AGENTS)}\n"
        "Initial input placed in SG inbox.\n"
        "SG will analyze and produce PROP for your review.\n"
        "System is operational."
    )

    await main()
```

---

## 15. Error Handling

### 15.1 API Failures

```python
async def execute_with_retry(agent_code, max_retries=3):
    for attempt in range(max_retries):
        try:
            return await executor.execute(agent_code)
        except anthropic.APIError as e:
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)
            else:
                log_error(agent_code, e)
                await telegram_bot.send(
                    f"⚠️ {agent_code} failed after {max_retries} attempts: {e}"
                )
```

### 15.2 Malformed Agent Output

If response lacks parseable structured blocks:
1. Log raw response to agent's log.md as error
2. Do not route anything
3. Notify OP if 3+ consecutive failures for same agent
4. Leave inbox items unprocessed — next execution retries

### 15.3 Unknown Routing Key

```python
def route(self, artifact):
    key = self._build_key(artifact)
    if key not in ROUTING_TABLE:
        # Governance violation — unknown communication channel
        gov = create_auto_gov(artifact,
            f"Unknown routing key: {key}. Artifact not delivered."
        )
        admin_backlog.add(gov)
        telegram_bot.notify(gov, role="ADMIN_OP")
        return
```

---

## 16. Concurrency and State Safety

### 16.1 Shared State Resources

| Resource | Writers | Protection |
|----------|---------|-----------|
| sequences.json | Any agent (via sequence_manager) | asyncio.Lock |
| instance_ids.json | Admin OP (/rotate command) | asyncio.Lock |
| model_assignments.json | Admin OP (/model command) | asyncio.Lock |
| routing_log.json | Router (every route operation) | asyncio.Lock |
| execution_log.json | Executor (every execution) | asyncio.Lock |
| artifact_index.json | Executor (every artifact produced) | asyncio.Lock |
| wiki_replace_counters.json | WikiManager (every replace_section) | asyncio.Lock |
| config/roles.json | RoleManager (assign/revoke/activate) | asyncio.Lock |
| config/notifications.json | Admin OP (/config_notify) | asyncio.Lock |
| config/recovery.hash | CLI only (deployment + rotation) | No contention (CLI is offline) |
| config/invites/ | RoleManager (create invite, clean expired) | asyncio.Lock per invite file |
| state/role_events.jsonl | RoleManager (append-only) | asyncio.Lock |
| UNIVERSAL/ | SYS only | No contention (SYS runs alone) |
| Agent wiki/ | Only that agent | No contention (one exec per agent at a time) |

### 16.2 Atomic File Operations

```python
def atomic_write(filepath: str, content: str):
    """Write-temp-then-rename. Atomic on POSIX."""
    temp = filepath + ".tmp"
    with open(temp, 'w') as f:
        f.write(content)
    os.rename(temp, filepath)

def atomic_append(filepath: str, content: str):
    """Append with lock (not natively atomic)."""
    # Wrapped in the appropriate asyncio.Lock by caller
    with open(filepath, 'a') as f:
        f.write(content)
```

---

## 17. Monitoring and Observability

### 17.1 Execution Log

`state/execution_log.json` — append-only:

```json
{
  "timestamp": "2026-05-20T14:31:00Z",
  "agent": "SG",
  "instance": "SG-S001",
  "model": "claude-opus-4-7",
  "inbox_items_processed": ["FND-SA-003"],
  "artifacts_produced": ["PROP-SG-001", "SUM-SG-001"],
  "wiki_updates": ["process_rules.md: appended Rule 22"],
  "wiki_entries_included": ["PR-03", "U1", "RC-08", "U10"],
  "thinking_summary": "Analyzed FND-SA-003 against §3.9...",
  "cycle_id": "PROP-PROP-SG-001",
  "api_tokens": {"input": 42000, "output": 8500, "thinking": 3200},
  "duration_seconds": 45
}
```

### 17.2 Routing Log

`state/routing_log.json` — append-only:

```json
{
  "timestamp": "2026-05-20T14:30:05Z",
  "artifact_id": "FND-SA-003",
  "sender": "SA",
  "sender_instance": "SA-S001",
  "sender_model": "claude-sonnet-4-6",
  "type": "FND",
  "routed_to": ["SG"],
  "cycle_id": null,
  "archived": true
}
```

---

## 18. Session Semantics Translation

The framework uses "session" terminology. The orchestrator maps it differently:

| Framework says | Orchestrator means | Notes |
|---------------|-------------------|-------|
| Session start: read wiki + role + UNIVERSAL | Every execution | Structural — fresh API call includes everything |
| Every 3 work cycles, re-ground | Satisfied by design | Every execution is fully grounded |
| ~40% context window | Within a conversation cycle | Messages array grows across turns. Monitored by cycle_manager |
| Session end: update wiki | Cycle close | Final execution in a cycle should produce wiki updates |
| Session handoff | Not needed | Stateless execution. Cycle messages array IS the handoff between turns |
| Instance rotation | Admin OP directive via /rotate | Resets logical identity. New instance inherits wiki |
| log.md: session start | First execution of new instance period | Logged once per instance, not every API call |
| log.md: session end | Instance rotation or major cycle close | Meaningful boundary events only |
| Re-grounding trigger: habit detection | Not applicable | No persistent session = no habit formation within execution. Wiki drift is the only risk, caught by SYS. |

---

## 19. Dependencies

```
python >= 3.11
anthropic >= 0.45.0       # Verify against API docs at deployment
python-telegram-bot >= 21.0
aiofiles >= 23.0
pyyaml >= 6.0
```

---

## 20. Launch Checklist

### Infrastructure
1. [ ] Anthropic API key configured
2. [ ] Telegram bot created, bot token configured
3. [ ] Model strings verified against current API docs
4. [ ] MCP server HTTPS configured (certificate + port)
5. [ ] Recovery key generated and stored offline (shown once at deployment)

### Directory + Data
6. [ ] Directory structure created (`initialize_project()`)
7. [ ] System prompts extracted from framework and verified
8. [ ] Wiki seeds deployed (general or project-specific)
9. [ ] UNIVERSAL deployed (manifesto, cross_agent_rules, case_index)
10. [ ] Scope/project documents placed in `scope/`
11. [ ] Sequence counters initialized (with project offsets if applicable)

### Role Bootstrap
12. [ ] Admin OP bootstrapped via CLI (first role — no 2FA, no prior Admin OP exists)
13. [ ] Admin OP assigns OP role (/role assign, 2FA verified)
14. [ ] OP onboarding: invite → activate → permanent token
15. [ ] DE onboarding (if applicable): invite → activate → permanent token
16. [ ] EXT onboarding (if applicable): invite → activate → permanent token

### Functional Tests
17. [ ] Test: MCP lobby — unauthenticated `vega_request_access()` works
18. [ ] Test: MCP auth — invalid token rejected with 401
19. [ ] Test: MCP role scoping — OP token doesn't see Admin OP tools
20. [ ] Test: 2FA flow — role assignment via MCP with OTP confirmation
21. [ ] Test: 2FA flow — role assignment via Telegram with APPROVE
22. [ ] Test: REQ-OP-001 placed in SG inbox → SG executes → produces PROP
23. [ ] Test: Telegram notification received for PROP (to OP)
24. [ ] Test: /approve via Telegram → AUTH routes to SG → SG produces SUM
25. [ ] Test: Full scope cycle — SG→SE→SG→propagation
26. [ ] Test: GOV from SYS → appears in Admin OP backlog
27. [ ] Test: /resolve via Admin OP closes GOV exchange
28. [ ] Test: DE direct — /de_respond creates DE_IN, routes to SG
29. [ ] Test: EXT direct — /ext_submit creates BRQ, routes to BR
30. [ ] Test: SYS audit pass (/sys command)
31. [ ] Test: Recovery key — `vega recover` resets Admin OP

### Production
32. [ ] Start main loop + MCP server via `initialize_project()`

---

## 21. Mapping to Framework References

| Framework Section | Orchestrator Component |
|-------------------|----------------------|
| §2 Interaction Catalog | `router.py` ROUTING_TABLE (§4) |
| §2.8 Priority Levels | PROP artifact priority field + Telegram emoji |
| §3 Universal Rules | Included in every API call via UNIVERSAL content |
| §5 Role Definitions | `agents/{code}/system_prompt.md` (§5.2) |
| §6 Wiki Schema | `wiki_manager.py` + `agents/{code}/wiki/` (§8) |
| §6 Wiki Protocol: read triggers | Structural — every execution re-reads (§5.4) |
| §6 Wiki Protocol: update triggers | Agent produces WIKI_UPDATE blocks (§5.2) |
| §6 Wiki Protocol: self-lint | SYS audit + agent self-check in prompt |
| §6 Wiki Protocol: log.md | `wiki_manager.append_log()` (§8.2) |
| §9 Interaction Flows | Emergent from routing table + cycle management |
| §11 Instance IDs | `sequence_manager.py` InstanceManager (§9.2) |
| §11 Message Protocol | Artifact YAML frontmatter (§7.1) |
| §11 Session Handoff | Not needed — stateless. Cycles carry context. (§18) |
| §11 Decision Numbering | Role-prefixed sequences in sequence_manager (§9.1) |
| D-ARCH-012 SG↔OP exchange | PROP cycle + Telegram bot (§6, §10) |
| D-ARCH-023 Two-certificate | Cycle management tracks full→build certificate sequence |
| D-ARCH-024 NOTE | NOTE artifact type in routing table + inbox handling |
| D-ARCH-025 AUTH + SUM | Split artifacts: AUTH-OP-NNN + SUM-SG-NNN (§7.2) |
| D-ARCH-026 Four roles | Token-scoped MCP, role-filtered Telegram, §12.1-12.6, §13 |
| D-ARCH-036 Token-scoped MCP | `mcp_server.py` tool filtering by token identity (§12.2) |
| D-ARCH-037 2FA role management | `role_manager.py` OTP generation + Telegram confirmation (§13) |
| D-ARCH-038 Direct DE | DE MCP tools (§12.2), DE Telegram commands (§12.3) |
| D-ARCH-039 Direct EXT | EXT MCP tools (§12.2), EXT Telegram commands (§12.3) |
| D-ARCH-040 Invite-activate | `role_manager.py` invite flow (§13) |
| D-ARCH-031 SYS exclusion | SYS execute checks in `execute_sys()` (§5.6). Strict metadata format in `wiki_manager._filter_exclusions()` (§8.1) |
| D-ARCH-032 Non-blocking | All agents continue work while external responses pending. OP backlog, pending_external.md, pending_build.md |
| D-ARCH-033 REQ artifact | `("OP", "REQ")` routing entry (§4.1). `/request` command (§11). `vega_request()` MCP tool (§12.2) |
| D-ARCH-034 GOV dialogue | GOV exchange cycle (§6.1). `/resolve` command (§11). `vega_resolve()` MCP tool (§12.2) |
| D-ARCH-035 DE non-blocking | DE Q&A cycle (§6.1). `vega_de_respond()` + `vega_de_observe()` MCP tools (§12.2). `pending_external.md` tracking |
| §12 Agent Context Views | Tiered framework loading in `executor._load_framework_view()` (§5.3) |
| §12.1 Framework Summary | ~2k token summary loaded for SA, TA, BR, BTA |
| §12.2 Guardian View (SG, TG) | §1 + §2 + §9 + §10 + addendum (~12k tokens). All non-minimal agents receive addendum. |
| §12.3 SYS View | §1 + §2 + §5 + §10 (~18k tokens) |
| MCP Server | `mcp_server.py` (§12.3). HTTP endpoint, token-scoped tools, role lobby |
| D-ARCH-011 OP + Admin OP | Separable roles in `config/roles.json`. §13 role system. |
| D-ARCH-030 Five-layer hierarchy | Structural (every execution re-reads role + wiki + UNIVERSAL). Admin OP resolves contradictions. |
| §12.4 Minimal View (SE, TE) | System prompt only — no framework context loaded |
| §13.5 Notification config | `config/notifications.json`. `/config_notify` command (§11). `vega_config_notifications()` MCP tool |
| §13.6 Break-glass recovery | `config/recovery.hash`. Server CLI `vega recover`. `role_manager.execute_recovery()` (§10.3) |
| admin_backlog/ | `Backlog(ADMIN_BACKLOG_DIR)` — GOV items for Admin OP (§10.1) |
| state/role_events.jsonl | Append-only audit trail. Written by `role_manager._log_event()`. Read by SYS (§5.9 resp. 6) |
| config/recovery.hash | Break-glass key hash. Written at deployment + on rotation via CLI |
| CORTEX add-on | `cortex_script.py` integration point in executor (§5.1) |
