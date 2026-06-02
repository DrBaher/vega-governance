# Build Interaction Patterns — Build Rep

## 10 Recurring Build Behaviors

### 1. Self-deferring spec-required items
Build classifies required items as "deferred"/"future." Multiple specification-required items all improperly deferred. Fix: zero deferral authority, item-by-item review.

### 2. Modifying test expectations to match implementation
Most dangerous. Under V-Model, build can't see expected outputs. Watch for subtler forms: changing preconditions, filtering data, reinterpreting inputs.

### 3. Silent reliance on mocks
mock framework makes all tests pass without testing anything. A prior incident: 11 tests passed while testing nothing — mock framework was intercepting all calls. Require mock audit grep.

### 4. Applying changes without confirmation
Build sometimes executes before authorization. "NOT ACCEPTED — must be implemented" = authorization. Questions await answers.

### 5. "Miraculous resolution"
Deferred items claimed "resolved" without explanation. Ask: what changed? When? Show implementation.

### 6. "Spec-deferred" misclassifications
Distinguish: (a) spec explicitly defers (legitimate), (b) dormant but mechanism must exist, (c) not implemented (gap).

### 7. Confusing overloaded terminology
Build conflates overloaded project terminology. Check which meaning every time.

### 8. Manual data without expert review
Build labels data "expert-curated confirmed" without expert. Every "expert review" touchpoint means the Domain Expert, not build.

### 9. Unauthorized additions
Machine-generated data silently populated without authorization. Watch for undocumented data.

### 10. Register they can't find
Submitted register, then couldn't locate when referenced.

## Communication That Works
- Numbered Q&A (Q1-Q16), one fact per question
- Tables with spec references for every claim
- Explicit owner per action item (Build / Domain Expert / Us)
- Clean data files (CSV, corrected xlsx)

## Communication That Doesn't
- Long prose with embedded action items
- Mixing decisions with questions
- "Deferred"/"resolved"/"pending" without qualifier
- Batch approvals without item-by-item review

## V-Model Contract
Build receives: specs + test inputs (Test Execution Guide). Build reports: actual outputs per test ID. We compare. Build never sees expected outputs.
