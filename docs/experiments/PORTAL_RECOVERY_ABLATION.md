# Portal Recovery Ablation (Phase 4, Commit C)

- **Date**: 2026-11-01
- **Commit C**: independent portal evaluation (5-method comparison + ablation)

## 1. Methods compared

| id | method | script | heuristic | LLM | state-aware |
|---|---|---|---|---|---|
| A | Script Only | ✅ | — | — | — |
| B | Heuristic Only | — | ✅ | — | — |
| C | LLM First | — | — | ✅ (SCRIPTED MOCK) | — |
| D | Current Ladder | ✅ | ✅ | — | — |
| E | State-Aware Ladder | ✅ | ✅ | — | ✅ |

C is a SCRIPTED mock of an LLM policy (not a real model) — **REAL_LLM_NOT_RUN**.
All real-LLM comparisons are deferred until credentials exist.

## 2. Fairness

Same task across methods: same world (ground truth catalog, template version,
fault, fault seed), same task input, same maximum action budget, same time
budget, same safety policy. Each method runs on a FRESH browser context and a
FRESH world (environment reset; snapshot hash recorded). No method mutates a
shared environment for another.

Order invariance: the benchmark iterates methods × faults in a fixed order;
each case rebuilds its world, so execution order cannot leak state.

## 3. Metrics

Per method, per fault: verified success (ground-truth judged), recovery
success, safe containment, quote extraction accuracy, invalid/unsafe action
count, browser actions, LLM calls, tokens, cost, latency P50/P95, failure
type distribution. All ratios carry explicit n.

## 4. Results (real browser, n=7 per method, 35 runs)

| method | verified | recovery | containment | quote | LLM | cost | P50 |
|---|---|---|---|---|---|---|---|
| A Script Only | 6/7 | 5/7 | 0/7 | 6/7 | 0 | 0 | 546 ms |
| B Heuristic Only | 7/7 | 6/7 | 0/7 | 7/7 | 0 | 0 | 859 ms |
| C LLM First (MOCK) | 7/7 | 6/7 | 0/7 | 7/7 | 21 | 0.42 | 875 ms |
| D Current Ladder | 7/7 | 6/7 | 0/7 | 7/7 | 0 | 0 | 750 ms |
| E State-Aware | 7/7 | 6/7 | 0/7 | 7/7 | 0 | 0 | **484 ms** |

Full per-case data: `docs/eval/strategy_comparison.csv`,
`docs/eval/portal_recovery_results.json`, `docs/eval/portal_failure_cases.json`.

### Key honest conclusions

1. **A (Script Only) fails on F2 LAYOUT_DRIFT** (cards layout has no table;
   `tr[data-sku]` selector cannot find anything) → 6/7. This is the classic
   selector-drift failure the adaptive layers exist to fix.
2. **B/D/E all reach 7/7** — the heuristic semantic loop is already very
   strong on this fault set.
3. **E (State-Aware) does NOT beat D on success rate** — a genuine negative
   result on this fault set. Its demonstrated independent gains are:
   - **P50 latency −35%** (484 vs 750 ms): the semantic state gate lets E
     act immediately without the fixed-script first attempt and retries;
   - **safety validation before every action** (state check + action
     contract + postcondition) which D lacks.
4. **C (LLM First, SCRIPTED mock)**: 21 mock LLM calls, $0.42 mock cost, no
   success gain over zero-cost methods → LLM escalation shows **no benefit
   on this fault set** (real LLM not run; REAL_LLM_NOT_RUN).
5. The test set was NOT modified to manufacture significance; the negative
   E-vs-D result is recorded as-is.

### Failure-type distribution (per method)

| method | failure types (n) |
|---|---|
| A | F2_LAYOUT_DRIFT (1) |
| B | none |
| C | none |
| D | none |
| E | none |

All non-successes are SAFE_CONTAINMENT-classified (F5 session / F8 unsafe)
in the pilot; in the benchmark the fixed-script failures fall back to
heuristic so only A shows a real failure.

## 5. Honest framing

- If State-Aware (E) is NOT better than Current Ladder (D), that is reported
  as a negative result with an analysis (page faults too simple? state
  features not discriminative? recovery cost too high? policy choice no
  effect?). The test set is NOT modified to manufacture a significant result.
- LLM First (C) results are MOCK/SCRIPTED and never presented as real-LLM
  evidence.

### Ablation unit tests (`tests/test_phase4_ablation.py`)

- Without semantic recognition → blind retry (no SEMANTIC_LOCATE path).
- Without postcondition verification → wrong page accepted as success
  (verified: POSTCONDITION_FAILED catches it when enabled).
- Without budget-aware escalation → infinite retry (max_actions is the stop).
- LLM call budget enforced (max_llm_calls).
All 4 pass.
