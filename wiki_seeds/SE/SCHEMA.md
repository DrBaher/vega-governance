# Wiki Schema

## Purpose
Persistent knowledge store for this agent across sessions. Read at session start, update when something matters.

## Organization
- **index.md** — Entry point. Current state, pending items, links to other pages.
- **process_rules.md** — Rules learned from mistakes. Each rule traces to the incident that produced it.
- **reasoning_corrections.md** — Cases where this agent gave wrong assessments. Documented to avoid repeating.
- **Role-specific pages** — Accumulated domain and operational knowledge.

## Principles
1. One source of truth per topic. Point to specs, don't duplicate.
2. Every rule has a trace to a failure.
3. Update when something matters, not every conversation.
4. Short entries, dense content.
5. Never delete history — correct, supersede, or archive.
6. **After context compression:** treat own summaries as unverified. Re-read source files. (U7)

## Update Protocol
1. Read index.md + SCHEMA.md at every session start.
2. Update as things settle, not only at end.
3. Preserve prior statements when correcting (mark superseded).
4. Date-stamp major additions.
5. Spec changes go through SCN, not wiki edits.
