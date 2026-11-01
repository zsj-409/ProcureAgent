# ProcureAgent V3 任务日志

> 任务：企业级可靠执行 Agent 重构与独立评测实施（ProcureAgent 3.0）
> 开始：2026-11-01（CST, UTC+8）
> 仓库：github.com/zsj-409/ProcureAgent（zip 快照导入，本地 git 初始化）
> 工作目录：D:\agent_work\procureagent3\src\ProcureAgent-main

## 环境与基线

- 依赖安装于 Python 3.11 venv（3.14 无法构建 pydantic-core，需 3.11）。
- Playwright chromium 已安装，headless 可启动。
- 基线：`pytest` → **38 passed, 3 warnings (57.6s)** — 与 README 声称一致。
- Eval runner 实测（本机 2026-11-01）：
  - Task Success 0.938 / Interpretation 0.750 / Plan Validation 1.000
  - Executor 0.618 / **Recovery 1.000（n=1）** / **Recommendation Accuracy 0.667–0.750（README 声称 1.000，不可复现）**

## Phase 0 审计（已完成）

交付物：
- `docs/audit/PROCUREAGENT_V3_BASELINE_AUDIT.md` — 全模块能力标记 + 基线指标 + 9 项发现
- `docs/audit/V3_CORRECTNESS_AND_EVAL_AUDIT.md` — §16 的 9 个问题逐条回答（代码位置/问题/复现/严重度/修复/测试/阻断）

关键 P0 发现：
1. 所有 API 无认证授权（approve 匿名可调用）→ P0
2. 无原子 Step Claim，并发 resume 可双重执行同一 supplier → P0
3. 后台任务仅进程内线程池，进程终止即丢失执行权 → P0
4. 不同币种报价直接比较，无汇率处理，estimated_total 恒标 USD → P0
5. max_budget 语义不明（硬约束 vs 可审批例外未区分）→ P0
6. PlanValidator 不校验顺序与 FINALIZE → P0
7. Eval 恢复指标 n=1、推荐准确率是预填标签匹配（实测 0.667 非 1.000）、计划验证是自证 → P1
8. Workbench 无崩溃恢复可视化、审批无身份边界 → P2
9. 真实 LLM 无运行记录（REAL_LLM_NOT_RUN）→ P2

Gate：Phase 0 → Phase 1 **通过**；P0 修复（1/2/3）为 Phase 2 前置。

## Phase 1（已完成，2026-11-01）

### Commit A `6ed59ad` — PlanValidator 重构 + 审批封堵
- `PlanValidator`：依赖顺序校验（COLLECT→NORMALIZE→SCORE→CHECK_POLICY→FINALIZE），
  支持合法人工审查路径（无 FINALIZE 的 review 计划合法）。
- 运行时强制：`orchestrator.run/resume` 双重校验；旧持久化 plan 在 resume 时重验，
  无效 plan 标记 FAILED，绝不进入执行器。
- 审批安全：`app/security/`（ApprovalGuard + Principal + DemoIdentityProvider）；
  默认拒绝未认证 approve/reject（401 AUTH_REQUIRED）；请求体 approver 永不信任；
  单事务审批（去掉中间 APPROVED 状态）；ApprovalRecord 绑定 recommendation_version。
- 测试：7 个审批守卫 + 13 个 plan validator。

### Commit B（本次）— FX/报价/预算语义/解释器/Eval 勘误
- `app/procurement/fx.py`：FxService（方向化汇率、MISSING/STALE/INVALID_DIRECTION、离线 fixture）。
- `QuoteNormalizer`：保留 raw_quote + 归一化参考价；不可比较报价标记 incomparable。
- `ProcurementScorer`：只用可比较参考价排名；无可比报价明确报错。
- `ProcurementPolicy` 重写：user_budget_limit（硬预算，HARD 不可审批绕过）vs
  approval_threshold（内部审批阈值）；币种换算后比较；TOTAL_UNKNOWN/CURRENCY_UNRESOLVED。
- `interpreter`：预算模式（HARD/SOFT/APPROVAL_GATE）、币种提取、数量锚定修复。
- Eval Runner：`single_fault_recovery {pass, n}` 口径（不再称 Recovery Rate）；
  `low_stock_shortfall_routes_to_approval`（v2 语义修正）；accuracy 带 n。
- README：指标分类 HISTORICAL/REPRODUCED/CURRENT/NOT_EVALUATED。
- 测试：+66 → **104 passed**，ruff clean。

## 当前状态（Phase 1 末）

- `pytest`：**104 passed**；ruff：clean。
- Eval：Task Success 1.000、Single Fault Recovery pass=1/1 (n=1)、
  Recommendation Accuracy 0.833 (n=12)。
