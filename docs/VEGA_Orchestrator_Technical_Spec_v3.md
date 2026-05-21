# V-model Enabled Governance Architecture (VEGA)
## Orchestrator — Technical Specification v3

**Author:** Francisco
**Date:** May 2026
**For:** Claude Code implementation
**References:** VEGA Architecture Framework v4.0 (all §-references point to this document)

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
6. **Two human decision channels.** OP ↔ SG (PROP → exchange → AUTH) and OP ← SYS (GOV). OP has operational relay duties at launch for Domain Expert and External Build (not decision-making). Relay duties are automation candidates.
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
│  │              OP Backlog Queue                      │ │
│  │  (PROP items + GOV items, with priority)           │ │
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
│   ├── op_backlog.py            # OP queue management
│   ├── telegram_bot.py          # Telegram interface for OP
│   ├── config.py                # Configuration
│   ├── models.py                # Data models (Artifact, Agent, Cycle, etc.)
│   ├── sequence_manager.py      # Document ID + decision sequence counters
│   ├── state_manager.py         # Atomic state operations with locking
│   └── cortex_script.py         # CORTEX computation (when CORTEX is active)
│
├── framework/
│   ├── VEGA_Architecture_Framework_v4.md
│   ├── VEGA_Manifesto_v4.md
│   └── VEGA_LOINC_Project_Addendum.md   # (project-specific)
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
├── op_backlog/
│   ├── pending/
│   ├── in_progress/
│   └── resolved/
│
└── state/
    ├── sequences.json
    ├── instance_ids.json
    ├── model_assignments.json
    ├── routing_log.json
    ├── execution_log.json
    ├── artifact_index.json       # Artifact ID → execution ID lookup (for /thinking)
    └── wiki_replace_counters.json # Per-agent replace_section counts since last SYS run
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
THINKING_BUDGET_TOKENS = 10000  # Per execution

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
STATE_DIR = f"{BASE_DIR}/state"

# Telegram
TELEGRAM_BOT_TOKEN = "..."
TELEGRAM_OP_CHAT_ID = "..."

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

# External Build
EXT_BUILD_ENABLED = True
```

### 3.2 Agent Access Permissions

| Agent | Reads | Writes |
|-------|-------|--------|
| SG | own wiki, universal/, scope/, inbox/ | own wiki, outbox/ |
| SA | own wiki, universal/, scope/, inbox/ | own wiki, outbox/ |
| SE | own wiki, universal/, scope/, inbox/ | own wiki, outbox/, scope/ (only via DOC) |
| TG | own wiki, universal/, scope/, inbox/, test_models/full/ | own wiki, outbox/, test_models/full/ |
| TA | own wiki, universal/, scope/, inbox/, test_models/full/ | own wiki, outbox/ |
| TE | own wiki, universal/, inbox/, test_models/full/, test_models/build/ | own wiki, outbox/, test_models/full/, test_models/build/ |
| BR | own wiki, universal/, scope/, inbox/, test_models/build/ | own wiki, outbox/ |
| BTA | own wiki, universal/, inbox/, test_models/full/ | own wiki, outbox/ |
| SYS | ALL wikis (read), universal/, artifacts/archive/, ALL log.md, execution_log.json | own wiki, universal/ (write), outbox/ |

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

    # External Build (§2.5) — special handling
    ("BR", "BRP"):          [{"to": "EXT"}],

    # Domain Expert (§2.6) — special handling
    ("SG", "DE_OUT"):       [{"to": "DE"}],

    # System Auditor (§2.7)
    ("SYS", "GOV"):         [{"to": "OP"}],
}
```

### 4.2 OP-Bound Routes (non-blocking)

```python
OP_BOUND_TYPES = {
    ("SG", "PROP"): {
        "queue": "op_backlog/pending",
        "notify": True,
        "priority_field": True,
        "exchange_mode": True,
        "exchange_partner": "SG"
    },
    ("SYS", "GOV"): {
        "queue": "op_backlog/pending",
        "notify": True,
        "priority_field": False,
        "exchange_mode": False,
        "exchange_partner": None
    },
}
```

