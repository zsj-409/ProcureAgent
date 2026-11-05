# Phase 11B — Baseline Audit

- **Date**: 2026-11-05
- **HEAD**: `938a2ea`（Phase 11A span lifecycle）
- **Baseline**: 379 passed, ruff clean, working tree clean

## 审计结果（Task 0 的 10 项）

| # | 问题 | 现状 | 判定 |
|---|---|---|---|
| 1 | Evaluator 是否真正执行 Candidate | `ReleaseGateEngine.evaluate()` 消费**外部传入的 measurements dict**——它不执行 candidate，只对测量值判定。`unsafe_candidate_demo.py` 是手工构造 measurements 的**演示**，不是真实候选执行。 | **缺口（Commit A 修）** |
| 2 | 是否存在直接读候选预填 Verdict 的路径 | 已移除 `apply_gate_verdict`（11A）；gate_summary 从 gate results 重算。但 measurements 本身可由调用方预填。 | 部分（Commit A 补独立 verifier） |
| 3 | Candidate 是否可能接触隐藏 Ground Truth | 现有 shadow_demo 用 `make_world` 构造世界，candidate 只读 world——不接触 oracle/verifier 内部。但无强制隔离。 | 需测试固化 |
| 4 | Promotion 状态是否真正持久化 | **否**——`PromotionManager._status` 是进程内 dict，`_history` 是 list。跨进程/重启丢失。 | **P0 缺口（Commit B 修）** |
| 5 | Active Version 在哪里保存 | **无 Active Version 概念**——只有 `_status`（candidate → PROMOTED）。 | **P0 缺口（Commit B 修）** |
| 6 | Promotion 是否通过 DB CAS/短事务 | **否**——纯内存 append，无事务、无 CAS、无并发控制。 | **P0 缺口（Commit B 修）** |
| 7 | 版本变更与审计是否原子提交 | 无持久化，无从谈起。 | Commit B 修 |
| 8 | Rollback 后历史任务是否保留原版本绑定 | rollback 只改 `_status`；无 Task-Version 绑定概念。任务 checkpoint/plan 版本绑定是 Phase 2/3 的 `plan_hash`/`lease_epoch`——**全局 rollback 不影响已运行任务**（因为根本无全局版本表）。 | 需测试固化 |
| 9 | MCP 工具授权是否真正校验 Task Ownership | `get_task_execution_status` 调 `task_owner(task_id)`——但 `build_gateway` 的 identity_resolver 是**纯 demo principal**（is_demo=True 全通过），且 `task_owner` 默认 None（跳过）。 | **缺口（Commit C 修）** |
| 10 | Worker Resume 是否可能用过期授权上下文 | resume 无授权上下文——orchestrator.resume 直接执行，不经 gateway/ApprovalGuard。Worker 身份=orchestrator 硬编码。 | 需测试固化 |

## 独立验证能力（已具备，复用）

- `app/procurement/verifier.py` `AllocationConstraintVerifier`：独立重算 requested/allocated/shortfall/total landed cost/hard budget/quote expiry/comparability/supplier eligibility/inventory/delivery/split cap——**全部从 problem + lines 重算，不信任 candidate 预填**。
- `app/procurement/oracle.py`：小世界穷举 oracle（Phase 6）。
- `tests/eval/procurement_world.py` `make_world`：隔离采购世界工厂（fixed seed/snapshot）。

## 关键结论

1. **Release Gate 的 FAIL 必须来自真实候选执行 + 独立 Verifier 重算**，而不是手工 measurements——这是 Commit A 的核心。
2. **PromotionManager 必须持久化到 SQLite + CAS 事务**——这是 Commit B 的核心。
3. **MCP task ownership 必须有真实 task_owner 解析 + 非 demo principal**——这是 Commit C 的核心。
4. 现有 379 tests 全部保留，历史 benchmark 字节不变。

## 诚实边界

- SINGLE_TENANT_DEMO_ONLY：无真实 OIDC/多租户身份。
- 受控变异实验 ≠ 生产安全性的统计保证。