- 交付文档 8 份全部生成（contracts ×3、experiments ×4、audit reconciliation ×1）。

## 未解决（明确归属）

- **P0-2 并发 Step Claim 原子性** → Phase 2 首项。
- **P0-3 跨进程恢复（lease/heartbeat）** → Phase 2 首项。
- FX fixture 为离线示意（Phase 3+ 接真实数据源）。
- 过期报价未在 scoring 硬性拒绝（Phase 2/3）。
- 真实 LLM 未运行（REAL_LLM_NOT_RUN，无凭据）。

## 下一阶段 Gate

Phase 1 八项标准全部满足 → **Phase 2（Durable Runtime）可开始**：
原子 Step Claim、Lease/Heartbeat、并发执行排除、崩溃恢复、故障注入矩阵。

## Phase 2（已完成，2026-11-01，125 passed）

### Commit A `4b489b2` — 原子 Step Claim + Fencing Lease
- `app/runtime/step_claim.py`：LogicalStep/StepAttempt（新表，SQLite WAL + busy_timeout 30s）
- 原子 claim（UPDATE...RETURNING，仅 PENDING/RETRY_SCHEDULED/过期 CLAIMED 可领取）
- lease_epoch 每次 claim +1（fencing token）；complete/fail/mark_unknown 均按 owner+epoch 条件更新
- UNKNOWN_OUTCOME 独立状态，不可被自动重领（不盲目重试）
- 可注入时钟（RealClock/FixedClock）+ 幂等创建（ON CONFLICT DO NOTHING）
- 测试：10 个（含双线程并发单赢家）

### Commit B `3a58603` — Durable Worker + 崩溃恢复
- `app/runtime/durable_worker.py`：DB 可领任务扫描 + heartbeat 循环 + 优雅停机
- `TaskRepository.find_reclaimable_tasks`：无活跃租约的非终态任务
- `orchestrator.execute_step`：worker 驱动的单步执行入口
- **真实子进程崩溃恢复**：os._exit() 后新进程接管（2/2 成功）
- stale worker 的旧 epoch 无法提交（fencing 实证）
- Checkpoint 版本一致性：plan_hash + action_input_hash 绑定
- 测试：5 个（含真实子进程 kill + 恢复）

