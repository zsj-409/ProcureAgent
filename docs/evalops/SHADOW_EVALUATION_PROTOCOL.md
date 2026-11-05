# Phase 10 — Shadow Evaluation Protocol

- **Date**: 2026-11-02
- **Commit C**: (pending)

## Principle

The SAME procurement request and supplier snapshot are decided by the
baseline AND the candidate policy — independently, on ISOLATED worlds. The
candidate never touches real state.

## Hard rules

- Candidate does NOT write real task state, approvals, orders, or payments.
- Candidate does NOT mutate the baseline's world (each method gets its own
  immutable snapshot / fresh reset).
- Candidate tool calls go through the secure MCP gateway — shadow mode is
  NOT a bypass of auth/scopes/ownership.
- Every method's output is verified by the INDEPENDENT constraint verifier.

## Recorded per shadow run

| field | meaning |
|---|---|
| candidate_id / baseline_id | which versions were compared |
| world_hash | immutable snapshot identity |
| baseline_decision / candidate_decision | verified, cost, feasibility |
| independent_constraint_result | verifier verdict |
| verified_completion_difference | same / diff |
| cost_difference | numeric delta |
| safety_difference | none / detected |
| trace_ids | OTel trace correlation |
| runtime_ms | wall time |
| isolated | always true (invariant) |

## Implementation

`app/evalops/core.py: ShadowEvaluator` + `app/evalops/shadow_demo.py`
(runs the real GreedyAdapter vs OptimizerAdapter on a frozen
multi_supplier_joint world). Artifact:
`docs/eval/shadow_evaluation_results.json`.

## Honest boundary

- Shadow decisions are recommendations for the release gate — they are not
  production executions and not a substitute for the real business path.
- The world is a synthetic frozen fixture (as all Phase 4-7 worlds are).
