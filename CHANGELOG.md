# Changelog

## v0.3.0 — June 2026

### Architecture Framework v6

**Multi-role access system (D-ARCH-036 through D-ARCH-040):**
- **Four external roles:** OP (scope via SG), Admin OP (governance via SYS + role management), DE (domain expertise directly with SG), EXT (build interaction directly with BR). Each role has scoped MCP token + Telegram.
- **No OP relay:** DE and EXT interact directly with their agents. OP has configurable read visibility into DE and EXT channels. Scope governance unchanged — all scope changes still flow through PROP→AUTH.
- **OP and Admin OP separable (D-ARCH-026 rewritten):** Same person can hold both. When separated, scope authority and system authority are cleanly divided.

**Spec corrections from live deployment (SC-1 through SC-7):**
- **OP scope document access:** `vega_scope` tool for all 4 roles (SC-3). OP, Admin OP, DE, EXT can read live scope docs directly.
- **DE naming convention:** per-issuer sequential (not global). S14 cross-references H2.
- **§11 title:** "Implementation Notes" (not "for Claude Code").
- **/exchange removed from §13.1:** exchange via vega_exchange (MCP) or freeform text (Telegram, disabled by default via TELEGRAM_EXCHANGE_ENABLED).
- **Token-scoped MCP access (D-ARCH-036):** One endpoint, tools filtered by token identity. Unauthenticated lobby for role requests.
- **2FA for role management (D-ARCH-037):** OTP sent to Admin OP's Telegram. Confirm via Telegram (APPROVE) or MCP (OTP code).
- **Invite-activate-2FA onboarding (D-ARCH-040):** Admin OP approves → invite code to assignee's Telegram → activate via MCP with OTP → permanent token returned only in MCP.
- **New §13 Role-Based Access System:** Roles, authentication, onboarding, 2FA, notification configuration, break-glass recovery, Telegram ID verification, scope governance invariant.
- **Break-glass recovery (§13.6):** Recovery key generated at deployment, stored hashed, server CLI only. Revokes Admin OP, generates new invite, triggers SYS audit.
- **SYS gains role event audit (§5.9 responsibility 6):** Reads role_events.jsonl. Flags suspicious patterns.
- **GOV→Admin OP:** All SYS governance outputs go to Admin OP, not OP. Separate admin_backlog queue.
- **INIT type formalized:** Added to §1 type catalog and SG inputs table.
- **ISSUER list updated:** Now includes SYS and DE.
- **Session Handoff Protocol:** Marked as legacy (stateless model eliminates handoff).

### Orchestrator Spec v5

**Multi-role implementation:**
- **RoleManager class (§10.4):** Token hashing/verification, OTP generation, invite/activation lifecycle, revocation, modification, break-glass recovery, role_events.jsonl audit trail with documented schema.
- **MCPServer class (§12.3):** aiohttp HTTP endpoint, bearer token authentication, _PENDING pseudo-role for activation, role-filtered tool dispatch, 21+ tools across 5 role tiers.
- **Role-dispatching Telegram handlers (§10.2):** handle_message dispatches by chat_id → role. Full handlers for OP, Admin OP, DE, EXT with all commands specified.
- **Dual backlogs (§10.1):** op_backlog (PROP for OP) + admin_backlog (GOV for Admin OP). Generic Backlog class. Resolve handles items from pending or in_progress.
- **GOV routes to ADMIN_OP:** Routing table, ADMIN_BOUND_TYPES, execute_sys, auto-GOV all target admin_backlog.
- **CycleManager complete:** 8 new methods defined (close_by_artifact, get_active_by_artifact, get_cycle_by_participants, get_active_by_type, get_activity_summary, get_pending_for_role, get_history_for_role, check_context_usage).
- **apply_universal_update:** Now handles replace_section with diff logging.
- **read_universal(agent_code):** Exclusion filtering per D-ARCH-031 at every call site.
- **config/ directory:** roles.json, recovery.hash, notifications.json, invites/.
- **state/role_events.jsonl:** Append-only audit trail with 9-field schema.
- **Concurrency table:** 5 new state files with protection.
- **32-item launch checklist:** Infrastructure, directory, role bootstrap, functional tests, production.
- **Framework filename:** Updated to v6 throughout.

