# Recovery Policy Frontier (Phase 7, Commit B)

- **Date**: 2026-11-02
- **Pilot**: DEV split, 40 cases (F1/F2/F5/F6 × 3 difficulties × templates × seeds)

## 1. Pilot results (real Chromium, DEV)

| policy | n | verified recovery | safe stop | neither | observations | P50 |
|---|---|---|---|---|---|---|
| D State-Aware | 40 | 30/40 | 10/40 | 0 | **40** | 530ms |
| E Cost-Aware | 40 | 30/40 | 10/40 | 0 | **20** | 532ms |
| F Tuned Cost-Aware | 40 | 30/40 | 10/40 | 0 | **20** | 532ms |

All recover_quote goals recovered (F1 selector drift 10/10, F2 layout drift
10/10, F5 modal 10/10); F6 session expiration correctly SAFE-STOPPED 10/10.

**Discrimination on the DEV pilot is in COST, not success**: E/F need half
the observations of D (20 vs 40) because the cost-aware policy goes
SEMANTIC immediately on selector/layout drift, while the Phase 4 ladder
spends repeated RE_OBSERVE. Success is saturated at 100% on these simple
faults.

## 2. Frontier interpretation

- On EASY/MEDIUM drift faults, all policies succeed; the frontier is
  cost/observations (E/F win).
- On HARD / multi-fault / stale-quote / ambiguous cases (CONFIRMATION and
  TEST splits), success rate is expected to diverge — the next commits
  evaluate that.
- Session-expiration / ambiguous / unsafe are STOP-class: no policy may
  recover them (verified 10/10 safe stops).

## 3. Safety

- 0 unsafe actions across all policies (all actions read-only whitelist).
- No policy observed the hidden fault label (fault_hint derived from
  observable signals only).
- Fresh browser context + fresh portal world per case.

## 4. Promotion gate (Task 5)

F (tuned) shows equal quality + lower cost vs D on DEV. Promotion is
decided on CONFIRMATION (independent): if F keeps quality and lowers cost
there, it is promoted; if quality regresses or cost ties, it is HOLD/REJECT
per the rules in `tests/test_phase7_tuning.py`.

## 5. Honest note

The DEV pilot is a diagnostic (n=40, 4 families). The success saturation
means DEV alone cannot rank policies; the confirmation/test splits with
harder faults carry the decisive evidence.
