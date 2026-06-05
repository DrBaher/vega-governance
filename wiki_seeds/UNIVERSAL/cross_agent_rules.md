# Universal Rules — All Agents

Applies to every agent. Each traced to a real failure. See architecture §3 for full text.

## U1-U10 Summary
U1: Verify before applying | U2: Read end-to-end not diffs | U3: Trace = open files | U4: No domain standard IDs from memory | U5: Break comfortable patterns | U6: Sequential vs competing | U7: Compression produces confabulation | U8: Existing mechanisms first | U9: Disambiguate overloaded project terminology | U10: Propagation through ALL documents

## Spec Interpretation — Cross-Agent

### Overloaded terminology (CRITICAL — RC-05, RC-06)
When a project reuses the same term for distinct concepts, every agent and build will confuse them. Identify overloaded terms early and maintain a disambiguation table below. Each usage must be explicitly named whenever referenced.

> *Example from a prior project:* "Phase 2" was used for three distinct concepts: (1) System Phase 2: product LLM modes A/B/C running on deployment platform, (2) Build Phase 2: DB construction quality checks with 5 sub-steps, (3) Phase 2 of corrections: next validation cycle after Phase 1 passes. Agents and build confused them repeatedly until each was explicitly named.

[Project-specific entries below this line]

### Document aliases
Projects may use shorthand names for specification documents ("Document A", "the config doc"). Maintain a mapping table here so every agent resolves the same alias to the same document. Ambiguous references cause wrong-document errors.

> *Example from a prior project:* "Document A" = data specification (SOPs). "Document B" = Configuration v1.0 (parameters, class list).

[Project-specific entries below this line]

### Default scoring for incomplete data
When data entries are incomplete (not all fields populated), define the scoring rule clearly: do incomplete entries score on the fields they have, or are they penalized? Ambiguity here causes inconsistent validation.

> *Example from a prior project:* PARTIAL entries score 1.0 on axes they have. Missing axes are not penalized — they are simply absent from the score.

[Project-specific entries below this line]

### Status flag semantics
When a manual override flag is introduced (e.g., operator-confirmed, expert-approved), define its lifecycle: can it be rolled back? What triggers rollback? Who has authority? Flag semantics that are implicit get misused by build.

> *Example from a prior project:* OPERATOR_CONFIRMED was rolled back after discovering the flag bypass mechanism was being used without proper review. Rule: override flags require explicit lifecycle definition.

[Project-specific entries below this line]

### Active documents (authority)
Maintain an authoritative list of all active specification and management documents with current version numbers. This is the single source of truth for which documents are live. Any document not on this list has no authority. SG maintains this list via the document manifest.

> *Example from a prior project:* scope specification.2, data specification v1.1, technical specification v4.5, Client API v3.5, Phase 2 Config v1.1, Internal_DB_Construction_Tooling v1.0, Build Strategy, open issues register, session handoff, Product Walkthrough, Project Audit Archive, Project Overview, Spec Change History, Domain Expert Answers Gap Analysis.

[Project-specific: populated at initialization by SG. Updated with every version bump.]

### Display names ≠ authoritative assignments
Domain reference data is the ONLY authority for identifier assignments — not display names, common usage, or LLM training data. When a display name suggests one mapping but the reference data says another, the reference data wins.

> *Example from a prior project:* LCN text (display name) ≠ Part assignment (authoritative). The display name suggested a mapping that the reference data contradicted. The reference data was correct.

[Project-specific entries below this line]

### Dimensional analysis resolves some ambiguities, not all
When the domain uses units or measurements, some mappings are deterministic (physics determines the relationship). Others are genuinely ambiguous (multiple valid interpretations). Distinguish the two — don't assume all mappings require expert review when some are resolvable by analysis, and don't assume all are resolvable when some genuinely need expert judgment.

> *Example from a prior project:* [unit] = [property] always (physics). mmol/L = [property] always. Genuinely ambiguous: %, ratio, titer — these required expert review.

[Project-specific entries below this line]

### Official vs derived reference data
When the project uses multiple reference data sources, classify each as either official (primary authority, loaded directly) or derived (mapped/translated from another source). The classification determines trust level and update rules. Misclassifying derived data as official causes silent errors when the source changes.

> *Example from a prior project:* Biologie FRA and JDV were classified as domain-standard-official (Layer 1, loaded directly), NOT as terminology-mapped (Layer 2+). This distinction affected which pipeline stage handled them.

[Project-specific entries below this line]
