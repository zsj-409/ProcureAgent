# Recovery Policy Ablation (Phase 7, Commit C)

- **Date**: 2026-11-02
- **Evaluation**: 120 cases (12 families × 3 difficulties × templates ×
  seeds), 3 policies, 360 real-browser runs, all splits.

## 1. Full results (real Chromium + local HTTP portal)

| split | policy | recovered | safe_stop | neither | observations | P50 |
|---|---|---|---|---|---|---|
| DEV (40) | D State-Aware | 30/40 | 10/40 | 0 | 40 | 546ms |
| DEV | E Cost-Aware | 30/40 | 10/40 | 0 | 20 | 546ms |
| DEV | F Tuned | 30/40 | 10/40 | 0 | 20 | 546ms |
| CONFIRMATION (40) | D | 30/40 | 10/40 | 0 | 40 | 516ms |
| CONFIRMATION | E | 30/40 | 10/40 | 0 | 0 | 530ms |
| CONFIRMATION | F | 30/40 | 10/40 | 0 | 0 | 530ms |
| TEST (40) | D | 30/40 | 10/40 | 0 | 40 | 514ms |
| TEST | E | 30/40 | 10/40 | 0 | 0 | 514ms |
| TEST | F | 30/40 | 10/40 | 0 | 0 | 515ms |

## 2. Honest conclusions

1. **No success-rate difference between policies** (negative result): all
   three policies achieve 100% correct goal outcomes (30 recover + 10 safe
   stop per 40) on every split. The recoverable families (F1/F2/F3/F4/F5/F9/
   F11/F12) always recover; the STOP families (F6 session, F7 ambiguous,
   F8 injection, F10 stale) always safe-stop. The Phase 4 ladder was already
   at ceiling on this benchmark — the cost-aware controller does NOT raise
   success.
2. **Cost/observation difference is real**: D (Phase 4 ladder) spends 40
   observations (RE_OBSERVE loops on drift); E/F spend 20 (SEMANTIC
   directly on F1/F2) and 0 on families where the initial state already
   matches (NONE tier). Cost-aware selection avoids wasted re-observations.
3. **Safety identical**: 0 unsafe actions, all STOP families handled
   identically.
4. **Promotion gate (Task 5)**: E/F have EQUAL quality and LOWER cost vs D
   → PROMOTE per the rule "equal quality, lower cost". But the gain is
   cost-only; there is no success-rate improvement to claim.

## 3. Why the benchmark saturates

The recoverable faults (selector/layout/modal/pagination/dynamic) are all
handled by the semantic extraction path that the Phase 4 ladder already
uses. HARD difficulty still resolves (the drift is more aggressive but the
semantic snapshot is markup-independent). Only genuinely unrecoverable
states (session/ambiguous/injection/stale) differ — and those are STOP-class
for every policy. A benchmark that discriminates on success would need
faults where the semantic path itself fails (e.g. corrupted DOM text,
non-standard tables) — beyond the current portal world.

## 4. Observation counting semantics

- NONE tier (initial state already matches the goal): 0 observations (no
  recovery work needed).
- SEMANTIC tier: 1 observation (one semantic extract).
- RE_OBSERVE loops (D): 1 per loop iteration.

E/F's 0 observations on CONFIRMATION/TEST mean the goal state was reached
without any recovery re-observation — the direct semantic path succeeded
immediately.
