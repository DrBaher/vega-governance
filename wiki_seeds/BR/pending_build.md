# Pending Build Requests

Working file — only OUTSTANDING request items. When EXT responds, cross-
check the response against each request item. Mark delivered items as
resolved (remove from here, append a closure note to log.md:
`BUILD_RESOLVED | <item-id> | <answer summary>`). Carry forward
undelivered items in the next BRP with explicit "still outstanding"
framing.

Per Framework v5 §5.7 rules 11-12 (D-ARCH-032) — partial responses are
the norm; build answers some questions and submits some results, not
all. Partial silence is not compliance or refusal. Follow up until every
item has an explicit response or OP directs otherwise.

## Schema (one entry per outstanding item)

```
### <REQ-ID, e.g. Q-001 or RESULTS-feature-X>
opened: <ISO 8601>
brp_ref: <BRP-BR-NNN that carried this item>
type: question | result-request | directive
summary: <one-line description>
expected: <what a complete answer looks like — exact format if relevant>
state: outstanding | partial (<what's missing>)
```

<!-- Entries below this line -->