### 4.3 Routing Rules

1. When an agent produces an artifact in its outbox, the router picks it up.
2. Router reads `type`, `sender`, and (for response types) `ref_type` from the artifact metadata.
3. For each recipient: copies artifact to `agents/{recipient}/inbox/`.
4. Archives to `artifacts/archive/`. **Archive is immutable — never modified after write.**
5. Appends to `state/routing_log.json`.
6. For OP-bound types: copies to `op_backlog/pending/`. Sends Telegram notification.
7. For EXT/DE-bound types: see §12 External Interfaces.
8. **Unknown routing key** (type+sender not in table): log as governance violation, auto-create GOV-SYS-NNN, route GOV to OP backlog. Do not deliver artifact.

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
    universal_content = wiki_manager.read_universal()
    scope_docs = load_scope_docs(agent_code)
    inbox_items = inbox.get_unprocessed()

    if cycle:
        # Continue existing cycle — pass full messages array
        messages = cycle.build_continuation(inbox_items)
    else:
        # Fresh execution — single user message
        messages = build_fresh_messages(
            wiki=wiki_content,
            universal=universal_content,
            inbox=inbox_items,
            scope=scope_docs,
            instance_id=get_instance_id(agent_code)
        )

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
        thinking={"type": "enabled", "budget_tokens": THINKING_BUDGET_TOKENS},
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
    wiki_manager.apply_updates(agent_code, wiki_updates)

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

Each agent's `system_prompt.md` contains:

```markdown
# Role: [Agent Name] ([Code])
Instance: [ROLE]-S[NNN]

## Preamble
[Standard preamble from framework]

## Your Role
[Full role definition from Framework §5.X]

## File Locations
- Your wiki: Read and update per the wiki protocol.
- UNIVERSAL: Contains manifesto, cross_agent_rules, case_index.
- Scope documents: [list, if agent has access]

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

    inbox_content = format_sys_inbox(
        artifacts=all_artifacts,
        wikis=all_wikis,
        logs=all_logs,
        execution_log=execution_log,
        framework=load_framework(),
        audit_request=audit_request
    )

    # Execute as normal agent
    response = await anthropic_client.messages.create(
        model=get_model("SYS"),
        max_tokens=MAX_TOKENS,
        thinking={"type": "enabled", "budget_tokens": THINKING_BUDGET_TOKENS},
        system=load_system_prompt("SYS"),
        messages=[{"role": "user", "content": inbox_content}]
    )

    thinking_blocks, artifacts, wiki_updates, log_entries = parse_response(
        response, "SYS"
    )

    # SYS can write to UNIVERSAL
    universal_updates = [u for u in wiki_updates if u.target == "universal"]
    own_updates = [u for u in wiki_updates if u.target == "own"]
    
    for update in universal_updates:
        wiki_manager.apply_universal_update(update)
    for update in own_updates:
        wiki_manager.apply_updates("SYS", [update])

    # GOV artifacts go to OP backlog
    for artifact in artifacts:
        if artifact.type == "GOV":
            op_backlog.add(artifact)
            await telegram_bot.notify(artifact)

    log_execution("SYS", get_model("SYS"), [], artifacts,
                  wiki_updates, thinking_blocks, None)
```

---

## 6. Conversation Cycles

Bounded multi-turn interactions maintain a messages array across executions. The array provides conversational coherence. It is discarded when the cycle closes.

### 6.1 Cycle Definitions

