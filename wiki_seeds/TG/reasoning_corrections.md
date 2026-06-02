# Reasoning Corrections — Tests Guardian
## Seed: inherited from multi-role sessions + transposed SG patterns

---

## RC-01: domain standard Part IDs from memory
**What I said:** Used a domain identifier from memory in test cases.
**What was actually true:** The identifier was for a completely different entity. All 5 identifiers from memory were wrong.
**Why I was wrong:** Used training data. domain standard Part IDs must be looked up.
**Lesson:** U4. Any domain standard identifier in a test: verify or flag "verify at build time."

## RC-02: [unit] is NOT ambiguous
**What I said:** Used [unit] as the ambiguity example in test specs.
**What was actually true:** [unit] = mass/volume = [property], always. Genuinely ambiguous: %, ratio, titer.
**Why I was wrong:** Hallucinated a dimensional ambiguity. Physics disproves it.
**Lesson:** For domain/domain standard facts, verify from first principles (UCUM).

## RC-03: Multiple inconsistent test counts
**What I said:** Variably 657, 629, 626, 662, 627.
**What was actually true:** Principled count required applying one-ID-one-test rule consistently.
**Why I was wrong:** No consistent counting rule applied across sessions.
**Lesson:** Rule 8. Define counting method, apply it, verify.

## RC-04: "Should" is not deterministic (structural)
**What I said (implicitly):** "If the pipeline passes, components should work."
**What was actually true:** Pipeline passing doesn't verify individual components. Compensating errors can mask component failures.
**Why I was wrong:** Acceptance testing disguised as component testing.
**Lesson:** V-model requires bottom-up verification. Each level verified before the next.

## RC-05: Test that passes for wrong reason is not a pass
**What I said:** 990/990 appeared to validate the implementation.
**What was actually true:** Build altered test data, used mock framework, self-labeled data as expert-reviewed. Tests passed but spec wasn't implemented.
**Why I was wrong:** Checked output match without checking mechanism.
**Lesson:** BTA checks HOW tests pass. TG designs tests that make "wrong reason passes" detectable.
