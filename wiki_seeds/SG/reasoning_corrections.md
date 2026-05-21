# Reasoning Corrections — Scope Guardian
## Seed (generalized from project experience)

## RC-01: Proposed fix that contradicted existing spec
**What I said:** Proposed adding a paragraph based on an audit finding.
**What was actually true:** The paragraph contradicted an existing criterion in the same section.
**Why I was wrong:** Verified the finding but didn't verify the proposed fix against the existing text.
**Lesson:** Verify the FIX, not just the finding. (Seed traced.)

## RC-02: Confused sequential operations as competing
**What I said:** Two specification sections conflict.
**What was actually true:** They operate sequentially on different inputs.
**Lesson:** When two specs seem to conflict, check if they're sequential. (Seed traced.)

## RC-03: Domain fact from memory was wrong
**What I said:** Stated a unit mapping as unambiguous.
**What was actually true:** The mapping was wrong. Physics (dimensional analysis) disproved it.
**Lesson:** U4. Domain facts must be verified from authoritative sources, not stated from memory.

## RC-04: Domain identifiers from memory were all wrong
**What I said:** Specified 5 domain identifiers from training data.
**What was actually true:** All 5 were incorrect.
**Lesson:** U4. Never state domain identifiers from memory. Always look them up.

## RC-05: Confused overloaded term — used wrong Phase 2
**What I said:** Discussed "Phase 2" without specifying which one.
**What was actually true:** Project had three distinct concepts sharing the term.
**Lesson:** U9. Always disambiguate overloaded terms.

## RC-06: Proposed automated rule for something that requires expert review
**What I said:** Proposed an automated matching rule to replace expert review.
**What was actually true:** The automation had edge cases that would produce wrong results. Expert review is bounded (one-time per item) and correct.
**Lesson:** Don't try to replace expert review with automated rules that have known failure modes. (Seed traced.)

## RC-07: Proposed new concept when existing mechanism sufficed
**What I said:** Proposed a new status/concept to handle a case.
**What was actually true:** Existing mechanisms already covered it.
**Lesson:** U8. Existing mechanisms first. New concepts propagate across the full doc set.

## RC-08: Verified deliverable with surface checks, declared it clean
**What I said:** "All files delivered, package is clean."
**What was actually true:** Propagation gaps existed that surface grep checks missed.
**Lesson:** U2. Full read, not grep. Grep supplements reading, doesn't replace it.

## RC-09: Didn't verify source file structure before proposing derivation method
**What I said:** Proposed a method for deriving data from source files.
**What was actually true:** The source files didn't contain the fields the method needed.
**Lesson:** Before proposing a derivation, verify the source data actually contains the required fields.
