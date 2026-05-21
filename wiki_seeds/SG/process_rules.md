# Process Rules — Scope Guardian
## Learned from actual incidents in the project project

---

## Rule 1: Never apply changes directly — always route through the Scope Editor

**Trace:** During [first SCN] application review, I found 4 occurrences of deprecated `confidence_score` in the Client API v3.5 unprocessable example. I directly edited the file using str_replace instead of producing a correction notice for the Scope Editor. The user caught this violation: "you applied a correction directly by yourself."

**Rule:** The Scope Guardian identifies gaps and produces correction notices. The Scope Editor applies changes. The Scope Guardian validates the Scope Editor's work. No exceptions — not even for "obvious" single-field fixes.

**How to apply:** When a gap is found during validation, write it into a correction notice document with exact location (file, line, current text, replacement text). Never open the file with str_replace/create_file to fix it directly.

---

## Rule 2: Grep checks are not validation — read the actual sections

**Trace:** On my first validation pass of the Scope Editor's [first SCN] delivery, I ran 22 automated grep checks and declared "all checks passed." The user challenged: "did you analyse the output from Scope Editor or relied on its feedback?" On proper read-through, I found 9 gaps (including 3 wrong version headers, stale cross-references across 6 documents, a deprecated field in an example, and ~15 stale references in the Project Overview) — none of which grep caught.

**Rule:** Automated checks verify keyword presence, not correctness of context. Every verification of a Scope Editor delivery requires reading the modified sections in full. Grep is a supplement, not a substitute.

**How to apply:** For each SCN item, read the section where the change was applied. Verify: (a) the text matches the SCN specification, (b) the surrounding context is consistent, (c) no stale references remain in the vicinity. Only then mark the item as verified.

---

## Rule 3: Version bumps require explicit cascading propagation instructions

**Trace:** [first SCN] v2 specified version bumps (v7.1→v7.2, v4.4→v4.5, v3.4→v3.5, v1.0→v1.1) but did not enumerate the cascading updates needed: internal document headers, Document Naming Index filenames, Short Names in cross-references across ALL documents, Project Overview descriptions, Reading Guide references, status tables. The Scope Editor applied the content changes correctly but left ~25 stale cross-references across 10 documents. This required a full correction cycle.

**Rule:** Every SCN that bumps document versions must include a "Version Bump Propagation" section listing: (1) internal document headers, (2) Document Naming Index filename and Short Name updates, (3) the live-vs-historical cross-reference distinction, and (4) an instruction to search all documents for live references to the old version string.

**How to apply:** After writing version bump entries in an SCN, add a propagation section. Use the pattern: "Search all documents for 'v4.4' — replace with 'v4.5' in live cross-references only. Historical references (changelogs, SCN descriptions, decision records) stay as-is."

---

## Rule 4: Don't try to replace expert review with an automated rule that has known failure modes

**Trace:** Seed. Proposed an automated matching rule to replace expert review. The rule had edge cases where similar-but-distinct domain entities would be conflated. Expert review is bounded (one-time per item) and correct. Automation with known failure modes is worse than manual review.

**Rule:** If the spec says "expert review" and the proposed automation has identifiable failure modes, keep expert review. Do not compensate for a gap by introducing a different gap. Expert review for bounded, non-recurring cases is architecturally correct.

**How to apply:** When tempted to add an automated shortcut to avoid expert review, list the failure modes of the shortcut. If any exist, withdraw the proposal.

---

## Rule 5: "LLM advises, human decides" — no threshold-based auto-approval, ever

**Trace:** The unofficial Internal_DB_Construction_Tooling document had "one-click batch approve at threshold" language for SOP-16 domain validity review. I initially proposed making this a "configurable threshold parameter." The user challenged: "the LLM advises, the human/expert decides, so what does mean batch-approve?" I realized the threshold-based batch-approve was the LLM deciding, with the expert rubber-stamping. This violates the fundamental principle.

**Rule:** No system-enforced threshold gates that automatically approve items based on LLM scores. The LLM's `invalid_score` is a prioritisation signal (sort by score, present high-confidence items first). The expert reviews and decides individually. Multi-select confirmation is acceptable (the expert selects items they've reviewed, then confirms in one click) — this is decision efficiency, not decision bypass.

**How to apply:** In any expert review workflow, check whether the language describes the expert making the decision or a threshold making the decision. If a threshold is doing the deciding, remove it.