| Cycle type | Participants | Opens on | Closes on |
|-----------|-------------|----------|-----------|
| SCN application | SG ↔ SE | SCN-SG-NNN issued | VAL-SG-NNN issued |
| TCN application | TG ↔ TE | TCN-TG-NNN issued | VAL-TG-NNN with certificate=second |
| PROP exchange | SG ↔ OP | PROP-SG-NNN issued | AUTH-OP-NNN issued |
| Triage | TG (internal) | TFR-BTA-NNN received | TRI/ESC/TCN produced |
| Build scope Q&A | BR ↔ EXT | PRO-SCOPE arrives at BR | VR-BTA-NNN for this scope version, or next PRO-SCOPE |
| Build test Q&A | BR ↔ EXT | PRO-TEST-BUILD arrives at BR | VR-BTA-NNN for this test version, or next PRO-TEST-BUILD |
| Build results | BR ↔ EXT | BRQ (results) received from EXT | VR-BTA-NNN relayed to EXT |
| Build remediation | BR ↔ EXT | TRI relayed to EXT | New results submitted by EXT |

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

    def check_cycle_events(self, artifact):
        """Check if an artifact opens or closes a cycle."""
        # TCN cycle: only closes on second certificate
        if (artifact.type == "VAL" and artifact.sender == "TG" 
                and hasattr(artifact, 'certificate')):
            if artifact.certificate == "second":
                cycle = self.get_cycle_by_participants("TG", "TE")
                if cycle:
                    self.close_cycle(cycle)
            # first certificate doesn't close — TE still generates build version
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

### 6.4 Cycle Context Monitoring

Within a long cycle (e.g., BR↔EXT scope Q&A with many turns), the messages array grows. The orchestrator monitors:

```python
def check_context_usage(self, cycle):
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
  - PRO-SCOPE:v7.2
priority: P2
ref_type: null
status: unprocessed
---

# FND-SA-003: Cross-reference gap in §3.9

[artifact content]
```

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
def archive(artifact: Artifact):
    dest = f"{ARTIFACTS_DIR}/{artifact.id}.md"
    atomic_write(dest, artifact.to_markdown())
    # Archive is IMMUTABLE — no modification after write
```

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
                self._check_replace_threshold(agent_code)

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
        if update.action in ("append", "new_entry"):
            atomic_append(filepath, f"\n\n{update.content}")
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

### 10.1 OP Backlog Queue

```python
class OPBacklog:
    def add(self, artifact: Artifact):
        dest = f"{OP_BACKLOG_DIR}/pending/{artifact.id}.md"
        atomic_write(dest, artifact.to_markdown())

    def start_exchange(self, artifact_id: str):
        move(f"pending/{artifact_id}.md", f"in_progress/{artifact_id}.md")

    def resolve(self, artifact_id: str, auth: Artifact):
        move(f"in_progress/{artifact_id}.md", f"resolved/{artifact_id}.md")
        if auth.type == "AUTH":
            place_in_inbox("SG", auth)
```

### 10.2 Telegram Bot

```python
class TelegramBot:
    async def notify(self, artifact: Artifact):
        priority_emoji = {"P0": "🔴", "P1": "🟠", "P2": "🟡", "P3": "🟢"}
        emoji = priority_emoji.get(artifact.priority, "🟡")

        summary = self._extract_summary(artifact)

        # Include thinking highlights if available
        thinking = self._get_thinking_summary(artifact)
        thinking_section = f"\n💭 *SG reasoning:* {thinking}\n" if thinking else ""

        msg = (
            f"{emoji} **{artifact.id}** ({artifact.priority})\n"
            f"From: {artifact.sender} ({artifact.sender_model})\n"
            f"{summary}\n"
            f"{thinking_section}\n"
            f"Reply to discuss, or:\n"
            f"/approve {artifact.id}\n"
            f"/reject {artifact.id} [reason]\n"
            f"/modify {artifact.id} [instructions]"
        )
        await self.send(msg)

    async def handle_op_response(self, message: str):
        """Handle OP's response via Telegram."""
        if message.startswith("/approve"):
            artifact_id = self._extract_id(message)
            auth = create_auth(artifact_id, "approve")
            op_backlog.resolve(artifact_id, auth)
            # Close the PROP cycle
            cycle_manager.close_by_artifact(artifact_id)
            await self.send(f"✅ AUTH issued for {artifact_id}. SG will write SUM.")

        elif message.startswith("/reject"):
            artifact_id, reason = self._extract_id_and_text(message)
            auth = create_auth(artifact_id, "reject", reason=reason)
            op_backlog.resolve(artifact_id, auth)
            cycle_manager.close_by_artifact(artifact_id)
            await self.send(f"❌ AUTH issued for {artifact_id}")

        elif message.startswith("/modify"):
            artifact_id, instructions = self._extract_id_and_text(message)
            auth = create_auth(artifact_id, "modify", modifications=instructions)
            op_backlog.resolve(artifact_id, auth)
            cycle_manager.close_by_artifact(artifact_id)
            await self.send(f"✏️ AUTH issued with modifications for {artifact_id}")

        else:
            # Multi-turn exchange: route to SG within the PROP cycle
            current = op_backlog.get_in_progress()
            if current:
                exchange_msg = create_exchange_message(current.id, message, "OP")
                place_in_inbox("SG", exchange_msg)
                await self.send("↩️ Forwarded to SG. Response incoming...")
            else:
                await self.send("No active exchange. Use /pending to see queue.")

    async def handle_sg_exchange_response(self, artifact: Artifact):
        """Forward SG response during OP exchange to Telegram."""
        thinking = self._get_thinking_summary(artifact)
        thinking_section = f"\n💭 *SG thinking:* {thinking}\n" if thinking else ""

        msg = (
            f"💬 **SG response** (re: {artifact.references[0]})\n\n"
            f"{artifact.content_summary}\n"
            f"{thinking_section}\n"
            f"Reply to continue, or /approve /reject /modify"
        )
        await self.send(msg)
