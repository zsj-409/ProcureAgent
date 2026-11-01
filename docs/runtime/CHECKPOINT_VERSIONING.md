# Checkpoint Versioning (Phase 2)

- **Modules**: `app/runtime/step_claim.py` (hash helpers),
  `app/infrastructure/database.py` (`save_step_result`)

## 1. What a checkpoint binds

A completed logical step's checkpoint is keyed by:

| component | source |
|---|---|
| `task_id` | task |
| `plan_hash` | content hash of the validated plan |
| `step_key` | logical step id (`collect_<supplier>` etc.) |
| `action_input_hash` | hash of (product_name, quantity, supplier_id, extra) |
| `result_ref` | reference to the completed result |
| `status` | SUCCEEDED (only successful steps are reusable) |

## 2. Reuse rules

- A step is reused ONLY when the SAME task + plan_hash + step_key + 
  action_input_hash is SUCCEEDED.
- A NEW plan version (plan_hash change) never reuses old results — a replan
  with a different supplier set produces different step_keys/hashes.
- Changing supplier / product / quantity changes `action_input_hash` → old
  results invalid.
- Quote freshness: a completed quote's `collected_at` is re-checked before
  trust; expired quotes are not auto-reused (Phase 1 contract).

## 3. Enforcement points

- `StepClaimService.create_logical_step` keys on (task_id, plan_hash, step_key)
  with ON CONFLICT DO NOTHING — a new plan version creates new rows, never
  reuses old SUCCEEDED rows for a different plan.
- The claim WHERE never accepts SUCCEEDED → no double completion.
- `save_step_result` persists the normalized quote under the step's result
  ref for audit; a resumed worker reads it back without re-contacting the
  supplier ONLY when the checkpoint keys match.

## 4. Migration / audit preservation

- Existing `step_executions` rows are preserved (additive migration; no DROP).
- `logical_steps` / `step_attempts` are new tables created via
  `CREATE TABLE IF NOT EXISTS`; old audit info is untouched.
- Upgrade test: re-running on an old DB keeps all rows and creates the new
  tables (covered by the suite's normal startup path).

## 5. Known limitation

- A SUCCEEDED quote checkpoint is trusted for reuse within the same
  plan+input hash even if the supplier's live price changed since collection;
  freshness re-validation (quote_valid_until) is enforced at the policy/scorer
  layer, not yet as a hard checkpoint gate (Phase 2/3 boundary).
