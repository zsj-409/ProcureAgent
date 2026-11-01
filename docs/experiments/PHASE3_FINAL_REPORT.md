# Phase 3 Final Report

- **Date**: 2026-11-01
- **Commits**: `581be71` (A: external effect safety), `21b27fa` (B: task-level
  durability), (C: approval integrity + chaos)
- **Tests**: **144 passed** (was 125), ruff clean

## 1. What changed

| Commit | Content |
|---|---|
| A `581be71` | capability contract (ExternalCapability/RetryPolicy), SQLite transactional effect ledger (invocations vs committed, idempotent dedup), capability-aware worker recovery (UNKNOWN_OUTCOME for non-idempotent), claim-scope tests |
| B `21b27fa` | `reconcile_task_state` (full-task terminal state from persisted results, idempotent finalize), worker drives whole task, cross-process full-task recovery test |
| C (pending) | approval CAS (concurrent approve/reject single winner), quote-expiry gate, version binding, approval integrity tests, chaos results + reports |

## 2. Gate check (Task 12)

| # | Gate | Status | Evidence |
|---|---|---|---|
| 1 | Claim 不误匹配其他 Step | ✅ | expired lease of B not claimed via A (SQL scope + test) |
| 2 | 旧 Worker 无法覆盖新结果 | ✅ | fencing tests (2/2) |
| 3 | 只读重试准确统计重复调用 | ✅ | ledger invocation=2, committed=0 |
| 4 | 幂等重复调用只产生一个业务效果 | ✅ | idempotent crash → committed=1 |
| 5 | 非幂等未知结果不盲目重试 | ✅ | UNKNOWN_OUTCOME, no repeat |
| 6 | 账本多进程不丢记录 | ✅ | 4×20 writes = 80, no lost update |
| 7 | 真实进程终止后完整任务安全恢复 | ✅ | full-task kill → WAITING_APPROVAL |
| 8 | Task/Step 状态一致 | ✅ | StateConsistencyChecker all pass |
| 9 | 审批与方案版本绑定 | ✅ | recommendation_version on approval row |
| 10 | 未授权/过期审批不能生效 | ✅ | 401 AUTH_REQUIRED; 409 QUOTE_EXPIRED |
| 11 | 硬预算和 Phase 1 契约无回归 | ✅ | 144 passed incl. all Phase-1 tests |
| 12 | 独立测试和故障注入数据可复现 | ✅ | committed manifests + tests |
| 13 | 成功/失败/不确定口径明确 | ✅ | metrics registry, n per family |
| 14 | Safe Containment 不等于 Task Success | ✅ | UNKNOWN_OUTCOME labeled separately |
| 15 | 本地 Fencing 不夸大远端 Exactly-once | ✅ | documented limitation |

## 3. Negative results / limits

- **No duplicate committed business side effect** in any Phase-3 scenario
  (ledger ground truth).
- Recovery n per family is small (1-5); NOT a population rate. Phase 6 scales.
- Demo approval identity is a controlled header, not production auth.
- Cross-tenant enforcement beyond the single demo tenant not exercised.
- Ledger is SQLite (transactional) — good; a true distributed ledger / queue
  is out of scope (per "先最小闭环" rule).
- Non-idempotent tools have NO automated recovery path by design (human
  review / reconciliation is the terminal state) — a deliberate safety choice.

## 4. Commands

```
.venv/Scripts/python.exe -m pytest                                   # 144 passed
.venv/Scripts/python.exe -m pytest tests/test_external_effect_safety.py   # 12
.venv/Scripts/python.exe -m pytest tests/test_full_task_recovery.py       # 1
.venv/Scripts/python.exe -m pytest tests/test_approval_integrity.py       # 6
.venv/Scripts/python.exe -m ruff check app/ tests/                   # clean
```

## 5. Phase 3 → Phase 4 gate

**PASS** on all 15 criteria. State-Aware Portal Recovery may start.

## 6. What is interview/resume-worthy (honest)

- Cross-process atomic claim + fencing leases (2/4/8 workers, 1 winner).
- Real process-kill recovery of a COMPLETE procurement task (external ledger
  proves the crash window).
- Action-type-aware recovery: read-only re-query vs idempotent dedup vs
  non-idempotent UNKNOWN_OUTCOME — with a transactional external effect
  ledger separating invocation count from committed side effects.
- Approval CAS with quote-expiry gate and version binding.
- All claims carry n and honest limitations; no fabricated metrics.
