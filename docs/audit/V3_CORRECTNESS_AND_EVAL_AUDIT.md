# ProcureAgent V3 — Correctness & Evaluation Audit

- **Date**: 2026-11-01
- **Scope**: Phase 0 required audit — the 9 questions from the V3 task (§16),
  each with code location, problem, repro, severity, fix, test, and gate impact.
- **Baseline**: `docs/audit/PROCUREAGENT_V3_BASELINE_AUDIT.md`

---

## Q1. 审批接口是否缺少认证和授权？

**Code location**: `app/api/approvals.py` (`approve_task`), `app/api/workbench.py`
(`reject_task`), `app/api/tasks.py`, `app/api/agent.py` — no dependency on any
auth, no identity middleware in `app/main.py`.

**Problem**: `POST /api/v1/tasks/{id}/approve` is anonymous. Anyone can approve
any task. The `approver` field is hard-coded `"human"`. No role checks
(requester vs operator vs approver vs auditor), no tenant scoping, no
authorization policy. The only guard is the state-machine check
(`status == WAITING_APPROVAL`).

**Repro**: run app + mock suppliers, create a task reaching WAITING_APPROVAL,
then `curl -X POST http://127.0.0.1:8000/api/v1/tasks/<id>/approve` with no
credentials → 200, task COMPLETED.

**Severity**: **P0** — violates the V3 hard rule "审批必须检查调用者身份…权限…".

**Fix**: identity (bearer/api-key), role model (Requester / Procurement Operator /
Approver / Auditor), tenant boundary, approval bound to immutable
recommendation version, audit row with principal.

**Test**: unauthenticated approve → 401; operator approves → 403; cross-tenant →
404; duplicate approve → idempotent/409; approve after reject → 409.

**Blocks next phase?** Yes — everything in Phase 3 and all security claims.

---

## Q2. 两个 Worker 是否可能同时执行同一 LogicalStep？

**Code location**: `app/agent/runtime.py` `TaskRuntime.execute` (cache-check →
`create_step_execution(RUNNING)` → `action()`); `app/infrastructure/database.py`
`create_step_execution` (unconditional INSERT), `complete_step_execution`
(unconditional UPDATE).

**Problem**: No atomic claim. Two concurrent `run`/`resume` calls both miss the
cached-COMPLETED check, both insert RUNNING rows, both invoke the external
supplier. `StepExecution` has no owner column and status is not guarded by a
conditional UPDATE. Duplicate external effects and duplicate RUNNING rows are
possible.

**Repro**: two concurrent `POST /tasks/{id}/resume` (or one `run` + one
`resume`) on a FAILED task against a slow supplier → two RUNNING rows, supplier
called twice (observe via mock counters).

**Severity**: **P0**.

**Fix**: atomic claim (`UPDATE step_executions SET status='RUNNING', owner=?,
lease_until=? WHERE id=? AND status='PENDING'` with rowcount check, or a
dedicated claim table), owner token, lease + heartbeat, stale-lease scanner.

**Test**: concurrent claim → exactly one owner; second gets rejected/waits;
lease expiry → re-claimable; duplicate submission → one external effect.

**Blocks next phase?** Yes — Phase 2's core.

---

## Q3. 后台任务在进程终止时是否会丢失执行权？

**Code location**: `app/agent/background.py` `_run` (in-process
`ThreadPoolExecutor`); no lease column anywhere in `TaskRecord`.

**Problem**: Background tasks are owned only by the process. On crash/kill,
nothing persists ownership, nothing heartbeats, nothing reclaims. The task row
stays non-terminal forever. The existing `test_resume_e2e` rebuilds the app
*cleanly* and *deliberately* — it does not simulate a hard kill mid-step.

**Repro**: start a background portal task, kill the process, restart → task
remains `COLLECTING`; no scanner marks it; no worker picks it up.

**Severity**: **P0**.

**Fix**: persist `owner_worker_id`, `lease_until` on the task row; periodic
scanner (in-process or separate worker) marks stale-lease tasks FAILED with
`recovered_after` audit; resume then re-executes only lost steps. SQLite WAL
supports a minimal multi-worker setup; Postgres migration is the Phase-2
comparison, not a first step.

**Test**: kill mid-step → scanner marks FAILED → resume re-executes only the
unfinished step, supplier called once.

**Blocks next phase?** Yes — recoverability is the product's core promise.

---

## Q4. 报价中的不同币种是否被直接比较？