**Spec corrections from live deployment (SC-1 through SC-7):**
- **SC-1: Read-tool handler contract (§12.3).** 20-row contract table defining data source, return content, and role access for every read tool. Closes the class of bugs where implementer wires to wrong source.
- **SC-2: vega_history disambiguation.** Returns artifact body (primary, from archive) + routing metadata (secondary). OP read-path guarantee documented.
- **SC-3: vega_scope tool.** OP, Admin OP, DE, EXT can read live scope documents. Added to ROLE_TOOLS, contract table, dispatch.
- **SC-4: Thinking sidecar.** `{id}.thinking.md` written at archive time. Immutable. `vega_thinking` reads sidecar first, execution_log fallback.
- **SC-5: Reference resolution + scope loading.** Referenced archived artifacts resolved and injected into agent grounding with dynamic budget. Scope loading budget-aware with spec/management classification.
- **SC-6: Bootstrap import provenance.** `applied_via: import` for pre-orchestrator and INIT artifacts. SYS provenance audit. Emergency import via Admin OP CLI triggers automatic GOV.
- **SC-7: Validated-state snapshots (§7.5).** Snapshot on approve. Manifest with SHA-256 hashes. `vega_verify`, `vega_snapshots`, `vega_restore` (dual 2FA). SYS drift detection.
- **§10.3 renumbered** (was §10.4). RoleManager includes `initiate_restore` for dual-2FA scope restoration.
- **handle_read_command** defined in Telegram bot — 14 read commands dispatched.
- **Full MCP dispatch:** vega_reject, vega_modify, vega_pause/resume/rotate/model, vega_scope, vega_verify/snapshots/restore all wired.
- **TELEGRAM_EXCHANGE_ENABLED = False** — freeform exchange disabled by default, MCP is primary exchange channel.
- **SNAPSHOT config** in §3.1. SYS reads `snapshots/` for automated drift detection.

### CORTEX Addon v0.2 BETA
- **§2.1 Corpus Types:** CORTEX now indexes agent wikis (per-agent, file-level) and scope documents (shared, section-level via `##` headings).
- **§3.2 Scope navigation:** `cortex_script.navigate_scope()` for section-level relevance ranking within agent context budget.
- **§18.3 Scope indexing:** Triggers on initialization, post-SE-application, post-VAL. Decoupled from snapshot timing. Cold-start with `validation: tentative`.
- **Appendix A:** `index_scope()` and `navigate_scope()` methods + 6 helper method signatures. Three concept-extraction paths documented with reconciliation note.

## v0.2.0 — May 2026

### Architecture Framework v5
- **Non-blocking principle (D-ARCH-032):** No agent blocks on external responses (OP, DE, EXT). Agents log pending items and continue unaffected work.
- **REQ artifact type (D-ARCH-033):** OP submits ad-hoc scope work to SG via REQ-OP-NNN. Triaged identically to FND/DEV/ESC.
- **SYS↔OP dialogue (D-ARCH-034):** GOV exchange cycle with multi-turn dialogue. Closes on /resolve. SYS non-blocking while GOV pending.
- **DE non-blocking (D-ARCH-035):** SG tracks pending DE items in `pending_external.md` (working file). Continues other work. DE_IN feeds into next cycle.
- **BR pending tracking:** Rules 11-12 for partial response tracking + `pending_build.md`.
- **VAL certificate field:** `full | build` on TG VAL artifacts. TCN cycle closes on `build`.
- **Agent Context Views (§12):** Tiered framework loading — guardian (~12k), SYS (~18k), summary (~2k), minimal (0). Framework Summary documented.
- **DE naming normalized:** `DE_OUT` / `DE_IN` everywhere (was inconsistent).
- **Init sequence updated:** SG only, normal operation from start.
- **Project-specific content removed:** Framework is now fully project-agnostic.
- **Dangling traces fixed:** All legacy "Session N Rule M" references resolved.

