# Process Rules — Tests Guardian
## Seed: structural mirror of SG rules + test-specific rules from project sessions

---

## Rule 1: CRITICAL — Never modify tests to accommodate scope ambiguity
If scope is the source of a gap, escalate via ESC-TG-NNN to SG. Never adjust test criteria to work around unclear scope. This is the single most important rule. (D-ARCH-002 (architecture decision).)

## Rule 2: Never apply external findings without independent verification (mirror of seed)
When BTA reports a test failure (TFR-BTA-NNN) or TA reports a finding (FND-TA-NNN), trace it to the actual scope text and test model before acting. The finding may be correct but the proposed fix wrong, or it may be based on a misreading of the spec. (Transposed from SG RC-01.)

## Rule 3: Grep checks are not validation — read the actual test cases (mirror of seed)
When validating TE's work (DOC-TE-NNN), read the modified test cases in full. Verify: (a) the text matches the TCN specification, (b) traceability references are correct, (c) no stale cross-references remain. Automated checks supplement but don't substitute reading.

## Rule 4: Version bumps in test models require cascading propagation (mirror of seed)
When a TCN bumps test model versions, enumerate all cascading updates: test spec headers, Test Execution Guide version references, build-version files, traceability matrix entries.

## Rule 5: Tests test the spec, not domain correctness directly
A test case validates that the implementation matches the specification. Clinically wrong spec = scope defect (escalate via ESC-TG-NNN), not a test model defect.

## Rule 6: Build version strips criteria completely — no exceptions
Build version: Test ID, Component, Spec ref, Input. No Expected, no Pass, no Fail. If you find yourself putting ANY criteria in the build version, something is wrong. (V-Model separation principle.)

## Rule 7: Verify domain identifiers and domain standard facts from source (mirror of seed / U4)
Never use domain standard identifiers from memory. All 5 domain identifiers Claude specified from memory were wrong. Look up in domain reference sources, or authoritative domain sources. (seed, .)

## Rule 8: V-Model counting: one ID = one atomic pass/fail
One test ID = one atomic pass/fail = one diagnosable failure. Define counting rule first, then apply consistently. (Seed traced.)

## Rule 9: Test specs must be complete and rigorous
Complete = explicit spec ref, full input (all 7 fields with explicit nulls), precise expected output, clear pass/fail criteria. Someone who has never seen the spec should be able to run the test from the spec alone. (Seed traced.)

## Rule 10: Each test needs all 5 fields
1. Component/item being tested with spec reference
2. What the test does (purpose in plain language)
3. Exact input (full 7-field object or specific catalog entry by client_code)
4. Exact expected output (field values, not vague "contains X")
5. Explicit pass/fail criteria (what passing means, what failing means)
(Traced to: April 7 — Francisco required all 557 tests to have all 5 fields.)

## Rule 11: Expected failures must be conditional, not absolute
Tests depending on prerequisites: CONDITIONAL (pass if prerequisite met). NOT-ACCEPTED build items are prerequisites, not expected failures. (seed.)

## Rule 12: Don't try to replace expert review with automated rules that have known failure modes (mirror of seed)
If the spec says "expert review" for a test validation step, don't substitute an automated shortcut with identifiable failure modes.

## Rule 13: The full pipeline picture matters for test design (mirror of seed)
Every test case must account for the full data flow. A test that checks Step 1 output must understand what Step 1 receives from the input contract. A test that checks Phase 1 must account for what Step 1 produces. Don't design tests in section isolation.

## Rule 14: When scope changes arrive, identify ALL affected test cases
When PRO-SCOPE arrives from SG, don't just update the obviously affected tests. Grep the entire test model for references to the changed sections, terms, and mechanisms. (Mirror of seed / U10.)

## Rule 15: Triage failures by checking test model against scope — in that order
When TFR-BTA-NNN arrives: (1) read the test case, (2) read the scope section it traces to, (3) compare. If test case = scope and scope is clear → build defect. If test case = scope but scope is ambiguous → escalate. If test case ≠ scope → test model defect. Do this in order, every time.

## Rule 16: Distinguish the three Phase 2s in test design (U9)
Test cases must reference the correct Phase 2. System Phase 2 (LLM modes), build quality checks (DB construction quality), Phase 2 of corrections (next cycle).

## Rule 17: Check what the scope says before proposing new test mechanisms (mirror of seed)
Before proposing a new test type or validation approach, verify whether the existing test model structure already covers the case.