**Code location**: `app/procurement/scorer.py` `score()` — `min_price =
min(q.unit_price ...)` and ratios on raw `unit_price`; `policy.py` compares
`estimated_total` (raw) with `max_budget` (raw); `normalizer.py` passes
`currency` through without conversion; `Finalizer._template` hard-codes "USD".

**Problem**: `¥99` ≈ $14 is compared to `$89.99` as 99 > 89.99. No FX table,
no reference currency, no landed cost (tax/freight not modeled at all). The
estimated total's currency label is a lie for mixed-currency books.

**Repro**: score `[($89.99, USD), (99, CNY)]` → USD wins on raw compare.

**Severity**: **P0** — a real multi-currency decision is wrong, and V3 forbids
pretending at landed cost without verifiable rates.

**Fix**: add `fx_rates` (source→reference, with provenance + `updated_at`),
keep raw quote + normalized reference price; scoring/policy use reference
price; without a verifiable rate for a currency, do not decide (route to
approval / flag `currency_unresolved`).

**Test**: same-currency unchanged; mixed with rate converts; mixed without rate
→ not silently scored; raw preserved.

**Blocks next phase?** Yes — Phase 1 contract and Phase 5 optimizer.

---

## Q5. max_budget 是硬性约束还是可审批例外？

**Code location**: `app/procurement/policy.py` `evaluate` — 
`if max_budget is not None and estimated_total > max_budget: WAITING_APPROVAL`.

**Problem**: The code treats budget as an *approval trigger* (can be exceeded
with sign-off), but the contract (`ProcurementRequest.max_budget`) presents it
as a limit. Two different business semantics; neither is declared. V3 Phase 1
requires distinguishing user hard budget vs internal approval threshold.

**Repro**: `{"product_name": "MX Master 3S", "quantity": 1, "max_budget": 50}` →
WAITING_APPROVAL (a $99.99 item is simply routed to a human), not FAILED.