### Orchestrator Spec v4
- **MCP Server (§12.1-12.2):** Dual OP interface — Telegram (push + quick) + MCP (substantive via Claude session). HTTP endpoint, bearer token auth. 21 tools.
- **Tiered framework context (§5.3):** `_load_framework_view(agent_code)` replaces full framework loading.
- **10 conversation cycle types:** Added DE Q&A and GOV exchange.
- **Exchange turns are cycle-internal (§6.4):** Not standalone artifacts. Cycle archive captures full dialogue.
- **REQ + AUTH + BRQ routing entries:** All orchestrator-created artifacts now route through the table. No direct-placement exceptions.
- **API-resilient thinking:** `get_thinking_config(model)` adapts to API version.
- **Crash-resilient main loop:** try/except per tick, log + notify, continue.
- **Telegram resilience:** Notify failure ≠ routing failure. Readiness barrier.
- **Auto-GOV ID:** Timestamp-based with AUTO prefix. Reissue references original.
- **Exclusion metadata format:** Strict field order documented.
- **/request, /resolve commands:** New OP commands for REQ and GOV cycles.
- **OP guidance note:** Not all work goes through SG.

### Orchestrator implementation (`orchestrator/`)
Reference Python implementation of Spec v4:
- ~3,000 LOC across 14 modules; 110 pytest tests anchored to spec invariants
- Spec §16 async-locked shared state; AUTH/SUM split with immutable archival; Spec §15.2 malformed handling for all agents
- Telegram bot (Spec §10–11, 25 commands) + MCP server (Spec §12, 21 tools); both resilient to transient API failures
- System prompt auto-generation via framework section extraction (Spec §14 step 2)
- CORTEX placeholder so toggling the flag doesn't crash on import

### Claude Code plugin (`.claude-plugin/`, `skills/`, `templates/`)
- Plugin manifest at repo root — cloning into `~/.claude/plugins/` makes this repo a working Claude Code plugin
- `/vega:vega-init` skill: 13-step Q&A scaffold of a per-project VEGA deployment
- Templates use upstream `docs/` and `wiki_seeds/` as the single source of truth — no duplication

### CORTEX Addon v0.2 BETA
- Framework reference updated to v5.0
- No architectural changes

## v0.1.0 — May 2026 (Initial Release)

### Architecture Framework v4
- 9 agents across scope, test, and build lanes + governance oversight
- 31 architectural decisions (D-ARCH-001 through D-ARCH-031)
- Complete interaction catalog with exhaustive routing
- Wiki protocol: read triggers, update triggers, self-lint, failure-type instructions
- Five-layer authority hierarchy
- AUTH + SUM split: two immutable artifacts per OP exchange
- NOTE document type, two-certificate test model validation
- 19 universal case references (U-RC-01 through U-RC-19)

### Manifesto v4
- 8 principles, three-layer knowledge architecture
- Intellectual foundations: Simon, Cowan, Brooks, Karpathy, Bush

### Orchestrator Spec v3
- Stateless API execution model
- Conversation cycles (8 types)
- Prompt caching, extended thinking
- SYS exclusion filtering, wiki diff logging
- Telegram OP interface with 25-command library
- Concurrency safety, session semantics translation

### CORTEX Addon v0.2 BETA
- Two-layer architecture: script + agent
- Four navigation modes, mode composition
- Retroactive concept assignment, decay with floor
- Per-agent orchestrator-controlled activation

### Wiki Seeds
- 10 agent directories + UNIVERSAL
- Case index with 19 generic trace references