```

---

## 11. OP Command Library

```python
COMMANDS = {
    # STATUS
    "/help":     "Show all available commands",
    "/status":   "System overview: agent states, inbox counts, pending OP items",
    "/backlog":  "List pending PROP and GOV items with priorities",
    "/agent":    "Usage: /agent [code] — Agent details: last execution, wiki size, inbox",
    "/history":  "Usage: /history [artifact-id] — Full routing history of an artifact",
    "/pending":  "List active PROP exchanges awaiting response",

    # AUDIT & GOVERNANCE
    "/sys":      "Usage: /sys [instruction] — Trigger SYS audit (immediate). "
                 "No instruction = general audit. With instruction = targeted.",
    "/wiki":     "Usage: /wiki [code] — Agent wiki summary: pages, sizes, last updated",
    "/log":      "Usage: /log [code] [N] — Last N log entries for agent",
    "/thinking": "Usage: /thinking [artifact-id] — Show thinking blocks for an artifact's execution",

    # AGENT CONTROL
    "/pause":    "Usage: /pause [code] — Pause agent (inbox accumulates)",
    "/resume":   "Usage: /resume [code] — Resume agent",
    "/rotate":   "Usage: /rotate [code] — Rotate instance ID (new logical session)",
    "/retry":    "Usage: /retry [code] — Re-execute agent's last failed run",
    "/model":    "Usage: /model [code] [model-string] — Change agent's model",
    "/models":   "Show current model assignment per agent",

    # EXCHANGES
    "/approve":  "Usage: /approve [artifact-id] — Approve PROP/GOV",
    "/reject":   "Usage: /reject [artifact-id] [reason]",
    "/modify":   "Usage: /modify [artifact-id] [instructions]",

    # EXTERNAL INTERFACES
    "/build":    "Usage: /build [message] — Forward to BR inbox as BRQ-EXT",
    "/expert":   "Usage: /expert [message] — Forward to SG inbox as DE_IN",

    # REFERENCE
    "/agents":   "List all agents with roles (one-line each)",
    "/routing":  "Usage: /routing [type] — Show where artifact type routes to",
    "/decisions":"Usage: /decisions [prefix] — Recent decisions (D-, TD-, GD-)",
    "/framework":"Usage: /framework [section] — Quick-reference a framework section",
    "/cycles":   "List active conversation cycles",
}
```

---

## 12. External Interfaces

### 12.1 External Build (via BR)

At launch: OP relay. BR produces BRP → OP forwards. Build responds → OP uses `/build [message]` → orchestrator creates BRQ-EXT artifact in BR inbox.

Automation target: Telegram channel for build team, or file drop directory watched by orchestrator.

### 12.2 Domain Expert (via SG)

At launch: OP relay. SG produces DE_OUT → OP forwards. Expert responds → OP uses `/expert [message]` → orchestrator creates DE_IN artifact in SG inbox.

### 12.3 Relay Is Not Decision-Making

Per D-ARCH-026: OP has two decision channels (SG, SYS). Relay duties at launch are operational logistics — OP doesn't evaluate, modify, or approve relayed content. When relay is automated, the decision channels remain unchanged.

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
    op_backlog = OPBacklog()
    router = Router(ROUTING_TABLE, OP_BOUND_TYPES)
    telegram_bot = TelegramBot()
    executor = AgentExecutor(wiki_manager, sequence_manager,
                             instance_manager, cycle_manager)

    # Start Telegram bot
    asyncio.create_task(telegram_bot.start_polling())

    execution_count = 0

    while True:
        # 1. Route: check all agent outboxes
        for agent_code in AGENTS:
            outbox_items = artifact_store.get_outbox(agent_code)
            for artifact in outbox_items:
                # Check if this opens or closes a cycle
                cycle_manager.check_cycle_events(artifact)
                # Route
                router.route(artifact)
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
            await asyncio.gather(*tasks)
            execution_count += len(tasks)

        # 3. SYS scheduling
        if should_run_sys(execution_count):
            await executor.execute_sys()
            wiki_manager.reset_replace_counters()

        # 3a. SYS triggered by wiki replace threshold
        if wiki_manager.any_threshold_exceeded():
            await executor.execute_sys()
            wiki_manager.reset_replace_counters()

        # 4. Check resolved OP items
        for item in op_backlog.get_newly_resolved():
            router.route(item.auth)

        # 5. CORTEX periodic maintenance (if active)
        if CORTEX_ENABLED and should_run_cortex_maintenance(execution_count):
            for agent_code in AGENTS:
                cortex_script.periodic_maintenance(agent_code)

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
async def initialize_project(initial_input_path: str):
    """One-time project setup."""

    # 1. Create directory structure
    create_directory_structure()

    # 2. Extract system prompts from framework
    for agent_code in AGENTS:
        role_def = extract_role_definition(
            "framework/VEGA_Architecture_Framework_v4.md", agent_code
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
        op_backlog.add(gov)
        telegram_bot.notify(gov)
        return
```

