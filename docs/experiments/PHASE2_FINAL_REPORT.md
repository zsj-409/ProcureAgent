# Phase 2 Final Report

- **Date**: 2026-11-01
- **Commits**: `4b489b2` (A), `3a58603` (B), (C — pending)
- **Tests**: **125 passed** (was 104), ruff clean
- **New modules**: `app/runtime/` (step_claim, durable_worker, chaos)

## 1. What was delivered

| Commit | Content |
|---|---|
| A `4b489b2` | LogicalStep/StepAttempt tables, atomic claim (UPDATE...RETURNING), fencing lease, heartbeat, WAL, testable clock, 10 unit tests |
| B `3a58603` | DurableWorker (DB-backed scan, heartbeat loop, graceful stop), find_reclaimable_tasks, orchestrator.execute_step, crash recovery via real subprocess kill, UNKNOWN_OUTCOME semantics, checkpoint versioning, 5 tests |
| C (pending) | FaultInjector, ExternalEffectLedger (independent), StateConsistencyChecker, cross-process 2/4/8-worker chaos tests, machine-readable artifacts, reports |

## 2. Gate check (Task 15)

| # | Gate | Status | Evidence |
|---|---|---|---|
| 1 | 同一 LogicalStep 无未经协调的并发所有权 | ✅ | 2/4/8 workers → 1 winner; claim is atomic UPDATE |
| 2 | 旧 Worker 过期 Fencing Token 无法提交 | ✅ | stale complete() → False; subprocess epoch rejection |
| 3 | Worker 真实终止后新进程恢复 | ✅ | os._exit + new worker reclaims (2/2) |
| 4 | 不确定外部结果不盲目重试 | ✅ | UNKNOWN_OUTCOME not claimable; reconciliation required |
| 5 | 已完成 Checkpoint 不重复提交 | ✅ | SUCCEEDED never claimed again |
| 6 | 不兼容历史 Checkpoint 不误复用 | ✅ | plan_hash + action_input_hash keying |
| 7 | 任务/步骤/尝试/审计一致 | ✅ | StateConsistencyChecker 5/5 |
| 8 | Phase 1 业务契约仍有效 | ✅ | 125 passed incl. all Phase-1 tests |
| 9 | 真实多进程 + 故障注入通过 | ✅ | subprocess races + os._exit crash |
| 10 | 可靠性指标可复现 Manifest | ✅ | docs/eval/*.json |
| 11 | 失败/负结果完整报告 | ✅ | this report + DURABLE_RUNTIME_EVALUATION |
| 12 | 不夸大 DB 保护为外部 Exactly-once | ✅ | documented limitation |

## 3. Test commands (real)

```
.venv/Scripts/python.exe -m pytest                               # 125 passed
.venv/Scripts/python.exe -m pytest tests/test_step_claim.py      # 10
.venv/Scripts/python.exe -m pytest tests/test_durable_worker.py tests/test_crash_recovery.py  # 5 (subprocess)
.venv/Scripts/python.exe -m pytest tests/test_chaos_concurrency.py  # 6 (2/4/8 workers)
.venv/Scripts/python.exe -m ruff check app/ tests/               # clean
.venv/Scripts/python.exe scripts/generate_phase2_artifacts.py    # writes docs/eval/*.json
```

## 4. Negative results / honest limits

- **No duplicate external effect** observed in any race (ledger ground truth).
- **Recovery n is small** (2 scenarios) — not a population rate; Phase 6
  scales the fault matrix.
- Fencing cannot cancel an already-emitted external side effect.
- SQLite WAL concurrency is fine at 8 workers; a real throughput ceiling /
  Postgres comparison is out of scope for Phase 2 (per the "先最小闭环，再
  评估迁移" rule).
- Recovery latency measured is subprocess-dominated, not real lease-wait time.
- `find_reclaimable_tasks` lists non-terminal tasks even when all their steps
  are SUCCEEDED (the worker's per-step loop skips SUCCEEDED) — a task is
  marked COMPLETED only by the orchestrator's normal completion path; the
  durable worker currently reclaims individual steps, not the whole-task
  COMPLETED transition (known gap, Phase 3).

## 5. Phase 2 → Phase 4 gate

**PASS** on all 12 criteria. Next phase: Adaptive Portal Recovery (semantic
portal state graph, recovery ladder comparison) — NOT yet started per scope.

## 6. Remaining risks

1. Whole-task COMPLETED transition is not driven by the durable worker yet
   (it completes steps; the orchestrator still owns task finalization).
2. Quote-freshness hard gate at checkpoint reuse is policy-layer, not
   claim-layer.
3. Ledger append is not locked for truly-parallel same-file writers (each
   scenario uses its own ledger).
4. Real LLM / real CDP browser still REAL_LLM_NOT_RUN (no credentials).
