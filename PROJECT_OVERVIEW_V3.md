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

## 架构速览（现状 v2.0）

自然语言 → `ProcurementInterpreter` → `Planner` → `PlanValidator` → `ProcurementOrchestrator` → `TaskRuntime`（重试/超时/检查点/幂等）→ `ExecutorRouter`（API Executor / Portal Executor 三级升级：固定脚本→启发式→LLM）→ `QuoteNormalizer` → `ExecutionValidator` → `ProcurementScorer`（加权贪心 + 分单 ≤3 行 + shortfall）→ `ProcurementPolicy`（阈值审批）→ `RecommendationFinalizer`。SQLite 持久化 + Trace 审计 + Workbench SPA。

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

Gate：Phase 0 → 1 通过。P0-1/2/3 为 Phase 2 前置；P0-4/5/6 在 Phase 1 修复。

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
