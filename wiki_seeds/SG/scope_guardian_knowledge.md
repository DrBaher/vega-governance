# Scope Guardian Knowledge

## Cross-Reference Traps (project-specific — populate at initialization)
Document which sections across the specification set are coupled but appear independent.

## Decision Interpretation Subtleties (populate as decisions accumulate)
Document decisions where the intent is non-obvious from the text alone.

## Domain Terminology Pitfalls (populate from domain)
Document terms that have different meanings in different contexts within the project.

## Expert Interaction Patterns (seed traced)
- Domain experts may provide both narrative text and structured data (xlsx). Always cross-check item by item — they can contradict.
- When the expert's decision depends on understanding system behavior, confirm they understand the downstream consequence.
- The expert defines domain correctness. When their finding conflicts with current configuration, update the configuration, not the expert.
