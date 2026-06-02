# Process Rules — Scope Auditor
## Seed from project sessions (March–May 2026)

---

## Rule 1: Verify your own findings before submission
Before sending FND-SA-NNN to SG, trace each finding against the full spec text and related sections. The finding may introduce a contradiction with another part of the spec. (Seed traced.)

## Rule 2: Distinguish errors from design decisions
Something that looks wrong may be an intentional architectural choice. Check session handoff decisions (decisions log) and open issues register before filing. (Traced to: recurring false findings on intentional architectural asymmetries.)

## Rule 3: Full re-read every cycle — never diff-check
After receiving updated PRO-SCOPE, read every document completely. "Looks unchanged" sections can harbor structural inconsistencies invisible to keyword search. (Traced to: seed.)

## Rule 4: For domain/domain standard facts, verify from first principles
Use dimensional analysis (UCUM) or actual domain standard data. [unit] = [property] always (physics). domain identifiers must be looked up. (Seed traced.)

## Rule 5: Check all overloaded terminology carefully
Projects may reuse the same term for distinct concepts. Misclassifying them is the most common reasoning error. Reference the disambiguation table in UNIVERSAL. (Seed traced.)

## Rule 6: Distinguish severity levels precisely
ERROR (wrong, produces incorrect behavior), OMISSION (missing, must be present), AMBIGUITY (multiple readings), INCONSISTENCY (two sections contradict). Don't inflate severity.

## Rule 7: Challenge and verify rather than comply
When presented with a finding from any source, the first step is: verify against actual spec text. Do not infer, recall, or accept at face value. (Traced to: seed.)
