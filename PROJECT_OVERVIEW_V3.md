# ProcureAgent V3 项目概述

## 一句话目标

把 ProcureAgent 从"能跑通采购流程的 Agent Demo"升级为**对异构企业软件具有可验证执行可靠性、故障恢复能力和安全治理边界的 Agent Runtime**——回答"故障后为什么能恢复、如何证明供应商没有被重复执行、审批与状态如何一致、自适应网页恢复在什么条件下值得、组合采购方案如何满足硬约束、为什么最终状态评测比 LLM 自评更可信"。

## 工作目录

- 仓库：`D:\agent_work\procureagent3\src\ProcureAgent-main`（本地 git，分支 main）
- 远程快照来源：`github.com/zsj-409/ProcureAgent` @ main（2026-10-05 zip；本机 git 智能协议不通，用 codeload zip 导入）
- 日志：`logs/work_log.md`；审计：`docs/audit/`；合同：`docs/contracts/`；实验：`docs/experiments/`；安全：`docs/security/`；评测：`docs/eval/`

## 基线（已实测，2026-11-01）

- Python 3.11 venv，依赖全装；Playwright chromium 可用
- `pytest`：**38 passed**（与 README 一致）
- Eval runner：Task Success 0.938；**Recovery 1.000（n=1，不可信）**；**Recommendation Accuracy 0.667–0.750（README 的 1.000 不可复现）**；Plan Validation 1.000（自证式）

## 架构速览（现状 v2.0 + Phase 1）

自然语言 → `ProcurementInterpreter`（预算模式/币种解析）→ `Planner` → `PlanValidator`（依赖顺序校验）→ `ProcurementOrchestrator`（运行时计划门禁）→ `TaskRuntime`（重试/超时/检查点/幂等）→ `ExecutorRouter`（API / Portal 三级升级）→ `QuoteNormalizer`（FX 归一化，原始+参考价双保留）→ `ExecutionValidator` → `ProcurementScorer`（仅可比较报价排名 + 分单 ≤3 + shortfall）→ `ProcurementPolicy`（硬预算 vs 审批阈值）→ `RecommendationFinalizer`。审批写操作经 `ApprovalGuard`（默认拒绝未认证）。SQLite + Trace + Workbench SPA。

## Phase 0 结论（P0 清单）

| # | 问题 | 代码位置 |
|---|---|---|
| P0-1 | API 全匿名，approve 无认证授权 | app/api/approvals.py |
| P0-2 | 无原子 Step Claim，并发 resume 双执行 | app/agent/runtime.py, database.py |
| P0-3 | 后台任务进程终止即丢执行权 | app/agent/background.py |
| P0-4 | 币种直接比较，无汇率 | app/procurement/scorer.py, normalizer.py |
| P0-5 | max_budget 硬约束/审批例外语义不明 | app/procurement/policy.py |
| P0-6 | PlanValidator 不校验顺序与 FINALIZE | app/agent/validator.py |
| P1-1/2 | Eval 恢复 n=1、准确率=预填标签、计划验证自证 | tests/eval/eval_runner.py |
| P2 | Workbench 无崩溃恢复可视化；真实 LLM 无记录 | app/webui, model.py |

Gate：Phase 0 → 1 通过。P0-1（审批）已在 Phase 1 封堵；P0-2/3 为 Phase 2 前置；P0-4/5/6 已在 Phase 1 修复。

## Phase 1 完成（2026-11-01，104 passed）

- **审批安全**（Commit A）：默认拒绝未认证 approve/reject；请求体 approver 永不信任；单事务审批 + 版本绑定。
- **PlanValidator**（Commit A）：依赖顺序校验 + 运行时门禁 + resume 重验。
- **FX/报价**（Commit B）：方向化汇率、MISSING/STALE、原始+归一化双保留、scorer 仅用可比较报价。
- **预算语义**（Commit B）：user_budget_limit（HARD 不可审批绕过）vs approval_threshold；币种换算后判断。
- **Eval 勘误**（Commit B）：Recovery n=1 口径、low_stock 语义修正、README 指标分类。
- 测试：38 → **104 passed**；ruff clean；Eval：Task 1.000 / Accuracy 0.833 (n=12) / Single Fault Recovery 1/1 (n=1)。

## Phase 2 完成（2026-11-01，125 passed）