---

## 16. Concurrency and State Safety

### 16.1 Shared State Resources

| Resource | Writers | Protection |
|----------|---------|-----------|
| sequences.json | Any agent (via sequence_manager) | asyncio.Lock |
| instance_ids.json | OP (/rotate command) | asyncio.Lock |
| model_assignments.json | OP (/model command) | asyncio.Lock |
| routing_log.json | Router (every route operation) | asyncio.Lock |
| execution_log.json | Executor (every execution) | asyncio.Lock |
| artifact_index.json | Executor (every artifact produced) | asyncio.Lock |
| wiki_replace_counters.json | WikiManager (every replace_section) | asyncio.Lock |
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
| Instance rotation | OP directive via /rotate | Resets logical identity. New instance inherits wiki |
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

1. [ ] Anthropic API key configured
2. [ ] Telegram bot created, token + OP chat ID configured
3. [ ] Model strings verified against current API docs
4. [ ] Directory structure created (`initialize_project()`)
5. [ ] System prompts extracted from framework and verified
6. [ ] Wiki seeds deployed (general or project-specific)
7. [ ] UNIVERSAL deployed (manifesto, cross_agent_rules, case_index)
8. [ ] Scope/project documents placed in `scope/`
9. [ ] Sequence counters initialized (with project offsets if applicable)
10. [ ] Test: manual artifact placed in SG inbox → SG executes → produces PROP
11. [ ] Test: Telegram notification received for PROP
12. [ ] Test: /approve via Telegram → AUTH routes to SG → SG produces SUM
13. [ ] Test: Full cycle — SG→SE→SG→propagation
14. [ ] Test: SYS audit pass (/sys command)
15. [ ] Production: Start main loop via `initialize_project()`

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
| D-ARCH-026 OP channels | Two decision (SG, SYS) + relay duties (§12.3) |
| D-ARCH-031 SYS exclusion | SYS execute checks in `execute_sys()` (§5.5) |
| CORTEX add-on | `cortex_script.py` integration point in executor (§5.1) |
