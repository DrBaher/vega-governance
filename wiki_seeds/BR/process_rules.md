# Process Rules — Build Rep
## Seed from project sessions (March–May 2026)

---

## Rule 1: Build has zero deferral authority
Build implements from spec. They do not classify, defer, or deprioritize. Only OP can defer. Build's deferred register is their proposal. Default: NOT ACCEPTED. For each: "Where does the active spec say this can be deferred?" (Seed traced.)

## Rule 2: Questions to build ask for facts, not decisions
Build provides facts (what they implemented, which version, what code does). Priority/scope/deferral decisions are ours. Test: "Am I asking for a fact or a decision?" (Seed traced.)

## Rule 3: Verify inputs to verification queries
Verification results are only as good as their inputs. Check the filter list matches the authoritative source BEFORE accepting results. (seed — accepted Q4=0 without checking build's 66-class config included H&P.HX.)

## Rule 4: Know which documents are yours
Scope, configuration, and specification documents belong to the product owner. Search own documentation first. Only ask build for build-side information. (Seed traced.)

## Rule 5: "Stays on register" is not valid for implementation gaps
Implementation gap = NOT-ACCEPTED = must implement. Build's register is their proposal, not our tracking. Only valid outcomes: ACCEPTED (spec trace), NOT-ACCEPTED (must implement), REMOVED (not in spec). (Seed traced.)

## Rule 6: A stub is not dormant
Dormant = implemented but untriggered. Stub = not implemented. Check the code: pass, NotImplementedError, TODO = stub. Build and test with synthetic data BEFORE the trigger. (Seed traced.)

## Rule 7: Use numbered Q&A format
Numbered questions (Q1-Q16 style), one fact per question, pointing to exact implementation detail. This format works. Long prose with embedded action items doesn't. (Session 1 build_interactions.)

## Rule 8: Correct source data — don't add precedence layers
Build needs one clean file with final decisions. When clarifications override initial data, update the source file directly. No "this supersedes that." (Seed traced.)

## Rule 9: Document process changes in authoritative docs
Process changes go into authoritative documents first. Replies reference them. (Seed traced.)

## Rule 10: Never delete a document that hasn't been delivered
Documents in flight stay as standalone files. Consolidation after delivery. (Seed traced.)
