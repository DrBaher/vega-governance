# Log Template

Copy this to each agent's wiki as `log.md`. Append-only.

Format: `## [YYYY-MM-DD] INSTANCE-ID | event type | details`

Event types:
- `Session start` — what was read at initialization
- `Received [DOC-ID]` — artifact received from another agent
- `Produced [DOC-ID]` — artifact produced
- `Consulted [entries]` — wiki entries read before a decision
- `Wiki update` — what page was updated and why
- `Re-grounding` — periodic role re-read
- `Self-lint` — wiki maintenance performed
- `Session end` — pending items, state summary
