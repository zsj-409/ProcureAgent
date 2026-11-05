# Phase 11B — Real Candidate Regression Spec (Commit A)

- **Date**: 2026-11-05
- **Protocol**: real candidate execution + independent verification — no
  pre-filled FAIL labels.

## The problem

Phase 10's unsafe-candidate demo fed **hand-written measurements** into the
Release Gate. That proves the gate rejects a labelled violation, but it does
NOT prove the gate can **discover** a regression in a real candidate policy.

## The fix: Candidate Mutation Harness

`app/evalops/mutation.py` runs a REAL candidate (the existing weighted-greedy
allocation) and three TEST-ONLY mutants, each in an ISOLATED world (fixed
seed 7 → identical quote snapshot; explicit hard budget 162; allowlist
s1+s2+s3; s3 stock 15):

| candidate | mutation | real output | independent verifier |
|---|---|---|---|
| baseline | none (real greedy) | s1:6+s2:6+s3:3 = 162 | FULLY/PARTIAL, no violations → **PASS** |
| mutant_a | allocate full quantity to most expensive (s3 ×15) | 180.00 | `hard_budget_exceeded:180.00>162` → **FAIL** |
| mutant_b | allocate from expired quote (s1 valid_until −1h) | s1:6 | `line_quote_expired:s1` → **FAIL** |
| mutant_c | allocate beyond stock (s1:7) | 70.00 | `line_inventory_exceeded:s1` → **FAIL** |

## Independence

`AllocationConstraintVerifier` recomputes **every** constraint from the
problem + the candidate's ACTUAL allocation lines:
requested/allocated/shortfall, total landed cost, hard budget, quote
expiry, currency comparability, supplier eligibility, inventory, delivery
deadline, split limit. It never reads candidate-provided
`is_valid`/`feasible`/`verdict`.

## Fail-closed evidence

Every rejection saves: input hash, candidate version, actual candidate
output (lines), original quote snapshot, independent constraint result,
violation reason, gate result, world hash.

## Honest boundary

- Mutants are TEST-ONLY fixtures; production hard-budget check, policy
  guard, and the verifier are never modified.
- A controlled mutation experiment is NOT a statistical guarantee of
  production safety.

## Artifacts (docs/eval/)

- `candidate_mutation_manifest.json`
- `candidate_execution_results.json`
- `independent_constraint_results.json`
- `release_gate_decisions.json`

## Tests

`tests/test_phase11b_mutation.py` (8): baseline legal, each mutant detected,
verifier ignores candidate claims, isolated worlds, evidence fields
complete, production policy untouched.
