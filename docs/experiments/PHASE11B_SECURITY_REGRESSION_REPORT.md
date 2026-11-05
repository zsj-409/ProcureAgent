# Phase 11B — Security Regression Report

- **Date**: 2026-11-05
- **Question**: can an unsafe Agent version bypass the Release Gate and
  become the active version?
- **Answer (verified by real execution)**: NO.

## The experiment

| candidate | real output | independent verifier | gate | promotion |
|---|---|---|---|---|
| baseline (greedy) | s1:6+s2:6+s3:3 = 162 | FULLY/PARTIAL, no violations | PASS | QUALIFIED → PROMOTED (active) |
| mutant A (hard budget) | s3×15 = 180 | INVALID: hard_budget_exceeded:180>162 | FAIL | REJECTED → blocked |
| mutant B (expired quote) | s1:6 (expired) | INVALID: line_quote_expired:s1 | FAIL | REJECTED → blocked |
| mutant C (inventory) | s1:7 (>stock 6) | INVALID: line_inventory_exceeded:s1 | FAIL | REJECTED → blocked |

## Why this is not a label game

- The mutants are REAL allocation strategies (test-only fixtures) whose
  outputs are fed to the verifier — the verifier recomputes every
  constraint from the problem + lines.
- The gate consumes the verifier's feasibility class, not the mutant's
  own claims.
- Promotion is a SQLite CAS transaction: the mutant (REJECTED) raises
  `NOT_QUALIFIED` and the active version stays `baseline-7`.

## Evidence chain (one manifest)

`phase11b_execution_manifest.json` links: mutant candidate_id → input_hash
→ world_hash → actual lines → verifier violations → gate verdict →
promotion decision → OTel span outcomes (candidate_execution=FAIL,
independent_verification=INVALID, release_gate=REJECTED,
promotion_rejection=BLOCKED).

## Boundary

- The mutation harness never modifies the production hard-budget check,
  the policy guard, or the verifier.
- A controlled mutation experiment is not a statistical guarantee of
  production safety; REAL_LLM_NOT_RUN persists.
