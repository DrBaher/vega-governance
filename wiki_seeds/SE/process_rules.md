# Process Rules
## Accumulated from [first SCN] Application Cycle, May 2026

---

## Rule 01: Version header update is NOT implied by version bump
**Trace:** [first SCN] specified version bumps across multiple specification documents. All document content was correctly updated. The document headers — the first 3 lines of each file — were not touched. Scope Guardian Correction 1, 2, and 3 caught this in the first correction cycle. These were HIGH severity because the headers are the document's identity marker — a reader checking the version number would see the wrong version.
**Rule:** Applying a version bump requires an explicit str_replace on the document header line, separate from content changes. It is never automatic.
**How to apply:** After applying all content changes for a version bump, grep for the old version string in the document header (lines 1–5) explicitly. Pattern: `grep -n 'v[0-9]\.' <file> | head -3`. If it shows the old version, it has not been updated yet. The header format varies by document type. Check each document's header convention and verify the version string, month, and year are all updated.

---

## Rule 02: Two response examples in §7.3 — Processed AND Unprocessable
**Trace:** A field rename was applied to one response example but not the other in the same API specification section. The second example had multiple occurrences of the deprecated field in a nested data block. These examples were at opposite ends of a long section and not visually adjacent.
**Rule:** When a specification section contains multiple complete examples, any schema field change must be applied to ALL of them. Search for every example block, not just the first one.
**How to apply:** After any schema field change in a section with multiple examples, search for ALL example blocks and verify the change appears in each. Command: `grep -n '[old_field]\|[new_field]' <file>` — count all occurrences and verify none are the deprecated field.

---

## Rule 03: Table of Contents rows are not updated by body-text changes
**Trace:** A field rename correctly updated the body text of a section but missed the structured Table of Contents entry for the same section. The ToC row still had the old field name.
**Rule:** When a specification has a structured reference table (Table of Contents, section index), updating the body of a section does NOT automatically update the corresponding index row. These are independent text blocks — verify both.
**How to apply:** After updating any section body, run `grep -n` for the section identifier in the reference table and verify the corresponding row reflects the change.

---

## Rule 04: Changelog order can break when inserting entries
**Trace:** Changes included a previous-version changelog entry and a new-version entry. When applied, the new entry landed out of sequence — the chronological order was broken.
**Rule:** When adding new changelog entries to a block that already has entries, always read the full current block before writing the new content. The desired output must explicitly include all existing entries in the correct order, not just the new additions.
**How to apply:** Before applying any str_replace that includes changelog content, run `sed -n '<start>,<end>p'` to read the full current changelog block. Write the replacement to include ALL entries in chronological order (v3.1 through v3.N). Never str_replace only the new entry if the surrounding context has existing entries that could end up displaced.

---

## Rule 05: Version bump propagation extends to SIX locations in Project Overview alone
**Trace:** An SCN specified version bumps but the propagation to the project overview was incomplete. Multiple stale version references remained across the overview's sub-sections (index, guide, descriptions, status tables, counts). This required a full correction cycle.
**Rule:** A version bump on a specification document requires updating six distinct locations in the Project Overview: (1) Document Naming Index row (filename + Full Name + Short Name), (2) Reading Guide references, (3) Document descriptions paragraph, (4) Status/Completion table, (5) session handoff description decision count if decisions changed, (6) Document count note if document set changed. Each is in a different section of the Project Overview.
**How to apply:** Create a checklist at bump time. After version bumping, run: `grep -n 'v[old]' <overview_file>` and cross-reference every hit against the list of what was already updated. Distinguish live references (pointing the reader to "go read this") from historical references (recording "this was current at that time" in changelogs or decision records). Historical references must NOT be changed.

---

