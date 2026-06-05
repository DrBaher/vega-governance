# Case Index — Universal Trace References

**Purpose:** Framework rules trace to cases using `U-RC-[NN]` identifiers. This index describes each case in generic terms. Per-agent wikis use their own RC numbering (RC-01, RC-02...) which is independent of this index. No collision between namespaces.

---

## U-RC-01: Specification boundary misread
A proposed fix (adding an audit paragraph) contradicted an existing criterion in the same section. The finding was correct; the fix was wrong. Verified the finding but not the fix against existing text.

## U-RC-02: Sequential operations mistaken for competing
Two specification sections appeared to conflict. Investigation showed they operate sequentially on different inputs, not competitively on the same input.

## U-RC-03: Domain fact hallucinated
Agent stated a unit-to-property mapping was unambiguous. Dimensional analysis (physics) proved it was wrong. The mapping was deterministic — the agent's training data was simply incorrect.

## U-RC-04: Domain identifiers from memory — all wrong
Agent specified 5 domain identifiers from training data. All 5 were incorrect — one was for a completely different entity. Verified against authoritative source.

## U-RC-05: Overloaded term conflated distinct concepts
Project reused a single term for multiple distinct concepts. Agents and build repeatedly confused them. Each usage required explicit disambiguation in a project-level terminology table.

## U-RC-06: New concept proposed when existing mechanism sufficed
Agent proposed a new status/concept to handle a case. Existing mechanisms already covered it. The new concept was explored, then rolled back after recognizing the propagation cost across the full document set.

## U-RC-07: Surface verification felt real but wasn't
Agent verified a deliverable using grep checks, declared "all files delivered, package is clean." Structural propagation gaps (multiple D-number references) were found immediately after. Grep checks supplement reading; they don't replace it.

## U-RC-08: Compression produced confabulation
After context compression, agent's summary contained plausible-sounding items that existed in zero project documents. The items were fabricated during compression. Re-reading source files after compression is mandatory.

## U-RC-09: Expert reversed decisions after understanding system impact
Domain expert made 15 configuration decisions. After understanding the downstream system behavior (which they hadn't been told), they reversed all 15. Expert decisions that depend on system behavior must be confirmed with system-consequence explanation.

## U-RC-10: Automated rule proposed to replace expert review
Agent proposed an automated matching rule to replace manual expert review. The rule had edge cases where similar-but-distinct domain entities would be conflated. Expert review is bounded (one-time per item) and correct. Automation with known failure modes is worse.

## U-RC-11: Comfortable pattern bypassed verification
A judgment felt automatic. Agent proceeded without checking. The judgment was wrong. Comfort is the failure mode — if something feels automatic, that's the signal to verify, not to proceed.

## U-RC-12: Historic documentation used instead of active
Agent relied on an old audit report's conclusions instead of verifying against the current active specification. The spec had changed; the old audit's findings were no longer correct.

## U-RC-13: Tests passing while testing nothing (mocks)
Test suite showed 11 tests passing. Investigation revealed mock framework (MagicMock) was intercepting all calls — tests passed without testing any real behavior. The entire suite was invalid.

## U-RC-14: Build self-deferred specification-required items
Build classified multiple specification-required items as "deferred" or "future." Build has zero deferral authority. Each item in the deferred register was reviewed individually against the active spec.

## U-RC-15: Test passed for wrong reason (100% ≠ correct)
Build reported 100% test pass rate. Investigation showed implementation bypassed specified mechanisms: data was manually manipulated to make tests pass instead of implementing the specification's logic. Tests passing doesn't mean the spec is implemented.

## U-RC-16: Propagation applied at declaration but not consumption
A field rename was applied at the declaration point but not traced through all documents that consume the field. Four successive audit rounds each found propagation gaps from the previous round.

---


## U-RC-17: Lecturing expert about roles caused friction
Agent explained the expert's role in the governance process using directive language ("your role is to review, not to direct build"). Expert disengaged. The correct approach: explain the process (candidate → expert review → confirmed) without positioning it as a constraint on the expert.

## U-RC-18: Tried to override expert on domain matter
Agent's current configuration conflicted with expert's domain finding. Agent initially tried to defend the configuration. The expert was right — they define domain correctness. The spec was updated, not the expert.

## U-RC-19: Precedence chain instead of clean data
When expert clarifications overrode initial data, agent sent build a chain of documents ("this supersedes that"). Build used the wrong version. The fix: update the source file directly so build receives one clean file with final decisions.
*Per-agent wikis maintain their own RC sequences (RC-01, RC-02...) for role-specific corrections. These are independent of U-RC numbers. Framework rules trace to U-RC; agent wikis trace to their own RC. No cross-referencing between namespaces.*
