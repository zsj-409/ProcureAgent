# Phase 1 Correctness Report

- **Date**: 2026-11-01
- **Commits**: `6ed59ad` (plan validation + approval hardening), (pending Commit B: FX/budget/interpreter/eval)
- **Tests**: **104 passed** (was 38), 0 failures, 0 skips, ruff clean
- **Business rule version**: `budget-approval-rules-v1`; contract `input-contract-v1`

## 1. What changed (files)

| Area | Files |
|---|---|
| Approval security | `app/security/principal.py` (new), `app/security/approval_guard.py` (new), `app/api/approvals.py`, `app/api/workbench.py`, `app/infrastructure/settings.py`, `app/infrastructure/database.py` (ApprovalRecord + version binding), `app/main.py` |
| Plan validation | `app/agent/validator.py`, `app/agent/orchestrator.py` (runtime gate + resume re-validation) |
| FX & quotes | `app/procurement/fx.py` (new), `app/procurement/normalizer.py`, `app/procurement/schemas.py` (SupplierQuote normalization fields), `app/procurement/scorer.py` |
| Budget semantics | `app/procurement/schemas.py` (BudgetMode, user_budget_limit), `app/procurement/policy.py` (full rewrite) |
| Interpreter | `app/agent/interpreter.py` (quantity/budget/currency/mode parsing) |
| Eval | `tests/eval/eval_runner.py` (recovery n=1 label, low_stock semantic, accuracy n) |
| Docs | `docs/contracts/*`, `docs/experiments/*`, `docs/audit/BASELINE_EVAL_RECONCILIATION.md`, README |
| Tests | `tests/test_approval_guard.py`, `tests/test_plan_validator_phase1.py`, `tests/test_fx_and_pricing.py`, `tests/test_budget_semantics.py`, `tests/test_input_robustness.py` (new); `test_scorer.py`, `test_workbench_v2.py`, `test_task_flow.py` (updated for new contract) |

## 2. Acceptance criteria (from the Phase 1 gate)

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | 非法采购计划不能执行 | ✅ | validator rejects; runtime gate + resume re-validation |
| 2 | 未授权审批不能改变任务状态 | ✅ | guard 401/403; denied requests leave state + approval rows untouched |
| 3 | 不可比较报价不能被评分器当可比较金额 | ✅ | scorer uses `normalized_unit_price_ref`; raises when none comparable |
| 4 | 用户硬预算不能被审批绕过 | ✅ | HARD over-budget → FAILED; SOFT/APPROVAL_GATE → explicit approval |
| 5 | 输入与业务规则有一致契约 | ✅ | `input-contract-v1`, `budget-approval-rules-v1`, quote-normalization-v1 |
| 6 | 现有 + 新回归测试通过 | ✅ | 104 passed |
| 7 | Eval 报告准确反映样本与结果 | ✅ | single-fault n=1 labeled; accuracy n reported |
| 8 | 未解决 P0 有归属与限制 | ✅ | P0-1 approved; P0-2/P0-3 (concurrency, cross-process) → Phase 2 |

## 3. Real test commands

```
.venv/Scripts/python.exe -m pytest                          # 104 passed
.venv/Scripts/python.exe -m pytest tests/test_approval_guard.py   # 7
.venv/Scripts/python.exe -m pytest tests/test_plan_validator_phase1.py  # 13
.venv/Scripts/python.exe -m pytest tests/test_fx_and_pricing.py     # 20
.venv/Scripts/python.exe -m pytest tests/test_budget_semantics.py   # 15
.venv/Scripts/python.exe -m pytest tests/test_input_robustness.py   # 13
.venv/Scripts/python.exe -m tests.eval.eval_runner           # metrics below
.venv/Scripts/python.exe -m ruff check app/ tests/           # clean
```

## 4. Eval runner (Phase 1 run, 2026-11-01)

```
Task Success Rate:             1.000
Interpretation Success Rate:   0.750
Plan Validation Success Rate:  1.000
Executor Success Rate:         0.618   (includes injected failures)
Single Fault Recovery:         pass=1/1 (n=1) — NOT a population rate
Recommendation Accuracy:       0.833 (n=12)
```

## 5. Negative results / honest limitations

- **Recommendation Accuracy 0.833 (n=12)** is still label-matching against
  pre-filled expected suppliers — the Phase 6 harness will derive true
  cost-optimal ground truth. Not claimed as production accuracy.
- **Recovery n=1**: no fault-injection matrix yet (Phase 2).
- **Concurrency (P0-2) and cross-process recovery (P0-3) remain open** —
  Phase 1 does NOT claim enterprise-grade durability. This is a declared
  limitation, not hidden.
- FX fixture is offline/illustrative; expired-quote hard-gating is not yet
  enforced at scoring time.
- `estimated_total_currency` is currently hard-wired to USD in the
  orchestrator's policy call (the scorer emits reference currency); a fully
  generalized per-quote reference currency is Phase 5 work.

## 6. Phase 2 gate

**Conditional PASS.** All 8 Phase-1 criteria are met. Phase 2 (atomic step
claim, lease/heartbeat, crash recovery, fault injection) starts next; the
concurrency/cross-process P0s are Phase 2's first tasks.
