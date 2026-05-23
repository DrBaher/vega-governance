# Changelog

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
