# Audit Methodology — Scope Auditor

## Audit Order
1. Read wiki index.md + SCHEMA.md
2. Read each scope document end-to-end (not diffs)
3. Cross-reference checks between documents
4. Domain validation (domain standard, domain standard, domain)
5. Produce numbered findings FND-SA-NNN

## What to check per document
- Internal consistency: terms used consistently, cross-refs correct
- Architectural decisions: not contradictory (check D01-[decision]+ log)
- Domain accuracy: domain standard classifications, domain facts, unit mappings
- Completeness: referenced mechanisms exist, no dangling pointers
- Version alignment: all cross-document version references are current

## Known cross-reference traps
- process spec.3 (build quality checks quality checks) ≠ SOP-16 (domain plausibility). Different SOPs, different mechanisms.
- §3.9 Catalog Validator (exact assembled_code) ≠ §3.10 Code Selector (compatibility matching, UNASSIGNED = wildcard)
- [decision] (97 classes) and [decision] (H&P.HX excluded, H&P.HX.LAB included)
- §5.6 "exact lookup" means different things per field (tube/unit → mapping tables; specimen/method → reference databases)
