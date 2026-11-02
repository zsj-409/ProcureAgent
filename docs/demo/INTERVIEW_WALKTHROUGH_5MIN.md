# ProcureAgent 3.0 — 5 分钟面试演示

## 0-30s — 问题定义

企业采购 Agent 的现实约束：供应商 API/门户不可靠、审批必须严格、进程会崩溃、
外部副作用可能重复。ProcureAgent 回答：**故障后为什么能恢复？如何证明供应商不
被重复执行？审批与状态如何一致？自适应网页恢复在什么条件下值得？**

## 30s-1min — 架构一句话

三个正交能力：
1. **Durable Runtime**（app/runtime、app/agent）：原子 Step Claim、Lease/Fencing、
   效果账本、跨进程崩溃恢复、UNKNOWN_OUTCOME 语义。
2. **Safe Decision Making**（app/procurement）：硬约束整数规划、独立 Verifier、
   独立 Oracle、审批 CAS + 版本绑定 + 默认拒绝。
3. **Evidence-Based Recovery**（app/runtime/portal_*）：语义页面状态图、动作安全
   契约、State-Aware 恢复、成本感知策略。

Workbench（app/webui + app/api）统一展示。

## 1-2min — 演示 1：真实崩溃恢复（最有说服力）

`tests/test_full_task_recovery.py`：子进程执行采购步骤 → `os._exit` 硬杀 →
新 worker 重领 → reconcile 到 WAITING_APPROVAL。**外部效果账本显示恰 2 次
调用**（1 次崩溃前 + 1 次恢复后，无重复）。关键：效果记账与状态提交在同一事务，
崩溃窗口不产生"状态变了但没记录"。

## 2-3min — 演示 2：审批与状态一致性

- 未认证审批 → 401 AUTH_REQUIRED（默认拒绝）；
- 并发 approve/reject → CAS 状态转换，恰一个赢，败者 409 APPROVAL_RACE_LOST；
- 推荐版本绑定：报价/方案变了，旧审批自动失效；
- **approver 字段来自请求体永不信任**——身份只来自认证层。

## 3-4min — 演示 3：受约束优化 vs 贪心

Phase 6 的 28 个世界（14 族 × 2 seeds）：
- Optimizer：**18/28 全量履约、0 约束违反**；
- Greedy：14/28 全量，8 个违反（分单上限、过期报价等）；
- 独立 Oracle（穷举）验证 CBC 正确性；Cluster Bootstrap CI [0.0, 6.4] 含零 →
  **成本节省不显著，但约束正确性是确定性的**。这是面试里最诚实的一页。

## 4-5min — 演示 4：恢复策略评测 + 局限

- Phase 4 消融：5 方法 × 7 故障真实浏览器。State-Aware 与 Ladder 成功率持平
  （7/7），P50 484ms vs 750ms（低 35%）——收益在延迟与安全验证。
- Phase 7：360 次真实 Chromium、120 案例 × 3 策略三分裂。成功率饱和（100%
  正确目标、0 unsafe），Cost-Aware 观察数 40→20/0。**F 与 E 无成功率差异**。
- 局限清单：SINGLE_TENANT_DEMO_ONLY、REAL_LLM_NOT_RUN、观察次数≠金额成本、
  成功率饱和无法做显著性检验。

## 收尾

> 这个项目的取舍是：**用独立评测和诚实边界取代"效果图"**。每个数字都能追溯到
> commit、artifact 和测试；每个负结果都如实标注。这才是可验证的 Agent Engineering。
