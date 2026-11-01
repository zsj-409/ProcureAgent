# ProcureAgent V3 Baseline Audit

- **Audit date**: 2026-11-01
- **Repository snapshot**: `github.com/zsj-409/ProcureAgent` @ `main` (zip snapshot, 2026-10-05; commit `e3b2647` local import)
- **Environment**: Windows 11 / Python 3.11.9 venv / SQLite / pytest 8.3.5
- **Auditor scope**: Phase 0 (real-repo review + freeze) — *no code changes yet*

> This document records the **verified as-is state** of the repo before the V3
> work begins. Every claim below was reproduced by actually running the code
> (tests, eval runner, targeted probes). Markers follow the V3 contract:

> `IMPLEMENTED` / `UNIT_TESTED` / `E2E_VERIFIED` / `REAL_LLM_VERIFIED` / `EXPERIMENTAL` / `NOT_IMPLEMENTED`

---

## 1. Repo state & provenance

| Item | Observed | Verified |
|---|---|---|
| Repo reachable | `api.github.com` 200; codeload zip 200; `git clone` smart protocol **fails** (schannel handshake) | ✅ (zip imported) |
| Git history | Imported snapshot has no upstream history; local git initialized with one commit | ⚠️ |
| Archive source | `https://codeload.github.com/zsj-409/ProcureAgent/zip/refs/heads/main` (branch `main`) | ✅ |
| Archive SHA-256 | `a4c14168782f37554c00be49892888028dcf47c5296d986de671d611e11787a8` (`proc.zip`) | ✅ |
| Archive taken | 2026-11-01 15:46 (CST); zip entries dated 2026-10-05 12:35 | ✅ |
| Upstream commit ID | Not determinable from the zip (no `.git` inside). Local import commit `e3b2647` is **not** an upstream commit. | ⚠️ |
| Local baseline commit | `e3b2647` (import) → `145ea51` (Phase 0 audit) | ✅ |
| Version | `app/main.py` reports `2.0.0`; changelog `v2.0.0 — Deep-Dive Release` | ✅ |
| README claims | "38 passed" | ✅ **reproduced: 38 passed** (57.6s, 3 warnings) |
| Dependency set | `requirements.txt` pins 10 packages | ✅ installed clean on py3.11 |

> Python 3.14 fails to install `pydantic-core` (no prebuilt wheel, rust build
> fails). The repo targets py3.11 (`target-version = "py311"` in ruff config).
> Verified environment: Python 3.11.9, venv `.venv/` (fastapi 0.115.12,
> uvicorn 0.34.0, pydantic 2.11.3, pydantic-core 2.33.1, sqlalchemy 2.0.39,
> httpx 0.28.1, playwright 1.51.0, pytest 8.3.5, pytest-asyncio 0.25.3,
> python-dotenv 1.0.1).

## 2. Module-by-module capability markers