**Severity**: **P0** for contract semantics (the system cannot answer "is this
a valid order?").

**Fix**: add `budget_mode: HARD | SOFT | APPROVAL_GATE`; HARD → infeasible
allocation (Phase 5) and scoring rejects; SOFT → approval; APPROVAL_GATE =
current behavior documented as default until Phase 5.

**Test**: HARD overrun → FAILED; SOFT → WAITING_APPROVAL; split-award per-line
budget checks; documented default behavior.

**Blocks next phase?** Yes — Phase 1 contract.

---

## Q6. PlanValidator 是否允许不完整或错误顺序的计划？

**Code location**: `app/agent/validator.py` `PlanValidator.validate`.

**Problem**: Checks action set membership and supplier references only. It does
not enforce order (`SCORE` before `COLLECT` accepted), does not require
`FINALIZE`, does not require `NORMALIZE`. `PlanAction.FINALIZE` exists and the
rule-based planner appends it, but validation never checks it.

**Repro**: `PlanValidator().validate(ProcurementPlan(steps=[SCORE, CHECK_POLICY]), profiles)` → `None` (valid).

**Severity**: **P0** — the deterministic-execution guarantee is not actually
guarded at the plan level.

**Fix**: skeleton validation `COLLECT_SUPPLIER* → NORMALIZE* → SCORE →
CHECK_POLICY → FINALIZE`; FINALIZE required; reorder rejected; keep caps.

**Test**: valid skeleton passes; reorder rejected; missing FINALIZE rejected;
missing NORMALIZE rejected; ghost/duplicate supplier rejected (already).

**Blocks next phase?** Yes — plan integrity is the runtime's input.

---

## Q7. 现有 Agent Eval 的恢复指标、推荐准确率和计划验证指标是否真实可靠？

**Code location**: `tests/eval/eval_runner.py` — metrics block at bottom;
`run_plan_validation`; `CASES`.

**Problems**:
1. **Recovery Success Rate = 1.000 from n=1** — `recovery_cases = [r for r in
   results if r["name"] == "flaky_retry_recovers"]`. One case, one flaky retry.
   This is a single anecdote, and the metric is 1.0 because the *only* case
   passed. V3 explicitly forbids deriving a recovery rate from one successful
   flaky case.
2. **Recommendation Accuracy = pre-filled label matching** — the expected
   supplier id is written by the test author (`CASES[].supplier`), and the mock
   catalog is not a fixed ground-truth optimum (portal prices differ from API
   prices; mock state mutates across runs — `_flaky_failed_once` etc.). Measured
   on this machine: **0.667 / 0.750**, not the README's **1.000**. The README
   number is not reproducible.
3. **Plan Validation Success Rate** is computed from two hand-built plans (one
   valid, one invalid) constructed in the same function that scores them — a
   tautology, not a robustness metric, and it cannot catch P0-6 holes.
4. **Task Success Rate** mixes injected-failure expectations into "success"
   (`all_suppliers_fail` expected FAILED counts as a success) — acceptable as a
   scenario-pass rate, but must not be conflated with a real business success
   rate; V3 wants them split.

**Severity**: **P1** (does not corrupt production state, but the headline
claims are not evidence-backed).

**Fix**: accuracy = evaluator-computed ground truth (min landed cost in one
currency over the *actual* mock catalog) instead of pre-filled ids; recovery =
verified-recovery fraction over a fault-injection matrix (≥6 classes) with `n`
reported; plan metric = adversarial plan suite; separate scenario-pass vs
business-success metrics.

**Blocks next phase?** Yes — Phase 6 harness inherits these definitions.

---

## Q8. Web Workbench 是否能复现任务恢复和审批过程？

**Code location**: `app/webui/static/` (SPA), `app/api/workbench.py` (portal
replay endpoints), `app/api/approvals.py`.

**Problem**: The UI can list tasks, show task detail, replay portal frames,
approve/reject. But:
- "恢复" is only the *resume button* on failed tasks — there is no crash-recovery
  visualization (no lease/worker/recovery trace shown).
- Approval has no identity — the workbench button calls the anonymous approve
  endpoint; it cannot demonstrate authorization boundaries.
- Portal replay exists (frames + steps.json) — good — but there is no
  *recovery* panel showing which fault was injected and how it was recovered.

**Severity**: **P2** for the demo claim.

**Fix (Phase 9 workbench)**: Execution Flight Recorder view (trace + state
transitions), Chaos Recovery Lab (inject fault → show recovery trajectory),
Approval & Policy Inspector (roles, version binding, audit).

**Blocks next phase?** No (Phase 9 is end-of-roadmap), but keep in mind.

---

## Q9. 真实 LLM 与 Mock 的运行结果是否严格区分？

**Code location**: `app/agent/model.py` (`OpenAICompatibleModelClient` vs
`MockModelClient`), `app/agent/prompts.py`, `app/main.py` `build_model_client`,
`scripts/llm_smoke_test.py`, trace `model` field.

**Problem**: The *code* distinguishes them cleanly (real client only built when
`LLM_API_KEY` set; trace records `model` name + tokens; README smoke test
exists). But **no real-LLM run has ever been recorded in this repo** — no
manifest, no token/cost data. `ScriptedModelClient.calls` (eval) is not a real
LLM. So the system is honest by construction but unverified in practice.

**Severity**: **P2** (correct-by-construction; missing evidence).

**Fix (Phase 8)**: real-model run manifest (provider, model id, prompt hash,
call count, tokens, cost estimate, latency, errors, page snapshots, final
state, policy gate). Mark MOCK/SCRIPTED/REPLAY/REAL_LLM/LIVE_READ_ONLY.

**Blocks next phase?** No — offline work proceeds; REAL_LLM_NOT_RUN is a
declared status until credentials exist.

---

## Summary table

| # | Question | Severity | Fix phase | Blocks? |
|---|---|---|---|---|
| 1 | 审批认证/授权缺失 | P0 | Phase 3 (design now, land with Phase 2 gate) | Yes (Phase 3) |
| 2 | 并发执行同一 Step | P0 | Phase 2 | Yes |
| 3 | 进程终止丢失执行权 | P0 | Phase 2 | Yes |
| 4 | 币种直接比较 | P0 | Phase 1 contract + Phase 5 | Yes |
| 5 | max_budget 语义不明 | P0 | Phase 1 contract | Yes |
| 6 | PlanValidator 不校验顺序/终态 | P0 | Phase 1 (with Phase 0 fixes) | Yes |
| 7 | Eval 指标不可靠 | P1 | Phase 1/6 | Yes (Phase 6) |
| 8 | Workbench 恢复/审批复现不完整 | P2 | Phase 9 | No |
| 9 | 真实 LLM 无记录 | P2 | Phase 8 | No |

## Immediate actions (this cycle)

1. Phase 0 audit docs (this + baseline) — committed.
2. P0-6 plan-order validation + P0-4 currency handling + P0-5 budget-mode
   contract — Phase 1 code, committed separately.
3. P0-1/P0-2/P0-3 — land with Phase 2 (durable runtime), gated.
