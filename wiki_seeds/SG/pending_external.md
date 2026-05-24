# Pending External Questions

Working file — only OPEN items. When DE_IN arrives, remove the matching
entry from here and append a closure note to log.md (`DE_RESOLVED |
DE_OUT-SG-NNN | <answer summary>`).

Per Framework v5 §5.1 rule 8 (D-ARCH-035) — Domain Expert requests are
non-blocking. SG continues processing other inbox items not affected by
the outstanding question. DE responses feed forward — they do not
retroactively reopen completed work.

## Schema (one entry per outstanding DE question)

```
### DE_OUT-SG-NNN
opened: <ISO 8601>
question: <one-line summary>
what depends on the answer: <free text — which artifacts, decisions, or
  downstream work are gated on this answer; if nothing, say "feed-forward
  only">
references: <comma-separated artifact ids that motivated the question>
```

<!-- Entries below this line -->
