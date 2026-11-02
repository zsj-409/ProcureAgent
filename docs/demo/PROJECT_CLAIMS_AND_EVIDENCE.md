# ProcureAgent 3.0 — Claims & Evidence

每项可用于简历/面试的主张，附**代码位置、测试名、实验产物**。所有数字可追溯，
受控模拟实验明确标注，不冒充真实生产数据。

## 主张 1：跨进程崩溃恢复，外部副作用不重复

- **证据**：`tests/test_full_task_recovery.py` — 子进程执行 collect 步骤 →
  `os._exit` 硬杀 → 新 worker 重领 → reconcile 到 WAITING_APPROVAL，效果账本
  显示调用恰 2 次（崩溃前 1 + 恢复后 1）。
- 代码：`app/runtime/durable_worker.py`（效果账本）、`app/infrastructure/database.py`
  （原子 Claim/Lease）。
- 边界：这是**本地进程崩溃**模拟；真实部署的进程级故障语义相同（SQLite WAL 保证）。

## 主张 2：并发 Worker 不会重复执行（原子 Claim + Fencing）

- **证据**：`tests/test_phase2_*`（125 passed 阶段）— 并发 Resume/Claim 测试，
  恰一个赢家。
- 代码：`app/runtime/durable_worker.py` 的 `claim_step`（rowcount==1 判定）。
- 边界：SQLite 单写者模型下验证；PostgreSQL 迁移路径已抽象但未实测。

## 主张 3：未认证不能审批，approver 字段不可信

- **证据**：`tests/test_phase1_approval.py`（104 passed 阶段）+ `test_phase9_demo.py`
  的 `test_demo_unauthorized_approval_rejected`（401 AUTH_REQUIRED、状态不变、
  无审批行）。
- 代码：`app/security/approval_guard.py`、`app/security/principal.py`
  （DemoIdentityProvider 白名单）。
- 产物：`docs/security/APPROVAL_POLICY_SPEC.md`、`docs/experiments/APPROVAL_SECURITY_REPORT.md`。

## 主张 4：审批并发竞争恰一个赢（CAS + 版本绑定）

- **证据**：`tests/test_approval_integrity.py` — 并发 approve/reject → 409
  APPROVAL_RACE_LOST；推荐版本变化 → 审批自动失效。
- 代码：`app/api/approvals.py`（CAS + recommendation_version hash）、
  `app/infrastructure/database.py` 的 `cas_transition`。
- 边界：版本绑定基于推荐 JSON hash——报价/方案变化即失效，但**未**绑定到
  更细粒度的报价快照（Phase 3 已标注）。

## 主张 5：受约束优化比贪心更可靠（硬约束满足）

- **证据**：`tests/eval/procurement_benchmark.py` + `docs/eval/optimization_benchmark.json`
  — 28 世界：Optimizer 18/28 FULLY、**0 violation**；Greedy 14/28、8 violation。
- 代码：`app/procurement/optimizer.py`（两阶段词典序 + Decimal 复核）、
  `app/procurement/verifier.py`（独立验证）、`app/procurement/oracle.py`（穷举
  ground truth）。
- 产物：`docs/experiments/INDEPENDENT_ORACLE_VALIDATION.md`、`PHASE6_FINAL_REPORT.md`。
- **诚实边界**：Cluster Bootstrap CI [0.0, 6.4] 含零——**成本节省不显著**；
  确定性收益是约束正确性，不是省钱。

## 主张 6：语义页面恢复在真实浏览器上可验证

- **证据**：`tests/eval/portal_world.py`（本地 HTTP portal + F1-F8 故障）+ 
  `tests/test_phase4_portal_e2e.py`（7 个真实 Chromium E2E）。
- 消融（`docs/eval/portal_recovery_results.json`）：A 6/7、B 7/7、C 7/7、
  D 7/7（P50 750ms）、E 7/7（P50 484ms）。
- **诚实边界**：E 与 D 成功率持平；收益在延迟（-35%）与安全验证，非成功率。

## 主张 7：成本感知恢复减少开销（成功率饱和下的诚实负结果）

- **证据**：`docs/eval/recovery_policy_results.json` — 120 案例 × 3 策略 =
  360 次真实 Chromium：三策略均 30/40 recovered + 10/40 safe_stop、0 unsafe；
  obs D=40、E=20、F=20/0。
- 代码：`app/runtime/recovery_policy.py`（CostAwareRecoveryPolicy）。
- **诚实边界**：F 与 E 成功率相同，无额外提升；观察次数≠金额成本；成功率饱和
  无法做显著性检验。

## 主张 8：独立评测正确性（不信任自证）

- **证据**：`tests/eval/procurement_world.py`（28 worlds、snapshot_hash、种子
  注册）+ `portal_world.py`（ground truth 隔离）+ Evaluator 只读后端状态。
- DEV/CONFIRMATION/TEST 冻结划分：`docs/experiments/PROCUREMENT_EVALUATION_CONTRACT.md`。
- 边界：所有世界为**合成数据**；真实供应商数据未接入。

## 主张 9：Workbench 统一可观察系统

- **证据**：`app/api/observatory.py`（5 Labs）+ `app/webui/static/js/views/observatory.js`
  + `tests/test_phase9_workbench_e2e.py`（真实浏览器点击验证）。
- 14 个 artifact 全 present；每个 Lab 带诚实边界标注；live 数据端点需认证。

## 明确不主张的（面试主动说明）

- **不主张**生产级多租户隔离（SINGLE_TENANT_DEMO_ONLY）。
- **不主张**真实 LLM 已验证（REAL_LLM_NOT_RUN；Phase 8 harness 已就绪）。
- **不主张**成本节省统计显著（CI 含零）。
- **不主张**成功率提升（Phase 7 饱和，负结果如实报告）。
- **不主张**受控模拟 = 企业生产数据。