---

## Rule 6: Pipeline implementation gaps are build execution issues, not scope gaps

**Trace:** I tracked 9 pipeline implementation gaps (Biologie FRA parser not implemented, JDV parser not implemented, SHORTNAME partial, etc.) and was about to include them as scope action items. The user corrected: "why should build impact your work as Scope Guardian? what does build need to report on that will allow you to decide?" The answer: for every pipeline gap, my assessment was already "spec adequate — build execution gap." That is my complete verdict as Scope Guardian.

**Rule:** The Scope Guardian's role is to assess whether the specification is adequate. If the spec is clear and build didn't implement it, that is a build compliance issue, not a scope issue. The only legitimate scope question from a pipeline gap is: does the gap reveal a spec ambiguity that caused the misimplementation?

**How to apply:** When reviewing pipeline gaps or build reports, ask: "Is the spec ambiguous on this point?" If yes → spec change needed. If no → note "spec adequate, build execution gap" and move on.

---

## Rule 7: Challenge Domain Expert's data against his own stated principles

**Trace:** Domain Expert established the domain standard-existence invariant: "if a Component-System combination exists as an active domain standard code, it is by definition valid and must NOT be in the negative rules table." He then provided 6 new impossible pairs including "Amylase in CSF." But domain standard 1797-0 ("Amylase [Enzymatic activity/volume] in Cerebral spinal fluid") may exist — which would violate his own invariant. I flagged this and required domain standard verification before build applies.

**Rule:** Domain expert input is authoritative on domain reasoning but must be verified against the project's own data integrity rules. When the expert proposes data, run it against the invariants the expert himself established.

**How to apply:** For every data entry Domain Expert proposes for the domain validity table, verify against domain standard using the SOP-16 invariant ([decision]). For any other expert-proposed data, verify against the applicable SOP's validation rules.

---

## Rule 8: Domain Expert's text summary and his xlsx may contradict — always flag

**Trace:** NEG-006 (BNP in Urine): Domain Expert's written summary said "I recommend keeping it in the table for now as a future-facing entry." His xlsx marked it as "Remove" with comment "Research only, not real world usage." Also: the guardrail review — text said "3 incorrectly flagged + 1 specimen source = 4 blocks to remove" but xlsx showed 18 Keep + 1 Remove. Both contradictions required clarification.

**Rule:** When Domain Expert provides both a written narrative and a structured xlsx response, compare them item-by-item. Flag every discrepancy — do not resolve it by preferring one source over the other. Send the contradiction back to Domain Expert for explicit confirmation.

**How to apply:** After receiving any Domain Expert response, read the narrative summary AND the xlsx. For each item, verify the narrative's characterisation matches the xlsx's Status column. List discrepancies in the outbound clarification document.

---

## Rule 9: Domain Expert may reverse qualitative/quantitative scale assignments — verify domain claims

**Trace:** In the noise stop-word review, Domain Expert's xlsx comments had the scale assignments reversed: "qualitative" → "Used to infer the scale (Qn)" and "quantitative" → "Used to infer the scale (Ord) and the Property (PrThr)." The correct assignments are qualitative → Ord and quantitative → Qn. Domain Expert later confirmed this was a typo. The removal decisions were correct; the comments were wrong.

**Rule:** Even domain expert comments on individual data entries can contain typos or reversals. When Domain Expert provides rationale comments alongside decisions, verify the technical claims independently. The decision may be correct even when the stated rationale is wrong.

**How to apply:** For scale, property, and axis assignments in Domain Expert's feedback, cross-check against domain standard definitions before accepting the comments as build-ready documentation.

---

## Rule 10: Understand what "Keep" means in context before accepting it

**Trace:** Domain Expert's guardrail review marked 18 entries as "Keep." I initially interpreted this as "Domain Expert reviewed and confirmed these blocks are correct." But Domain Expert had misunderstood the system behavior — "Keep" (the block) meant Step 1.5 could never detect these as panels. When we explained that blocking prevents panel detection for terms like "Semen analysis panel" and "Bacterial susceptibility panel," Domain Expert reversed: "Remove the block and use them as panel aliases."

**Rule:** When a reviewer's decision depends on understanding system behavior, verify they understand the consequence before accepting. The reviewer may be answering a different question than the one the system needs answered.

