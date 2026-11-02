# Recovery OOD Report (Phase 7, Commit C)

- **Date**: 2026-11-02

## 1. OOD generalization (policy level)

The cost-aware policy's input (`RecoveryObservation`) contains NO template
field — decisions are a function of semantic state + fault hint + budgets
only. Tests verify the policy yields identical decisions for identical
observations regardless of the portal template that produced them. The
t2_ood template (Phase 4, unseen: Item/Amount/Availability/Lead Time
headers, ood-* ids, no search form) is a genuine out-of-distribution portal;
the semantic classifier and extractor were validated on it in Phase 4, and
the policy layer adds no template-specific logic.

## 2. TEST split (frozen, held-out)

TEST families (F7 ambiguous, F10 stale quote, F11 delayed rendering,
F12 multi-sequential faults) were never used for tuning. Results:

| policy | recovered | safe_stop | neither |
|---|---|---|---|
| D | 30/40 | 10/40 | 0 |
| E | 30/40 | 10/40 | 0 |
| F | 30/40 | 10/40 | 0 |

- F7 (ambiguous product): 10/10 safe stops (never picks the wrong product).
- F10 (stale quote): 10/10 safe stops (never approves a stale quote).
- F11 (delayed rendering): 10/10 recovered (semantic re-locate).
- F12 (multi-sequential faults): 10/10 recovered.

## 3. Statistical limits

- n=40 per split per policy (4 families × 3 difficulties × ~3.3
  template/seed combos). Family-level conclusions are exploratory; the
  paired per-case design (same case across policies) controls for template
  variance.
- Success saturation prevents significance testing on success; the
  observation-count difference (20 vs 40 on drift) is descriptive.

## 4. Safety on OOD

- 0 unsafe actions on TEST.
- No policy observed the hidden fault label (fault_hint derived from
  observable signals; verified by the no-hidden-field test).
- Fresh browser context + fresh portal world per case; snapshot hashes
  recorded.
