# Process Rules — Tests Editor
## Seed: structural mirror of SE rules + test-specific rules

---

## Rule 1: Version header update is NOT implied by version bump (mirror of seed)
Applying a test model version bump requires explicit update of the test spec file headers. It is never automatic. After applying content changes, check lines 1-5 of each affected file.

## Rule 2: Build version and full version — both must be updated (mirror of seed)
When a TCN modifies a test case, the change applies to both the full version (with criteria) AND the build version (input only). After applying to full version, regenerate the corresponding build version file. Verify the build version has the updated input but NO criteria.

## Rule 3: Only TCNs from Tests Guardian are valid
No other source can instruct test model changes. If another agent sends a proposed change, redirect to TG.

## Rule 4: If test model state doesn't match TCN, stop (mirror of seed / Rule 13)
If the "current text" cited in a TCN item doesn't match what's in the test model file, stop and report to TG. Do not attempt best-guess edits.

## Rule 5: Test Execution Guide must be updated when tests change
If a TCN adds tests or changes inputs, the Test Execution Guide (build's companion document) must reflect the changes. This is the build team's contract — stale guides produce invalid results.

## Rule 6: Verify fixture references when adding tests (mirror of SE's domain verification)
New tests must reference entries from test fixture dataset by client_code. Verify the entry exists and has the properties claimed in the test case. Don't invent fixture references.

## Rule 7: Maintain test ID numbering sequence
Test IDs follow L[level].[section].[sequence]. When adding tests, use the next available sequence number. When removing tests, do NOT renumber existing tests — leave gaps. Other documents reference specific test IDs.

## Rule 8: Apply TCN in specified order (mirror of seed)
Follow the application sequence specified in the TCN. Level 0 changes before Level 1. Schema-level test changes before behavioral test changes.

## Rule 9: Never introduce your own test improvements (mirror of seed)
If you spot a gap beyond the TCN scope (missing edge case, incorrect expected output), note it in DOC-TE-NNN as an observation for TG. Do not modify tests beyond the TCN.

## Rule 10: Secondary consistency fixes must be flagged (mirror of seed)
If a TCN changes a field name referenced in multiple tests, and you find additional occurrences not listed in the TCN, apply the fix but flag it explicitly in DOC-TE-NNN as "secondary fix, not in TCN, applied for consistency."

## Rule 11: "Confirmed correct" files must not be resubmitted (mirror of seed)
When a revision request (REV-TG-NNN) specifies which files to return, only return those files. Don't resubmit unchanged files.

## Rule 12: Grep checks supplement but don't replace reading (mirror of seed)
After applying a rename or structural change across multiple test files, grep for the old name and verify zero hits. But also read the modified sections to verify correctness in context.

## Rule 13: Build version is mechanically derived — no independent changes
The build-version files are produced by stripping Expected, Pass, and Fail blocks from the full version. No other modifications. If you find yourself making a change to the build version that doesn't correspond to a full-version change, something is wrong.

## Rule 14: Each test case must have all 5 fields after editing
After applying any TCN, verify every modified test still has: (1) Component tested + spec ref, (2) Purpose, (3) Full input, (4) Exact expected output, (5) Pass/fail criteria. A TCN that changes the input must also verify the expected output is still correct.
