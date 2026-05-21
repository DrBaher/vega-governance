# Scope Editor Knowledge

## Document Structure Conventions (populate at initialization)
Document formatting conventions, header styles, version bump procedures for each document in the specification set.

## Known Propagation Chains (seed traced)
When field X is renamed, these documents and sections reference it: [populate per project].
Pattern: every rename or restructure requires grepping ALL documents. Four audit rounds each found propagation gaps from the previous round.

## Version Management (seed traced)
- Version headers are NOT automatically updated by content changes. Always check lines 1-5 after applying changes.
- Cross-document version references: grep for the old version string across ALL files after a bump.
- Historical references in changelogs stay as-is. Only live references update.

## SCN Application Patterns (seed traced)
- Apply in specified pass order. Schema before structure before examples before cross-references.
- When SCN says "current text" and it doesn't match: STOP. Report to SG. Do not guess.
- Secondary consistency fixes (propagation beyond what SCN explicitly lists): apply but flag in DOC-SE-NNN.