- **原子 Step Claim**（Commit A `4b489b2`）：LogicalStep/StepAttempt 表、条件 UPDATE+RETURNING claim、lease_epoch fencing、heartbeat、UNKNOWN_OUTCOME 独立态。
- **Durable Worker**（Commit B `3a58603`）：DB 可领任务扫描、崩溃恢复（真实子进程 os._exit 后新进程接管 2/2）、checkpoint 版本一致性。
- **Chaos Evaluation**（Commit C）：独立 ExternalEffectLedger、StateConsistencyChecker、2/4/8 跨进程并发（每步恰 1 赢家、重复外部效果 0）、机器可读产物 docs/eval/*.json。
- 关键指标：crash 恢复 2/2、状态一致 5/5、stale fencing 拒绝 2/2。Phase 2 Gate 12/12 通过。

## Phase 3 完成（2026-11-01，144 passed）

- **外部效果安全**（Commit A `581be71`）：能力契约、SQLite 事务化效果账本（invocation vs committed 分离、幂等去重）、非幂等崩溃 → UNKNOWN_OUTCOME。
- **任务级持久化**（Commit B `21b27fa`）：reconcile_task_state 驱动完整任务终态，真实进程终止后完整采购任务恢复。
- **审批完整性**（Commit C）：CAS 并发单赢家、报价过期门禁、版本绑定。
- 崩溃窗口三类动作精确分类：只读 (inv 2/comm 0)、幂等 (inv 2/comm 1)、非幂等 (不重试→UNKNOWN)。0 重复业务副作用。Phase 3 Gate 15/15。

## Phase 4 完成（2026-11-01，187 passed）

- **Commit A `083a2fa`**：审批 CAS 原子性 P0 修复、claim task/plan 绑定、Semantic Portal
  State Graph（语义状态识别 + 稳定签名 + 转换日志）、Safe Browser Action Contract、
  每任务独立 BrowserContext（不再复用 contexts[0]）。
- **Commit B `b447494`**：State-Aware Recovery Controller（故障分类 + 有界恢复 +
  SAFE_STOP）、真实本地 HTTP Portal（8 类故障注入 + 独立 Ground Truth）、
  7 个真实浏览器 e2e 测试、PRICE_RE 货币后缀 bug 修复、机器可读 chaos 产物。
- **Commit C**：五方法对照（A Script 6/7、B Heuristic 7/7、C LLM-mock 7/7、
  D Current Ladder 7/7、E State-Aware 7/7）、消融 4 测试、OOD 模板 2 测试。
- **诚实负结果**：E 成功率与 D 持平（7/7），独立收益是 P50 延迟 −35%（484 vs 750ms）
  与安全验证；C（LLM mock）21 次调用无成功率增益。真实 LLM REAL_LLM_NOT_RUN。
- Phase 4 Gate 18/18 通过。

## 路线图

- **Phase 1**：输入契约（budget_mode、FX、原始+归一化报价分离）、PlanValidator 顺序校验、输入扰动测试 → 当前进行
- **Phase 2**：Durable Runtime（原子 Claim/Lease/Heartbeat/Checkpoint/过期扫描/崩溃恢复/Idempotency Key/Unknown Outcome）+ 故障注入（CrashPoint/FaultInjector）
- **Phase 3**：审批权限模型（Requester/Operator/Approver/Auditor）+ 版本绑定 + 事务一致性
- **Phase 4**：Semantic Portal State Graph + 自愈策略对照（Script/Heuristic/LLM/Escalation/State-Aware）
- **Phase 5**：受约束采购优化（与加权贪心/最便宜可行对照）
- **Phase 6**：独立 Evaluator（最终数据库状态判定 + 故障注入 + DEV/CONFIRM/TEST/OOD 划分）
- **Phase 7**：Recovery Policy Evolution（可选，先 Static→Sandbox→Replay→Confirmation）
- **Phase 8**：真实 LLM 验证（Manifest：provider/model/prompt hash/tokens/cost）
- **Phase 9**：Workbench 展示（Flight Recorder / Chaos Lab / Approval Inspector / Optimization Compare / Experiment Lab）

## 不可违反规则（执行中）

- 默认只连本地 Mock；不自动下单付款；LLM 不得绕过硬约束
- 一切指标与 Baseline 对照；负结果如实报告
- 每个阶段单独 commit；最终测试集不参与调参
