# Reasoning Corrections — Scope Editor
## Seed (generalized from project experience)

## RC-01: Missed Table of Contents entry during rename propagation
**What I said:** Applied all renames per SCN.
**What was actually true:** A structured ToC row still had the old field name.
**Lesson:** Rename propagation must include ToC entries, not just body text.

## RC-02: Applied change without verifying the current document state
**What I said:** Applied replacement text per SCN.
**What was actually true:** The "current text" cited in the SCN had been modified in a prior pass. The replacement was applied to already-modified text.
**Lesson:** Always re-verify document state before each SCN item application.

## RC-03: Bridge state domain code lookup verified at wrong abstraction level
**What I said:** Confirmed a domain code was correct for a test case.
**What was actually true:** The query terms wouldn't actually retrieve that code via the lookup mechanism.
**Lesson:** When verifying domain code lookups, trace the ACTUAL query path, not just the conceptual correctness.

## RC-04: Output specification field count was wrong
**What I said:** Specification output had 6 fields.
**What was actually true:** After SCN application, it had 8 fields (2 new fields added by the SCN).
**Lesson:** After applying SCN changes, re-count structural elements. Don't rely on pre-SCN counts.
