# Reasoning Corrections — Tests Editor
## Seed — to be populated as TE accumulates corrections

Initial patterns inherited from SE experience:

## RC-01 (pattern): Changelog order can break when inserting entries
When adding new test cases to a file that already has tests, read the full current block before writing. Verify sequence order is maintained.

## RC-02 (pattern): Cross-references across ALL test files must be updated
A test model version bump requires searching for live cross-references across ALL test spec files plus the Test Execution Guide. Historical references (in change logs) stay as-is.