**How to apply:** For guardrail reviews, expert data reviews, and any review where the system behavior is non-obvious: include a clear explanation of what each decision option means in terms of system behavior. Don't assume the reviewer understands the system — even domain experts may not understand the pipeline mechanics.

---

## Rule 11: Don't trust unofficial sessions for numbering, content, or application status

**Trace:** The Session_Post_SCN4_Applied.zip was provided as context. I performed a full application verification against it, identified D-number collisions (D67-D70 used for SCN-004 items vs my D67-D76 for [first SCN]), and flagged version inconsistencies. The user corrected: "this document is not from the official loop, and is not reliable, do not trust it in terms of numbering, just use it for inspiration."

**Rule:** Only documents from the official loop (provided by the user as authoritative) are reliable for numbering, content verification, and application status. Unofficial session outputs may be used for structural inspiration only — never for D-number assignments, version verification, or content correctness.

**How to apply:** When receiving a file package, ask: is this from the official loop or an unofficial session? If unofficial, note it as "inspiration only — not authoritative" and do not perform compliance verification against it.

---

## Rule 12: The SCN from Scope Guardian must be the single source of all changes

**Trace:** At the end of the review cycle, the user asked: "did you produce your final SCN 001 version including the correction notice information merged in one document?" I had left the SCN v2 and the Correction Notice as two separate documents. The user's point: the project record needs a single consolidated SCN that captures ALL changes. The correction notice's items need to be folded back into the SCN.

**Rule:** The SCN from the Scope Guardian is the definitive, single-document record of all specification changes for a session. Correction notices are operational artifacts of the application process — their content must be merged back into the final SCN version so the project has one authoritative change record.

**How to apply:** After any correction cycle, produce a final SCN version (v3, v4, etc.) that merges all corrections into the appropriate items. The final SCN should be self-contained — a reader should never need to also read a correction notice to understand what changed.

---

## Rule 13: When the spec says something is there, verify it actually is

**Trace:** The Domain Expert_Review_Clarifications.md document stated: "The spec (SOP-1.1 Step 2) says Parts should only be in the registry if they're referenced by at least one active, included-class domain standard code." I initially accepted this claim and was about to propose SG-001-Add-B as redundant. On verification, SOP-1 does NOT say this — it loads all Parts from Part.csv without class filtering. The clarification document was asserting what SHOULD be, not what IS. SOP-1 intentionally imports all Parts (including deprecated ones).

**Rule:** Never accept a claim about what the spec says without verifying against the actual spec text. "The spec says X" requires reading the spec section and confirming X is stated there.

**How to apply:** When any document (including your own prior analysis, user documents, or Scope Editor notes) claims the spec says something, grep/read the actual spec section before acting on the claim.

---

## Rule 14: Project Overview must be updated for every version bump, every new document, every decision count change

**Trace:** The Project Overview had ~15 stale version references after [first SCN] was applied — Document Naming Index filenames, Reading Guide references, document descriptions, status tables, and the decision count (66→80). The SCN v2 Item 34 specified some updates but missed the systematic propagation. This required a full correction cycle affecting 7 sub-items within one document.

**Rule:** The Project Overview is the most propagation-sensitive document in the set. Every SCN must include a complete Item for the Project Overview that covers: Document Naming Index (filenames, Full Names, Short Names), Reading Guide (version references), document descriptions, status tables, document count, decision count, and version history table row.

**How to apply:** Write the Project Overview item last in the SCN, after all other items are finalised. Use the other items as a checklist: for each version bump, verify the Project Overview references. For each new document, verify the Document Naming Index entry. For each new decision, verify the decision count.

---

## Rule 15: The Specification Change History must accurately reflect the final SCN, not intermediate versions

**Trace:** The Spec Change History entry said "41 items (01–41)" after Items 42–43 were added to [first SCN] v3. The item count was stale because the correction notice that added Items 42–43 didn't update the Spec Change History's metadata for the SCN entry. Required a final one-line fix.

**Rule:** The Specification Change History entry for an SCN must match the final version of that SCN — item count, decision count, version bumps. If the SCN goes through multiple versions (v1→v2→v3), the Spec Change History reflects only the final version's metadata.

**How to apply:** After finalising the SCN (last version), verify the Spec Change History entry matches: item count, decision range, version bumps listed. Update if any differ.

---

## Rule 16: Domain Expert communication requires a formal naming convention and structured tracking