## Rule 06: Cross-references across ALL documents must be updated when versions bump — not just the changed document
**Trace:** An SCN specified version bumps but did not enumerate the cascading cross-references. After application, multiple live cross-references across other documents still pointed to old version numbers.
**Rule:** A document version bump requires a search for live cross-references to the old version across the ENTIRE document package. Live cross-references = references that point a reader to the current version of a document (e.g., "see [Document] v[current] §[section]"). These differ from historical references (e.g., "[prior SCN] was applied to [Document] v[old]" — recording what was current at decision time). Historical references must stay.
**How to apply:** After every version bump, for each bumped document, run: `grep -rn '[Document] v[old]' <all_other_files>`. For each hit, apply the live/historical distinction: if it reads "go here to learn about X" → update. If it reads "this change was made against version X" → leave. Changelogs, SCN descriptions, and decision records are always historical. Active specification cross-references are always live.

---

## Rule 07: Item count in SCN entry must be updated when items are added post-facto
**Trace:** Items 42–43 were added to [first SCN] as part of a v3 correction. Both items were applied correctly. But the [first SCN] entry in the Specification Change History still read `41 items (01–41)` at line 472. This was caught by the Scope Guardian as a single-line gap after Item 43 was verified. This is a meta-consistency issue: the SCN entry describes itself, and its self-description was stale.
**Rule:** When adding items to an SCN (post-application corrections that become part of the same SCN), update the item count field in the Specification Change History entry for that SCN. The `Items:` header line must reflect the final count including all additions.
**How to apply:** After applying any item that is added to an existing SCN, run: `grep -n 'Items:.*items.*(' Specification_Change_History_updated.md` and verify the count includes the newly added items. Update both the number AND the range (01–41 → 01–43).

---

## Rule 08: Scope Editor analysis findings require Guardian approval before application — two findings were withdrawn
**Trace:** Initial analysis produced F-01 through F-13. Two findings were formally withdrawn by the Scope Guardian before any changes were applied: two findings that were either incorrectly characterized or whose proposed solutions had their own failure modes. Both were withdrawn because the characterized scenarios were either incorrect or the proposed solution had its own failure modes.
**Rule:** All findings from a Scope Editor analysis pass are hypotheses until the Scope Guardian reviews and approves them. Do not self-authorize changes on findings — the Guardian can and will withdraw findings. The Q&A section is the correct mechanism for surfacing ambiguities before the Guardian review.
**How to apply:** After producing an analysis, present all findings with a clear Q&A section for items with decision-level uncertainty. Wait for the Guardian's explicit approval or withdrawal of each finding before applying any change. A finding "not addressed" in a Guardian response is not approved by silence — it must be explicitly approved.

---

## Rule 09: Secondary consistency fixes within scope of a targeted correction may be applied proactively — but must be flagged
**Trace:** Correction 8 targeted §5.6 "mmol/L → always [property]". While applying this, I also found §12.7 ambiguity text had "mmol/L → always [property] (primary)" — the "always" had been left stranded when [first SCN] Item 07 added "(primary)". I applied a secondary fix to remove "always" from §12.7 as well. This was not in the correction notice. The Scope Guardian accepted it.
**Rule:** Secondary consistency fixes that are strictly within the scope of the correction target (same field, same concept, directly caused by the same underlying edit) may be applied alongside the explicit correction. But they must be flagged in the application report as secondary fixes, identified as beyond the explicit scope of the notice.
**How to apply:** After applying an explicit correction, search for the same pattern in adjacent related sections. If found: (a) apply the fix, (b) mark it clearly in the application log as "secondary fix, not in correction notice, applied for consistency," (c) explicitly ask the Guardian to verify this secondary fix is acceptable. Do not apply secondary fixes silently.

---

## Rule 10: The Document Naming Index has a strict 3-column format
**Trace:** A new document entry was added to an index table with the wrong number of columns, mixing the index format with a version history format. The correct format was 3 columns; the entry had 5.
**Rule:** The Document Naming Index uses exactly 3 columns: Filename (backtick-quoted), Full Name, Short Name (bolded). Version, date, and description do NOT belong in this table. Those belong in the Document Package Summary table (a separate table in Project Overview).
**How to apply:** When inserting a new row into an index table, verify it matches the column count of existing rows by counting pipes. Check the established format and follow it exactly.

---