### Commit C（本次）— Chaos Evaluation
- `app/runtime/chaos.py`：ExternalEffectLedger（独立账本）、FaultInjector、StateConsistencyChecker
- 跨进程并发：2/4/8 workers 同一文件 DB → 每步恰 1 赢家、外部效果恰 1 次（6 测试）
- crash 后恢复：ledger 证明"外部已执行、本地未提交"窗口
- 机器可读产物：docs/eval/*.json（7 个）
- 报告：docs/runtime/*（4 份）+ docs/experiments/*（4 份）

## 当前状态（Phase 2 末）

- `pytest`：**125 passed**；ruff：clean
- 关键指标：并发 claim 单赢家 2/2/4/8 全过、crash 恢复 2/2、状态一致 5/5、
  重复外部效果 0、stale fencing 拒绝 2/2
- Phase 2 验收 Gate 12/12 通过

## 未解决（明确归属）

- 任务级 COMPLETED 终态转换仍由 orchestrator 完成（durable worker 只驱动单步）→ Phase 3
- 报价时效在 checkpoint 复用处的硬门禁是 policy 层非 claim 层 → Phase 3
- ledger 对真正并行同文件写入无锁（每场景独立 ledger）→ Phase 6
- 真实 LLM / 真实 CDP 浏览器 REAL_LLM_NOT_RUN

## 下一阶段

Phase 2 验收通过 → **Phase 4（Adaptive Portal Recovery）可开始**（按任务书顺序，
Phase 3 审批治理为前置但本轮不展开 Portal RSI）。

## Phase 3（已完成，2026-11-01，144 passed）

### Commit A `581be71` — 外部效果安全
- 工具能力契约（ExternalCapability：READ_ONLY/REMOTE_IDEMPOTENT/RECONCILABLE/NON_IDEMPOTENT/UNKNOWN）
- SQLite 事务化外部效果账本（external_invocations vs external_committed_effects，幂等键去重）
- worker 恢复按能力决策：非幂等工具崩溃 → UNKNOWN_OUTCOME（绝不盲目重试）
- Claim SQL 作用域审查（无跨 Step 误领）+ 12 测试

### Commit B `21b27fa` — 任务级持久化
- `reconcile_task_state`：从持久化步骤结果重建任务终态（幂等 finalize）
- durable worker 驱动完整任务（非仅单步）
- 真实进程终止后完整采购任务恢复测试（WAITING_APPROVAL，2 报价，2 次调用）

### Commit C（本次）— 审批完整性与 Chaos
- 审批 CAS（并发 approve/reject 单赢家）+ 报价过期门禁（QUOTE_EXPIRED）+ 版本绑定
- 审批完整性 6 测试（并发竞态、过期报价、未认证回归、角色拒绝）
- 报告：EXTERNAL_EFFECT_CAPABILITY_CONTRACT / UNKNOWN_OUTCOME_RECOVERY_SPEC /
  TASK_LEVEL_DURABILITY / APPROVAL_INTEGRITY_V3 / PHASE3_CHAOS_RESULTS / PHASE3_FINAL_REPORT

## Phase 4（进行中，2026-11-01）

### Task 0 — Phase 3 正确性回归（P0 修复）
- **P0 审批事务原子性**：approve/reject 的 CAS 状态转换与审批审计行改为**同一事务**
  （`approval_cas_transition`）——消除"任务 COMPLETED 但无审批记录"的崩溃窗口。
- **P1 claim 身份绑定**：claim() 支持可选 task_id/plan_hash 绑定（WHERE 校验），
  防止调用层拿其他任务的 step_id 获得执行权。
- 新增 `test_phase4_preconditions.py`（3 测试）。
- 审计报告：`docs/portal/PHASE4_PRECONDITION_AUDIT.md`（0.3 租户隔离如实标注 NOT IMPLEMENTED）。

### Commit A — Semantic Portal State（本次）
- `app/runtime/portal_state.py`：语义状态识别（8 类）、稳定状态签名（排除随机 class/id/
  时间戳/query，敏感于产品/报价版本/会话/租户）、状态转换日志。
- `app/runtime/safe_actions.py`：有限动作白名单 + 安全执行器（前置状态校验、唯一性检查、
  后置条件验证、预算、PAGE_HOSTILE 阻断、不可信页面降级只读）。
- `PortalSession` 修复：CDP 不再复用 `contexts[0]`，每任务独立 BrowserContext（Task 5）。
- 新增 `test_phase4_state_actions.py`（17 测试）。
- 文档：`docs/portal/PORTAL_BASELINE_AUDIT.md`、`SEMANTIC_PORTAL_STATE_GRAPH.md`、
  `SAFE_BROWSER_ACTION_CONTRACT.md`。

### Commit B — State-Aware Recovery + Portal Chaos（本次）
- `app/runtime/recovery_controller.py`：故障分类（11 类）+ 有界恢复策略（确定性低开销优先、
  预算耗尽/会话过期/歧义/危险 → SAFE_STOP）+ Durable Runtime 绑定（task/step/lease/plan hash）。
- `tests/eval/portal_world.py`：真实本地 HTTP 供应商 Portal（登录/搜索/结果表/卡片/报价详情/
  分页/弹窗/会话/8 类故障注入 + 独立后端 Ground Truth + snapshot hash 环境隔离）。
- `tests/eval/portal_evaluator.py`：独立 Evaluator（Ground Truth 校验、verified/recovery/
  safe containment 分离、分子分母指标）。
- 浏览器端到端测试 7 个（真实 Chromium + 真实本地 Portal）：F1 选择器漂移语义恢复、
  F2 布局漂移卡片提取、F4 授权关弹窗、F5 会话过期安全停止、F7 提示注入安全、F8 歧义/危险安全停止、
  环境隔离。修复 `parse_quote_rows` 的 **PRICE_RE 货币后缀 bug**（"99.99 USD" 无法提取）。
- 机器可读产物（docs/eval/）：manifest、world registry、fault injection、recovery results、
  cost ledger。结果：verified 5/7、recovery 4/7、safe containment 2/7、quote 5/7。
- 文档：`docs/portal/STATE_AWARE_RECOVERY_POLICY.md`、`docs/experiments/PORTAL_CHAOS_BENCHMARK.md`。

- `pytest`：**144 passed**；ruff：clean
- 崩溃窗口三类动作精确分类：只读（invocation 2, committed 0）、幂等（2, committed 1）、
  非幂等（不重试 → UNKNOWN，committed 保持 1）
- 全部场景 0 重复业务副作用；账本多进程 4×20 写入无丢失
- Phase 3 验收 Gate 15/15 通过

## 未解决（明确归属）

- 恢复样本每族仍小（1-5），非总体率 → Phase 6 扩展故障矩阵
- demo 审批身份非生产认证；跨租户未实测
- 非幂等工具无自动恢复路径（人工对账为终态，安全权衡）
- 真实 LLM / CDP REAL_LLM_NOT_RUN

## 下一阶段

Phase 3 验收通过 → **Phase 4（State-Aware Portal Recovery）可开始**。