| Module | Marker | Evidence |
|---|---|---|
| `TaskRuntime` (retry/timeout/checkpoint/idempotency) | `UNIT_TESTED` + `E2E_VERIFIED` | retry attempts persisted; resume E2E test proves SQLite checkpoint reuse across process rebuild |
| `BackgroundTaskRunner` | `UNIT_TESTED` (single-process) | `test_agent_run_background_flow`; **NOT cross-process verified** |
| `PlanValidator` | `UNIT_TESTED` | unit checks exist; **no order enforcement, no finalize enforcement** (see §4.6) |
| `ExecutionValidator` | `UNIT_TESTED` (partial) | empty-quotes FAIL path verified; shortfall→review path verified |
| `ProcurementScorer` + split award | `UNIT_TESTED` | price/delivery/stock weights; split lines ≤3; shortfall reported |
| `ProcurementPolicy` | `UNIT_TESTED` | threshold semantics, budget-override, shortfall→approval |
| `QuoteNormalizer` | `UNIT_TESTED` (via tests) | currency passthrough only — **no FX conversion** |
| `ApiExecutor` | `UNIT_TESTED` | mocked transport; real mock server E2E |
| `PortalExecutor` (fixed script → heuristic → LLM) | `E2E_VERIFIED` (heuristic only) | chaos self-heal, card layout, gate click verified against real Playwright |
| `PortalAgentLoop` guards | `E2E_VERIFIED` | bounded actions; form values restricted to product name; **LLM level not E2E-verified (no real model)** |
| `TraceRecorder` | `UNIT_TESTED` | rows persisted; readback API |
| `Workbench APIs` (list/reject/approvals/suppliers/portal-steps/overview) | `E2E_VERIFIED` | `test_workbench_v2` |
| Web UI (SPA) | `UNIT_TESTED` (API-level) | static SPA served; not visually E2E-audited here |
| Approval endpoints | `UNIT_TESTED` | happy-path approve/reject; **no auth, no authorization, no idempotent re-approval test** |
| Real LLM integration | `REAL_LLM_NOT_RUN` | no credentials configured; only `MockModelClient`/`ScriptedModelClient` paths exercised |
| Real browser CDP | `NOT_VERIFIED` | `chrome_cdp_url` empty; tests launch own headless chromium |

## 3. Baseline metrics (reproduced 2026-11-01)

### 3.1 pytest

```
38 passed, 3 warnings in 57.56s
```

- No failures, no skips. (README claim "38 passed" **reproduced**.)
- Portal tests launch real headless Chromium (Playwright).

### 3.2 Eval runner (`python -m tests.eval.eval_runner`)

Recorded output (this machine, 2026-11-01):

```
Task Success Rate:             0.938
Interpretation Success Rate:   0.750
Plan Validation Success Rate:  1.000
Executor Success Rate:         0.618
Recovery Success Rate:         1.000
Recommendation Accuracy:       0.667   ← README claims 1.000 — NOT reproducible
Average Steps:                 2.125
Average Latency (ms):          4037
Average LLM Calls:             0.529
```

> Two runs on this machine produced `Recommendation Accuracy = 0.667` and
> `0.750` — the README's `1.000` is **not reproducible** and varies run to run
> (portal pricing changes with mock-state; see §4.3).

Per-case detail (one run):

```
normal_balanced_qty20     ok ✓  supplier ✓
price_priority_qty20      ok ✓  supplier ✗  ← accuracy miss
delivery_priority_qty20   ok ✓  supplier ✓
stock_priority_qty20      ok ✓  supplier ✓
low_total_no_approval     ok ✓  supplier ✗  ← accuracy miss
budget_exceeded           ok ✓  supplier ✓
api_supplier_failure      ok ✓  supplier ✓
portal_supplier_failure   ok ✓  supplier ✓
single_supplier_valid     ok ✓  supplier ✓
all_suppliers_fail        ok ✓  supplier ✓
low_stock_fails           ✗  (got WAITING_APPROVAL, expected FAILED)
abnormal_price_fails      ok ✓  supplier ✓
flaky_retry_recovers      ok ✓  supplier ✓
agent_normal              ok ✓  supplier ✗  ← accuracy miss
agent_missing_quantity    ok ✓
agent_nonexistent_product ok ✓
llm_unavailable_fallback  ok ✓  supplier ✓
```

## 4. Findings

Severity key: **P0** = can corrupt business state / violates a hard invariant;
**P1** = reliability/security gap with known trigger; **P2** = quality/eval gap.

---

### P0-1 — No authentication or authorization on any API endpoint

**Location**: `app/api/approvals.py`, `app/api/tasks.py`, `app/api/agent.py`, `app/api/workbench.py` (all routers), `app/main.py` (router registration)

