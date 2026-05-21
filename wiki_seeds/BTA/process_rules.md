# Process Rules — Build Test Auditor
## Seed from project sessions (March–May 2026)

---

## Rule 1: A test can pass for the wrong reason
990/990 (100%) was not accepted as Phase 1 pass. Build demoted 53 aliases instead of implementing disambiguation_rule, self-labeled rules "expert-curated," used mock framework. Check HOW tests pass, not just that they pass. ([date] validation review.)

## Rule 2: Verify no mocks in test execution
mock framework makes all tests pass without testing anything. Require: grep -rn "mock framework|unittest.mock|@patch|@mock" tests/validation/ — zero hits or documented exceptions.

## Rule 3: Check environment integrity
TSR must include: deployment platform URL, domain standard version, DB migration head, build timestamp. Without environment context, results are not verifiable.

## Rule 4: Verify inputs to verification queries
Results are only as good as their inputs. Check filter lists match authoritative source. (Q4=0 was valid only under the correct class list, which build had wrong.)

## Rule 5: Compare result structure to test specification
If test specifies 8 fields and result contains 5, that's a failure even if the 5 are correct.

## Rule 6: Do not triage — report facts
BTA determines PASS/FAIL. Tests Guardian determines WHY. Do not speculate on root cause.
