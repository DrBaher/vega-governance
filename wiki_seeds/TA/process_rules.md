# Process Rules — Tests Auditor
## Seed — to be populated as TA accumulates learnings

## Rule 1: Read the spec section before judging the test case
A test that seems wrong may correctly reflect non-obvious spec behavior. Read the cited Spec ref first.

## Rule 2: Check test cases against test fixture dataset fixture
Tests reference entries by client_code. Verify referenced entries exist and have claimed properties.

## Rule 3: Verify expected outputs are deterministic
"Should include X" is testable. "May include X or Y" needs to be split into two test cases.

## Rule 4: Audit build version derivation
Build version = exact subset of full version with criteria stripped. Structural differences are a finding.

## Rule 5: Check cross-level dependencies
L1 assumes L0 passes. L2 assumes L1. If higher-level expected output contradicts lower levels, something is wrong.

## Rule 6: Every finding needs spec trace
Cite specific test case IDs AND scope requirement references. Findings without trace are incomplete.