## Rule 11: Filename versioning convention distinguishes specification from management documents
**Trace:** A correction codified the filename versioning convention that was implicit: specification documents include the version number in the filename, management/tracking documents do not.
**Rule:** When creating output files, versioned specification documents must include the version number in the filename using dot-separated notation (`_v1.1.md`, `_v4.5.md`). Management documents (Project Overview, session handoff, open issues register, Build Strategy, Specification Change History) have no version suffix in the filename — the session package zip acts as version marker.
**How to apply:** Check the Document Naming Index for the target document. If the current filename has a version suffix → new output must increment it. If no version suffix → no suffix in the output filename either.

---

## Rule 12: "Confirmed correct" files in a correction cycle must not be resubmitted
**Trace:** The SG Final Correction Notice for Items 42–43 explicitly stated: "Files to return: Specification_Change_History.md and Project_Overview.md only. All other 12 files are confirmed correct from the previous correction cycle and do not need to be resubmitted." The SG also stated this in the previous correction cycle notice: "Files confirmed correct and requiring no changes: 4 of 14."
**Rule:** When a correction notice specifies "files to return," only those files should be returned. Do not resubmit unchanged confirmed-correct files — it creates ambiguity about which version is authoritative.
**How to apply:** Read the correction notice "Files to return" section first. Build a set of exactly those files. Apply changes only to those files. Verify no accidental modification to other files (check file modification timestamps if possible).

---

## Rule 13: Apply changes to the SCN files list BEFORE applying content — establish file counts
**Trace:** The [first SCN] v2 specified version bumps for 6 files and one new file. Before applying 41 items across 8 passes, copying all source files to working names was critical. The container state resets between sessions, so the first operation after re-extracting files was to copy them to versioned output names. This ensured every str_replace targeted the correct versioned working copy.
**Rule:** Before applying ANY SCN changes, (1) extract/copy all source files to working directory, (2) create all output versioned files via copy (cp source → versioned_output), (3) verify all expected files are present with correct names. Only then begin str_replace operations on the versioned copies.
**How to apply:** `ls /home/claude/domain_system/` after extraction and copy. Verify every expected output filename exists. Run all str_replace operations against the output filenames (e.g., `project__DB_Construction_Specification_v1_1.md`), never against the original source filenames.

---

## Rule 14: After applying changes that touch the same concept in multiple SOPs, verify all referenced sections
**Trace:** [first SCN] Item 01 (D67) changed the external-code-to-domain mapping schema across process spec0, §12.10, and Annexes D.4.2.3/D.4.2.4. Multiple str_replace operations were required across different sections of the same file. After all changes were applied, a grep was needed to confirm zero residual `external_code` occurrences: `grep 'external_code' project__DB_Construction_Specification_v1_1.md → 0 results`. Without this, a missed occurrence in a remote section would not be caught.
**Rule:** After applying a schema-level rename (renaming a field name throughout a document), always run a grep for the OLD field name in the final output and verify the result is 0. Any non-zero result indicates a missed occurrence.
**How to apply:** The verification checklist in the SCN provides the definitive list of grep checks. Run every check in the verification checklist after all passes are complete. Do not skip checks because a section "looks clean" — the verification is not redundant, it catches things the human eye misses.

---

## Rule 15: The Scope Guardian's role is sovereign on corrections — even when the SCN was incomplete on their end
**Trace:** The [first SCN] v2 correction cycle noted explicitly: "Most stem from a propagation gap in the SCN itself — the version bumps were specified but the cascading cross-reference updates across the document set were not enumerated. This is a Scope Guardian oversight, not a Scope Editor error." The Scope Guardian issued the corrections and accepted responsibility for the oversight.
**Rule:** When a correction is issued, apply it exactly as specified regardless of whether the original SCN was at fault. The Scope Guardian's correction notice is authoritative. Do not argue that the SCN should have caught this — just apply the correction cleanly and acknowledge the gap in the application log if relevant.
**How to apply:** Read every correction notice fully before applying any change. Note the severity and scope. Apply in order. The Scope Editor's job is to apply accurately, not to adjudicate responsibility for the gap.

---

## Session metadata

See Document 3 (scope_editor_knowledge.md) for session metadata.
