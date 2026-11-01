# Task-Level Durability (Phase 3)

- **Module**: `app/agent/orchestrator.py` (`reconcile_task_state`),
  `app/runtime/durable_worker.py`
- **Commit**: `21b27fa`

## 1. Full-task recovery path

The durable worker now drives the WHOLE task, not just single steps:

```
DurableWorker._run_task
  ├─ for each plan step: create logical step → claim → execute (fenced)
  └─ reconcile_task_state(task_id)   ← NEW
       ├─ re-validate persisted plan (Phase 1 gate)
       ├─ rebuild quotes from SUCCEEDED step results (no re-contact)
       ├─ validate → score → policy → finalize (deterministic)
       └─ mark COMPLETED / WAITING_APPROVAL / FAILED
```

## 2. Deterministic terminal states

- All collect steps SUCCEEDED + policy auto-approves → `COMPLETED`.
- Shortfall / budget-exception / above-threshold → `WAITING_APPROVAL`
  (NOT treated as completed).
- No usable quotes → `FAILED`.
- Invalid persisted plan → `FAILED` (never executes).

## 3. Idempotent finalize

`reconcile_task_state` returns immediately when the task is already
COMPLETED / WAITING_APPROVAL / REJECTED / FAILED — no double finalize, no
re-scoring a completed task.

## 4. Guardrails (prevented)

| hazard | guard |
|---|---|
| step SUCCEEDED but task stuck RUNNING | reconcile drives terminal state |
| task double-finalized | terminal status early-return |
| old-version quotes enter new plan | plan_hash + action_input_hash keying |
| failed step → COMPLETED | only SUCCEEDED steps contribute quotes; empty → FAILED |
| WAITING_APPROVAL mistaken as done | policy result decides; WAITING_APPROVAL is a distinct terminal |

## 5. Verified end-to-end (test_full_task_recovery)

Child process: seed task+plan → execute collect step 1 (external effect #1)
→ `os._exit(0)`. New worker: reclaim step 2 (external effect #2) → reconcile
→ task lands in **WAITING_APPROVAL** ($99.99 × 20 over the $1000 threshold),
recommendation has 2 quotes, approval_required=True. Invocation count = 2,
no duplicates.