**Problem**: Every endpoint is anonymous. `POST /api/v1/tasks/{task_id}/approve`
accepts any caller and immediately flips a task to `APPROVED` then `COMPLETED`.
There is no identity, no role, no tenant, no approval-policy check, no audit of
*who* approved. `ProcurementPolicy` decides *whether* approval is needed, but
nothing checks *who is allowed to grant it*.

**Reproducible**: `curl -X POST http://localhost:8000/api/v1/tasks/<id>/approve` with no credentials → 200, task COMPLETED.

**Severity**: P0.

**Fix**: Requester/operator/approver/auditor identity model; bearer-token or
API-key auth; role checks on approve/reject; approval bound to immutable
recommendation version; audit log captures principal + decision + version.

**Test**: unauthenticated approve → 401; non-approver role → 403; cross-tenant
task access → 404/403; re-approve idempotent (second approve no-op or 409 with
consistent state).

**Blocks**: yes — every later phase that touches approval or multi-tenant state.

---

### P0-2 — Concurrent workers can execute the same step in parallel (no lease)

**Location**: `app/agent/runtime.py` `TaskRuntime.execute`; `app/agent/orchestrator.py` `_collect_one`; `app/infrastructure/database.py` `create_step_execution` / `complete_step_execution`

**Problem**: The runtime checks for a cached COMPLETED result, then
unconditionally creates a RUNNING attempt and runs `action()`. Nothing claims
the step atomically. Two `resume` calls (or one `run` + one `resume`, or two
workers) on the same task concurrently both pass the cache check, both create
RUNNING rows, and both call the external supplier. `StepExecution.status` is a
plain column write, not a conditional claim (`UPDATE ... WHERE status='PENDING'`).
The result is duplicate external effects and two RUNNING rows for the same key.

**Reproducible**: fire two concurrent `POST /tasks/{id}/resume` on a FAILED task
with a slow supplier; observe two RUNNING attempts and duplicated supplier calls
(`CALLS` counters in `test_resume_e2e` prove supplier calls are visible).

**Severity**: P0.

**Fix**: Atomic step claim — `INSERT/UPDATE ... WHERE status IN (PENDING,RUNNING)` +
check rowcount; owner token (worker id) on the RUNNING row; lease + heartbeat
for crash recovery; a step may only run when it is not claimed by a live lease.

**Test**: concurrent claim → exactly one winner; second gets `claimed` / waits;
lease-expired step re-claimable; duplicate submission → single external effect.

**Blocks**: yes — Phase 2 is built on this.

---

### P0-3 — `BackgroundTaskRunner` loses execution ownership on process exit

**Location**: `app/agent/background.py` `_run`

**Problem**: Tasks run in an in-process `ThreadPoolExecutor`. If the process
dies (crash, deploy, `/stop`), the in-flight task simply stops. There is no
persisted lease, no heartbeat, no re-claim by another worker. The DB row stays
in a non-terminal state (e.g. `COLLECTING`) forever. The task is "lost", not
"recoverable". (The v2 resume test only simulates *controlled* rebuild, not a
hard kill.)

**Reproducible**: start a background task against a slow portal, kill the
process, restart → task stuck in `COLLECTING`, no worker reclaims it.

