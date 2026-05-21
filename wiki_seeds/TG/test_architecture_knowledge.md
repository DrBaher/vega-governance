# Test Architecture Knowledge — Tests Guardian

## Test Catalog (test fixture dataset)
- 124 entries (V1), built from [client] data + synthetic entries
- Covers: simple matches, multi-language-specific, panels, edge cases, multi-axis

## Level Dependencies
- L0: Data/reference DB verification (foundational data, reference databases, Panels, etc.)
- L1: System sub-components (tokenization, n-gram, match selection)
- L2: Integration (Catalog Validator, Code Selector, end-to-end)
- L3: Pipeline (9-step architecture, catalog lifecycle)
- PC: Processing/Compute (extraction, mapping, performance, API)

## Test ID format: L[level].[section].[sequence]

## 7 test spec files
01: L0 Data Part 1 | 02: L0 Data Part 2 | 03: L0 Data Addendum
04: L1 System | 05: L1/L2/L3 Addendum | 06: L2/L3 Pipeline
07: Processing/Compute

## Common test specification errors
- domain identifiers from memory (always verify)
- Missing explicit nulls in input fields
- Expected outputs assuming unimplemented behavior
- Gap tests less rigorous than originals
- CONDITIONAL vs ABSOLUTE failure classification

## Input format (all 7 fields, explicit nulls)
{ test_name, specimen, tube, units, method, synonyms, lab_code }