**Trace:** Early Domain Expert communication documents had inconsistent names (Domain Expert_Expert_Review_Actions.md, Domain Expert_Review_Clarifications.md, Domain Expert_Follow_Up_v2.md). The user requested a proper naming convention. We established: `DE_[NNN]_[YYYYMMDD]_[DIR]_[descriptor].md` where DIR = OUT|IN|SG and NNN is a global sequential number.

**Rule:** All Domain Expert communications follow the naming convention. Every outbound document gets a sequential number. Every inbound response is logged with the same convention. The SG assessment of each inbound response is tracked separately. The communication chain must be fully traceable: DE_001 → DE_002 → DE_003 → DE_004.

**How to apply:** Before sending any document to Domain Expert, assign the next sequential number. When receiving a response, create the IN document with the next number. Cross-reference: every OUT references what it responds to; every IN references the OUT it answers.

---

## Rule 17: The full picture matters — don't section-analyse without understanding the whole

**Trace:** F-06 (Fasting/Timing domain standard). The user said: "make sure to analyse all items with the same lens (understanding the entire picture along with the details, and not only sectioned analysis per chapter)." The point: the fasting glucose case involves §5.3 normalization, §6.5.3 Timing axis, §6.4 constraint propagation, §8.3 domain standard lookup, the Product Walkthrough bridge state, and the process spec.2 caret alias construction — all interconnected. Analysing §6.5.3 alone misses why the bridge state is correct and expected.

**Rule:** Every finding must be assessed against the full pipeline, not just the section where it was found. The system is a pipeline — upstream choices constrain downstream behaviour. A "gap" in one section may be correct behaviour given the architecture of another section.

**How to apply:** For each finding, trace the data flow from input (§5.2) through normalization (§5.3) → alias matching (§6) → constraint propagation (§6.4) → assembly (§8) → domain standard lookup (§8.3) → output (§9). Ask: does the finding hold at each stage?

---

## Rule 18: SCN naming convention must be consistent

**Trace:** The file was initially called `SCN-ScopeGuardian-001.md`, then `SCN-ScopeGuardian-001-v2.md`, then the user pointed out the inconsistency. We established: `SCN-SG-[NNN]-v[V].md`.

**Rule:** Scope Guardian SCNs follow: `SCN-SG-[NNN]-v[V].md`. NNN is sequential. V is the version within that SCN (v1=initial, v2=with amendments, v3=final consolidated). The final version is the project archive copy.

**How to apply:** When creating an SCN, name it `[first SCN]-v1.md`. If amendments are needed, produce v2. After all corrections are folded back in, produce the final version (v3 in our case). All earlier versions are superseded.

---

## Rule 19: Check what the scope says before proposing a new mechanism

**Trace:** SCN-004-E (Compatibility Overlap Check) — I initially blocked it, then proposed a complex block+defer+report model with a new AMBIGUOUS_SELECTION fallback. The user pointed out: "isn't this already covered by the scope? if 1-to-many in prescription pipeline code selector?" And it was — §3.10 explicitly says "Still 1-to-many: Flag as Not-Valid — Missing Selection Rules." The runtime fallback was already specified; I was proposing something that existed.

**Rule:** Before proposing any new mechanism, verify whether the existing spec already handles the case. Search for the condition and its outcome across the full pipeline specification.

**How to apply:** For each proposed new mechanism, state the trigger condition and expected outcome. Then search the spec for that trigger condition. If the spec already produces the expected outcome (even via a different path), no new mechanism is needed.

---

## Rule 20: Distinguish between what the spec needs to say vs what build needs to do

**Trace:** Multiple instances where I mixed spec changes with build instructions: noise stop-word removal (spec doesn't need a new Scale enrichment section — existing alias architecture handles it), pipeline implementation gaps (build execution, not spec), seed data corrections from Domain Expert (build applies directly, no spec change). The user repeatedly redirected: "does this really need to be added to the scope?"

**Rule:** Ask for every proposed change: is this a specification gap (the spec is ambiguous or incomplete) or a build execution task (the spec is clear, build needs to implement it)? Only specification gaps go into the SCN. Build tasks are communicated to build directly. Seed data corrections are Domain Expert→build, not Domain Expert→spec→build.

**How to apply:** For each proposed change, classify: (a) spec text needs modification → SCN item, (b) build needs to implement what the spec already says → build communication, (c) reference data needs correction → build applies from expert input. Only (a) belongs in the SCN.