**Severity**: P0 (recoverability claim is the product's core promise).

**Fix**: persist worker ownership + lease with heartbeat in the task table;
a stale-lease scanner marks abandoned tasks FAILED (or re-queues them) with a
`recovered_after` audit field; do not claim cross-process recovery until this
exists. SQLite in-process threads are fine for single-process demos; multi-
process workers need DB-backed leases (SQLite WAL or Postgres) — Phase 2.

**Test**: kill process mid-step, restart, scanner marks task FAILED and a
resume re-executes only the lost step; no duplicate external effect.

**Blocks**: yes.

---

### P0-4 — Different currencies are compared as if identical

**Location**: `app/procurement/scorer.py` `ProcurementScorer.score` (`min_price`, ratios);
`app/procurement/policy.py` `evaluate` (`estimated_total > max_budget`);
`app/procurement/normalizer.py` (currency passthrough, no FX table)

**Problem**: `SupplierQuote.currency` exists, but scoring divides raw
`unit_price` values without converting to a common currency. A quote of
`¥99` (≈$14) is compared directly against `$89.99` and loses. `estimated_total`
is always labeled "USD" in `RecommendationFinalizer._template` regardless of the
actual currency mix. There is no exchange-rate table, no `to_currency` field on
the quote, no landed-cost concept (tax/freight are not even modeled).

**Reproducible**: score quotes `[$89.99 USD, ¥99.00 CNY]` → USD quote wins by
raw numeric comparison; estimated_total claims USD.

**Severity**: P0 (business decision wrong on a real multi-currency book).

**Fix**: add `fx_rates` (source→reference currency) with `updated_at` and
provenance to the input contract; `QuoteNormalizer` keeps raw values and emits
a normalized `unit_price_ref` in a reference currency only when a verifiable
rate exists; scoring/policy operate on the reference price; **no fabricated
"accurate landed cost" without a verifiable rate** (V3 rule). Add currency-mismatch
tests; policy compares `estimated_total_ref` vs `max_budget` *in the same
currency*, else requires approval (cannot decide) rather than silently deciding.

**Test**: same-currency unchanged; mixed-currency with known rate converts;
mixed-currency without rate → approval required / not silently scored; raw
quote retained next to normalized.

**Blocks**: yes (Phase 1 contract + Phase 5 optimizer correctness).

---

### P0-5 — `max_budget` is treated as a hard cap *and* an approval exception, inconsistently

**Location**: `app/procurement/policy.py` `evaluate`

**Problem**: When `estimated_total > max_budget`, the policy returns
`WAITING_APPROVAL` — i.e. the budget is **not** a hard constraint; it is an
approval trigger. The same budget is described to the user as a limit
(`ProcurementRequest.max_budget`). There is no distinction between a user's
hard budget (must never be exceeded) and an internal approval threshold (may be
exceeded with sign-off). Phase 1 requires this distinction.

**Reproducible**: `{"quantity":1,"max_budget":50}` on a $99.99 item →
`WAITING_APPROVAL` (task waits for human), not `FAILED` and not `APPROVED`.

**Severity**: P0 for contract clarity (business semantics ambiguous); the
current behavior (approve-with-exception) is defensible only if documented.

**Fix**: add `budget_mode` (`HARD`/`SOFT`/`APPROVAL_GATE`) to the request
contract; HARD → allocation must satisfy it (Phase 5 optimizer) and scoring
rejects infeasible quotes; SOFT/APPROVAL_GATE → explicit human decision.
Document default: `APPROVAL_GATE` (current behavior) until Phase 5 lands.

**Test**: HARD budget overrun → FAILED (no partial auto-success); SOFT → 
WAITING_APPROVAL with reason; mixed with split award → per-line budget check.

**Blocks**: yes (Phase 1 contract, Phase 5).

---

### P0-6 — `PlanValidator` does not validate plan order or finalize step

**Location**: `app/agent/validator.py` `PlanValidator.validate`

**Problem**: The validator requires SCORE and CHECK_POLICY to be present
anywhere, and COLLECT_SUPPLIER steps to be well-formed — but does not enforce
*order* (e.g. SCORE before COLLECT is accepted) or a FINALIZE step at all. The
`PlanAction.FINALIZE` enum exists; the rule-based planner appends it, but the
validator never requires it. An LLM could emit a plan that scores before
collecting and the executor would still accept it.

**Reproducible**: `ProcurementPlan(steps=[SCORE, CHECK_POLICY])` validates clean
(try it: `PlanValidator().validate(...) is None`).

**Severity**: P0 (plan integrity gate is weak; execution-order invariant not
guarded).

**Fix**: enforce a minimal valid skeleton:
`COLLECT_SUPPLIER* → NORMALIZE* → SCORE → CHECK_POLICY → FINALIZE`
(strict subset allowed only when steps are absent), require FINALIZE, reject
reordering. Keep `max_steps` cap.

**Test**: valid skeleton passes; SCORE-before-COLLECT rejected; missing
FINALIZE rejected; missing NORMALIZE rejected; extra unknown action rejected.

**Blocks**: yes (the whole deterministic-execution guarantee is built on it).

---

### P1-1 — `Recommendation Accuracy` in the eval harness is misleading

**Location**: `tests/eval/eval_runner.py` (accuracy computation + case expectations)

**Problem**:
1. Accuracy checks only that `recommended_supplier` equals a **pre-filled
   expected supplier id** in the case table. It is a *labeled-match* test, not a
   *ground-truth best-supplier* test: the expected id is chosen by the test
   author, and the mock data changes (portal prices differ from API prices), so
   the "right" answer is not derived from any objective cost optimum. It cannot
   distinguish a correct cost-optimal pick from an arbitrary pick.
2. README claims `1.000`; measured here: `0.667`/`0.750` run-to-run.
3. `Recovery Success Rate` is computed from **a single case**
   (`flaky_retry_recovers`, n=1) — one successful flaky retry. That is not a
   recovery rate; it is anecdote. (V3 explicitly forbids claiming 100% from one
   flaky case.)

**Severity**: P1 (does not block code, but blocks honest claims; V3's whole
thesis is *independent final-state evaluation*).

**Fix**: accuracy = does the recommendation achieve the **lowest total landed
cost** (same currency, verified FX) among feasible candidates — computed by the
evaluator, not pre-filled; recovery rate = verified-recovery fraction over a
real fault-injection matrix (BEFORE_EXTERNAL_CALL, AFTER_COMMIT_BEFORE_RESPONSE,
LEASE_EXPIRED, etc.); report `n` with every rate; do not count injected-failure
expectations as normal-task success.

**Test**: evaluator derives ground truth from the mock supplier catalog each run;
recovery matrix ≥ 6 fault classes; report sample sizes.

**Blocks**: yes (Phase 6 harness must be built on this fix).

---

### P1-2 — `Plan Validation Success Rate = 1.000` is inflated by the same plan object

**Location**: `tests/eval/eval_runner.py` `run_plan_validation`

**Problem**: The metric runs `PlanValidator` on one valid plan and one invalid
plan, both constructed inline by the test — i.e. the validator is scored against
the *same hand-built examples it is designed to accept/reject*. `1.000` here is
a tautology, not a robustness measure, and (per P0-6) does not cover order or
finalize.

**Severity**: P1.

**Fix**: plan-validation metric = fraction of **adversarial/perturbed** plans
rejected + valid plans accepted, drawn from a perturbation suite
(reorder, missing finalize, ghost supplier, duplicate supplier, oversized).

**Blocks**: yes (with P0-6).

---

### P1-3 — Approve endpoint mutates state twice; no idempotency, no version binding

**Location**: `app/api/approvals.py` `approve_task`

**Problem**: After creating the approval record it sets `APPROVED` then
immediately `COMPLETED` via two `save_state` calls — two DB transactions with
no intervening guard, so a concurrent observer can see `APPROVED` and a crash
between them leaves an approved-but-not-completed task (approval recorded but
status inconsistent). Re-approval after COMPLETED returns 409 (good), but a
second approve racing the first can create duplicate approval rows. The approval
is not bound to the recommendation version it approved; the stored
`recommendation_json` can later change while the approval remains valid.

**Severity**: P1 (approval integrity + audit).

**Fix**: single-transition approval (WAITING_APPROVAL → COMPLETED in one
transaction with the approval row); record `recommendation_version` (hash) in
the approval; stale/version-mismatched approvals invalid; idempotent re-approve.

**Test**: approve → single status change; crash-between-transitions impossible;
approval on version A invalid after version B saved; concurrent double-approve
→ one approval row.

**Blocks**: Phase 3.

---

### P1-4 — No explicit `Unknown External Outcome` handling; cached-success ambiguity

**Location**: `app/agent/runtime.py` `get_completed_step_result`; `app/agent/orchestrator.py` `_collect_one`

**Problem**: A step whose external call returned 200 but whose payload was
unparseable is stored as COMPLETED (see `test_resume_e2e`: A and C reach
COMPLETED with empty/bad payloads). Resume then **reuses that checkpoint and
never re-contacts the supplier**, even though no usable quote was ever obtained.
The system treats "HTTP 200 observed" as "external effect succeeded", which V3
explicitly forbids ("不得把 API 返回 200 ... 直接视为最终任务成功"). There is no
UNKNOWN/UNCERTAIN outcome state, no re-verify policy.

**Severity**: P1 (recovery semantics; overlaps P0-2/3 in the resume path).

**Fix**: introduce an explicit outcome model (SUCCESS / FAILED / UNKNOWN
[external outcome uncertain]) on step attempts; a COMPLETED step is only
reusable when its result was *usable* (normalization succeeded); UNKNOWN steps
require re-verification (idempotent query) before retry or must be re-executed
under a new idempotency scope.

**Test**: bad-payload-completed step → not silently reused; resume re-verifies
and re-collects; duplicate-external-effect counter stays 0 for verified
idempotent queries.

**Blocks**: yes (Phase 2).

---

### P1-5 — Portal CDP session shares the first browser context across tasks

**Location**: `app/executors/portal_executor.py` `PortalSession.open`

**Problem**: When connecting over CDP, `browser.contexts[0]` is reused and
`_owns_context=False`; every portal task opens its page in that shared context
(cookies, local state, session). Different portal tasks (different suppliers or
different tenants) therefore share session state — V3 explicitly calls out the
risk of reusing a CDP context. Locally-launched browsers get a fresh context
each time, so this only bites when `chrome_cdp_url` is set — which is exactly
the real-browser scenario.

**Severity**: P1 (session isolation; cross-tenant leakage potential).

**Fix**: always `browser.new_context()` per portal task when isolation is
required; only reuse a context when explicitly configured; never share auth
state across supplier tasks. Document the CDP-context policy.

**Test**: two portal tasks over one CDP browser → distinct contexts (no shared
cookies/localStorage).

**Blocks**: Phase 4 (portal isolation is a stated V3 requirement).

---

### P2-1 — `low_stock_fails` case expectation contradicts the v2 split-award behavior

**Location**: `tests/eval/eval_runner.py` CASES

**Problem**: The case expects `FAILED` for "Low Stock Mouse qty 20" (stock=1),
but v2 changed `ExecutionValidator` to route stock shortfalls to split award +
human approval. Actual behavior observed: `WAITING_APPROVAL`. The eval's
*expected* label is stale relative to v2's own changelog.

**Severity**: P2 (eval self-inconsistency, not production bug).

**Fix**: update the case expectation to `WAITING_APPROVAL` (or make it an
all-suppliers-fail scenario), and add a shortfall-specific assertion (shortfall
> 0, approval_required).

**Blocks**: no.

---

### P2-2 — No explicit test for `Rejected` on the *approve* side of approval-race

**Location**: tests

**Problem**: `test_reject_flow` covers reject idempotency; there is no
approve/reject race test (approve and reject arriving concurrently), no
approve-after-reject, no reject-after-approve. V3 Phase 2/3 fault matrix requires
`APPROVE_REJECT_RACE`.

**Severity**: P2.

**Fix**: add race tests with explicit single-winner semantics (first writer
wins, second gets 409).

**Blocks**: Phase 3.

---

## 5. Answers to the Phase-0 questions (task §16)

1. **审批接口缺少认证和授权** → **YES (P0-1)**. `approve`/`reject`/`run`/`list` are anonymous.
2. **两个 Worker 可能同时执行同一 LogicalStep** → **YES (P0-2)**. No atomic claim; cache-check-then-run race.
3. **后台任务在进程终止时丢失执行权** → **YES (P0-3)**. Thread-pool only, no lease/heartbeat/reclaim.
4. **报价中的不同币种被直接比较** → **YES (P0-4)**. `min_price` on raw unit_price; no FX; label always "USD".
5. **max_budget 是硬约束还是可审批例外** → **AMBIGUOUS (P0-5)**. Current code: approval exception; contract does not say.
6. **PlanValidator 允许不完整/错误顺序的计划** → **YES (P0-6)**. No order, no FINALIZE requirement.
7. **Eval 的恢复/推荐/计划指标是否真实** → **NO (P1-1/P1-2)**. Recovery n=1; accuracy is pre-filled-label matching; plan metric is tautological. README `1.000` not reproducible (measured 0.667/0.750).
8. **Web Workbench 能复现任务恢复和审批过程** → **PARTIAL (P2)**. Task list/detail/portal replay exist; approval gate is a client-side button hitting anonymous endpoints; no *recovery replay* in the UI (no crash-resume visualization).
9. **真实 LLM 与 Mock 的运行结果是否严格区分** → **YES in code** (MockModelClient vs OpenAICompatibleModelClient; trace `model` field; README smoke test requires credentials) — **but NO real-LLM run has ever been recorded** (`REAL_LLM_NOT_RUN`).

## 6. Verified capabilities (honest inventory)

- ✅ 38/38 tests pass (portal self-healing with real Chromium included)
- ✅ SQLite checkpoint resume across a **controlled** app rebuild (`test_resume_e2e`)
- ✅ deterministic split award with shortfall reporting
- ✅ deterministic policy with budget-override + shortfall → approval
- ✅ adaptive portal recovery from chaos markup + interstitial gate
- ✅ background execution with bounded concurrency
- ✅ workbench API + SPA (list/reject/approvals/suppliers/portal-steps/overview)

## 7. NOT yet verified / missing (V3 gates)

- ❌ Cross-process crash recovery (P0-3)
- ❌ Concurrent-execution exclusion (P0-2)
- ❌ Approval authorization & version binding (P0-1, P1-3)
- ❌ FX-aware costing (P0-4)
- ❌ Budget-mode semantics (P0-5)
- ❌ Plan-order validation (P0-6)
- ❌ Real LLM run manifest (REAL_LLM_NOT_RUN — no credentials)
- ❌ Real CDP browser session (chrome_cdp_url never set in tests)
- ❌ Independent final-state evaluation (Phase 6)
- ❌ Constrained optimizer (Phase 5)
- ❌ Portal state graph (Phase 4)
- ❌ Recovery policy evolution (Phase 7)

## 8. Gate decision

**Phase 0 → Phase 1 gate: PASS (with P0 fixes required before Phase 2).**

- Phase 0 deliverable: this document + `V3_CORRECTNESS_AND_EVAL_AUDIT.md`.
- Phase 1 (contracts + input robustness) may start immediately; the P0 fixes
  P0-1/P0-2/P0-3 are prerequisites for Phase 2 (durable runtime) and must land
  with Phase 2, not before Phase 1 contract work.
- No enterprise-production claim is made from this baseline. The system is a
  solid single-process demo with honest deterministic internals; it is not yet
  a durable, policy-governed multi-worker runtime.

## 9. Reproducibility notes

- Full suite: `.venv/Scripts/python.exe -m pytest` → 38 passed (~58 s).
- Eval: `.venv/Scripts/python.exe -m tests.eval.eval_runner` (needs mock API
  + portal served in-process; the runner starts them).
- Portal tests require Playwright Chromium: `.venv/Scripts/python.exe -m playwright install chromium`.
- Python 3.11 required (3.14 fails pydantic-core build).
