# ProcureAgent 3.0 — Architecture Explanation

## 分层架构

```
┌─────────────────────────────────────────────────────────┐
│  Workbench (app/webui + app/api)                        │
│  任务创建/审批/供应商 + Observatory (5 Labs)            │
├─────────────────────────────────────────────────────────┤
│  Orchestration (app/agent)                              │
│  Orchestrator → Planner → Interpreter → PlanValidator   │
│  DurableWorker: Claim/Lease/Fencing/效果账本/崩溃恢复    │
├─────────────────────────────────────────────────────────┤
│  Execution (app/executors)                              │
│  ExecutorRouter → API Executor / Portal Executor        │
│  Portal: SemanticState + SafeActionExecutor + Recovery  │
├─────────────────────────────────────────────────────────┤
│  Domain (app/procurement)                               │
│  Normalizer → FX → Scorer → Policy → Optimizer → Verifier│
├─────────────────────────────────────────────────────────┤
│  Foundation (app/infrastructure + app/security)         │
│  SQLite(WAL) / Settings / ApprovalGuard / Trace         │
└─────────────────────────────────────────────────────────┘
```

## 三个核心机制

### 1. Durable Runtime — 崩溃恢复为什么可靠

- **原子 Step Claim**：`UPDATE ... WHERE step_id=? AND status='PENDING'`，rowcount==1
  才是赢家——两个 Worker 并发时恰一个获得执行权（fencing token 绑定）。
- **效果账本（external effect ledger）**：每个有副作用的动作先记 intent（幂等键），
  执行后记结果。崩溃后重放只看**已提交**效果，绝不在未知状态上重试非幂等动作。
- **UNKNOWN_OUTCOME**：NON_IDEMPOTENT 动作在网络错误后不能假设成功/失败——状态机
  进入 UNKNOWN_OUTCOME，需要人工/策略决定，绝不自动重做或假装完成。
- **跨进程恢复**：SQLite WAL + busy_timeout 保证崩溃后数据一致；新 worker 扫描
  过期 lease，reconcile 状态到合法下一步。

### 2. Safe Decision Making — 预算与审批为什么不透明

- **默认拒绝**：未认证不能审批；approver 字段来自请求体永不信任；身份只来自
  Auth 层。
- **审批 CAS + 版本绑定**：approve/reject 用条件状态更新（原子），并发恰一个赢；
  审批绑定 recommendation_version（推荐 JSON 的 hash），方案变化自动失效。
- **FX 方向化**：汇率有源、有时、有方向，绝不默认 1.0，绝不覆盖原始币种；
  STALE/MISSING/INVALID_DIRECTION 都是显式错误。
- **硬约束优化**：PuLP/CBC 两阶段词典序（先最大化覆盖、再最小化成本）+ Decimal
  复核（预算容差 0.005），违反→INVALID；SOLVER_UNRESOLVED 明确标注非最优。
- **独立 Verifier + Oracle**：不信任求解器自证；独立约束验证器重算所有边界，
  穷举 Oracle 在小世界提供 ground truth。

### 3. Evidence-Based Recovery — 网页恢复为什么值得

- **语义状态图**：8 类语义状态（LOGIN/DASHBOARD/SEARCH/RESULTS/QUOTE_DETAIL/
  SESSION_EXPIRED/ERROR/UNKNOWN），稳定签名排除随机 class/id/时间戳。
- **动作安全契约**：有限白名单（OPEN_ALLOWED_PAGE/FILL_SEARCH/SUBMIT_SEARCH/
  SELECT_PRODUCT/NEXT_PAGE/EXTRACT_QUOTE/STOP_AND_ESCALATE 等），每动作有前置
  状态、后置条件、预算；PAGE_HOSTILE（页面含交易措辞）直接阻断。
- **恢复分层**：Fixed Script → Heuristic → LLM（可选）→ State-Aware Ladder；
  Cost-Aware 策略按状态+预算选层，安全门禁不可绕过。
- **评测隔离**：独立 Portal World（本地 HTTP + 真实浏览器 + 故障注入器），
  Evaluator 只读后端 ground truth，策略看不到隐藏标签。

## 数据流（一个采购任务）

1. 用户输入 → Interpreter（LLM 或规则 fallback）→ ProcurementRequest（Schema 校验）
2. Planner → PlanValidator（依赖序、无循环）→ LogicalStep 序列
3. DurableWorker：逐步骤 Claim → Execute（API/Portal）→ 效果记账 → SUCCEEDED
4. 报价归一化 → FX → Scorer（Greedy）或 Optimizer（可选）→ 推荐 + 版本 hash
5. 达到阈值 → WAITING_APPROVAL → 认证审批（CAS）→ COMPLETED
6. 全程 Trace 记录到 Flight Recorder

## 安全边界

- 无真实下单/付款路径（代码层面无 payment/checkout 模块）。
- 默认只连本地 Mock Supplier；真实接口需显式授权配置。
- 多租户：SINGLE_TENANT_DEMO_ONLY（诚实标注，不宣称生产级）。
- 真实 LLM：REAL_LLM_NOT_RUN（无凭据时完全离线）。
