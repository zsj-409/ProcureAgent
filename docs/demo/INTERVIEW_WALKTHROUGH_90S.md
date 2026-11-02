# ProcureAgent 3.0 — 90 秒面试演示

## 开场（0-10s）

> ProcureAgent 是一个**可验证执行可靠性**的企业采购 Agent Runtime。它不是一个
> 调 API 的 demo，而是回答一个工程问题：**当供应商门户、审批和 Worker 都出故障时，
> Agent 如何证明自己不会重复下单、不会越权审批、不会状态错乱？**

## 演示 1 — Durable Runtime（10-30s）

打开任务 Flight Recorder：
- 每个逻辑步骤有 **原子 Claim + Lease/Fencing**，两个 Worker 永远不会同时执行同一
  步骤；
- 现场杀掉 worker 进程（`test_full_task_recovery.py` 展示了真实 `os._exit` 恢复）：
  新 worker 重领步骤、效果账本保证**外部副作用恰好执行一次**；
- 副作用不确定时进入 **UNKNOWN_OUTCOME**，绝不假装成功。

## 演示 2 — Safe Decision Making（30-55s）

打开 Procurement Optimization Lab：
- 硬预算、报价时效、FX 方向、分单上限、库存、交期全部是**硬约束**（整数规划）；
- 独立 Verifier 复核每个方案，**覆盖不足的方案绝不冒充全量成功**；
- 诚实边界：成本节省只在全履约配对里算，Cluster Bootstrap 区间含零——
  **我不宣称统计显著**，我展示的是约束正确性。

## 演示 3 — Evidence-Based Recovery（55-80s）

打开 Chaos Recovery Lab / Agent Experiment Lab：
- 语义状态图 + 动作安全契约：页面漂移、会话过期、prompt injection 全部有恢复路径，
  不安全的动作被**白名单拦截**；
- Phase 7 做了 **360 次真实 Chromium 运行**：三策略成功率相同（饱和），Cost-Aware
  减少的是观察次数——**这是诚实的负结果**：它证明"更聪明的策略"不总能提升成功率，
  但能降低运行成本。

## 收尾（80-90s）

> 三条主线：Reliable Runtime、Safe Decision Making、Evidence-Based Recovery。
> Workbench 把它们统一成可观察、可追溯、可离线演示的完整系统。
> 所有结果来自**真实浏览器 + 独立 ground truth**；真实 LLM 未运行（标注
> REAL_LLM_NOT_RUN），模拟与真实严格分开报告。
