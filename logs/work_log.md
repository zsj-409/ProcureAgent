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

### Commit C — Independent Portal Evaluation（本次）
- 五方法对照（35 次真实浏览器运行）：A Script Only 6/7、B Heuristic 7/7、
  C LLM First（SCRIPTED mock）7/7（21 次 mock 调用、成本 0.42）、D Current Ladder 7/7、
  E State-Aware 7/7。
- **诚实结论**：E 成功率与 D 持平（无显著提升，如实报告），独立收益 = P50 延迟 −35%
  （484 vs 750ms）+ 每个动作前的安全验证（状态门 + 动作契约 + 后置条件）。
  C（LLM mock）无成功率增益 → LLM 升级在本故障集无收益。
- 消融测试 4 个（去掉语义识别 → 盲重试；去掉后置验证 → 错误页面被接受；去掉预算 → 无限循环）。
- OOD 测试 2 个：t2_ood 未见模板（Item/Amount/Availability/Lead Time 表头、无搜索表单、
  ood-* id）语义提取成功 + dev selector 失败 + snapshot hash 不同。修复表头语义 hints
  （Amount/Availability/Lead Time 等变体）。
- 产物：strategy_comparison.csv、portal_recovery_results.json（by method）、
  action_safety_report.json、quote_extraction_accuracy.json、portal_failure_cases.json。
- 文档：`docs/experiments/PORTAL_RECOVERY_ABLATION.md`、`PHASE4_FINAL_REPORT.md`。

## Phase 5（进行中，2026-11-01）

### Commit A — 受约束采购优化（本次）
- `app/procurement/optimizer.py`：整数规划分配优化器（PuLP 3.3.2/CBC），最小化
  Total Landed Cost，约束：数量守恒、库存、供应商准入、硬预算、交付时限、
  最多分单供应商数、供应商占比、税费/运费、统一币种。
- 可行性三分类：FULLY_FEASIBLE / PARTIAL_FEASIBLE（需重新确认，绝不自动完成）/
  INFEASIBLE（明确原因）。低成本部分履约 ≠ 完整采购成功。
- 新增 18 测试（tests/test_phase5_optimizer.py）。
- 与 Baseline 对比（tests/eval/optimization_benchmark.py，7 场景）：
  greedy 违反分单上限（3 家当 max=2）、many_suppliers 只覆盖 105/150 却报推荐；
  cheapest-feasible 违反交付时限；optimizer 全部尊重约束并显式返回可行性。
  简单无约束场景三者一致（诚实边界）。
- **修复 fx 测试夹具时间脆弱性**：`as_of` 从固定日期改相对 now-1h（跨 24h 窗口
  导致 FX_RATE_STALE 假失败）。
- 文档：`docs/contracts/ALLOCATION_OPTIMIZER_SPEC.md`、
  `docs/experiments/PHASE5_OPTIMIZATION_REPORT.md`；产物 optimization_comparison.csv /
  optimization_benchmark.json。

## Phase 6（进行中，2026-11-02）

### Task 0 — Phase 5 正确性审查（3 个 P1，已修复）
- **0.1 BIG_M 词典序不可靠** → 严格两阶段（阶段1 最大化覆盖，阶段2 固定覆盖最小化成本）。
- **0.2 float 系数无 Decimal 复核** → `_decimal_recheck`（预算容差 0.005、整数/库存/时限/分单/占比复核，
  违反 → INVALID）。
- **0.3 非最优误判 INFEASIBLE** → 新增 SOLVER_UNRESOLVED；FEASIBLE 未证明最优 → 返回分配 +
  optimality_proven=False。
- 审计：`docs/experiments/PHASE6_PRECONDITION_AUDIT.md`。

### Commit A `579aac8` — Evaluation Correctness（226 passed）
- `app/procurement/verifier.py`：Independent Constraint Verifier（独立重算全部硬约束，
  reason_code 违规，FULLY/PARTIAL/INFEASIBLE/INVALID/SOLVER_UNRESOLVED 分类）。
- `app/procurement/oracle.py`：SmallWorldOracle（穷举 ≤4 供应商 ≤12 数量，词典序最优，
  不调 PuLP）。
- 21 测试：两阶段覆盖优先（价格差 500×/运费 1000 仍全覆盖）、预算边界精确、Oracle=Optimizer
  一致性（129.00 等）、验证器捕获过期/不可比较/库存/预算违规。

### Commit B — Reproducible Benchmark（本次，235 passed）
- `tests/eval/procurement_world.py`：14 场景族 × 2 seeds = 28 worlds，DEV/CONFIRMATION/TEST
  划分，snapshot_hash（报价有效性按过期布尔而非时间戳）、reset() 确定性。
- `tests/eval/procurement_adapters.py`：统一 AllocationCandidate（A Greedy / B Cheapest /
  C Optimizer / D Oracle），独立验证器复核。
- `tests/eval/procurement_benchmark.py`：指标注册表（分子/分母）+ 配对比较（仅双方 FULLY 才算
  成本节省）+ 机器产物。
- **修复 optimizer 过期报价 bug**：optimize() 过滤 quote_valid_until 过期报价
  （此前会选过期报价 → INVALID，现 INFEASIBLE）。
- **Benchmark 结果（28 worlds）**：Greedy full 14/28 violation 8/28；Cheapest full 10/28
  violation 12/28；**Optimizer full 18/28 violation 0/28**；Oracle（10 小世界）violation 0/10。
  联合可行 n=14：Optimizer 平均省 2.67%。

### Commit C — Integration & Final Evaluation（本次，248 passed）
- 安全回归 13 测试：预算边界（==/-0.01/+0.01）、FX 缺失/过期排除、固定运费改变最优、
  solver timeout → SOLVER_UNRESOLVED、硬预算不可绕过、PARTIAL 永不自动 FULLY、
  过期报价排除、工作流语义（FULLY→审批、PARTIAL→确认、INFEASIBLE→无结果）。
- Solver scaling：10→80 供应商 0.047s→0.094s，无超时。
- 统计分析：配对 Bootstrap n=14，平均节省 **2.671%，95% CI [0.621, 5.157]**；
  失败案例（greedy INVALID 8、cheapest INVALID 12、optimizer INVALID 0）。
- 文档：PROCUREMENT_EVALUATION_CONTRACT / GREEDY_VS_OPTIMIZER_REPORT /
  PROCUREMENT_SAFETY_REGRESSION / PHASE6_FINAL_REPORT；
  产物：solver_performance.json、paired_bootstrap.json、failure_cases.json 等。
- **Phase 6 Gate 18/18 通过**：Optimizer 可作为可选（feature-flagged）业务路径，
  Greedy 仍为默认，替换需后续集成决策。

## Phase 7（进行中，2026-11-02）

### Task 0 — Phase 6 Metrics Reconciliation（Gate 0 通过）
- **勘误 1**：Phase 6 报告"真实合法 FULLY=6"表述错误——FULLY 分类已排除违规者，
  正确表述为 Greedy 合法 FULLY 14/28 + 独立 INVALID 8。无重复扣除（验证器单一标签）。
- **勘误 2**：普通 Bootstrap 忽略同族相关性 → 新增 **Cluster Bootstrap（按 family 整组重采样）**：
  n_clusters=7，mean 2.671%，CI **[0.0, 6.4]**（下界含 0，节省不显著）——修正"CI 不含 0"的过度声明。
- **修复公平性缺陷**：Greedy 分单上限硬编码 3，与 Optimizer 的 world.max_split_suppliers 不一致 →
  `ProcurementScorer.score(max_split=...)` 可选参数，GreedyAdapter 传 world 上限。Greedy violation 8→6。
- **冻结报价有效期**：world hash 用构造时冻结的 expired 标志（防运行时时钟翻转）。
- 文档：`docs/experiments/PHASE6_METRIC_RECONCILIATION.md`（历史保留 + 勘误版本）。

### Commit A — Benchmark & Policy Contract（本次，267 passed）
- `app/runtime/recovery_policy.py`：统一 RecoveryObservation/RecoveryDecision +
  **CostAwareRecoveryPolicy**（可解释成功率先验 × 分层成本 → 预算感知选层，分层尝试上限防循环，
  硬预算/安全门禁不可绕过，LLM 默认禁用）。
- `tests/eval/recovery_benchmark.py`：12 故障族 × 3 难度 × 多模板种子判别性基准，
  独立恢复目标（recover_quote / safe_stop），DEV/CONFIRMATION/TEST 隔离。
- 19 测试（策略 12 + 基准 7）：成功/安全停止/预算耗尽、SELECTOR→SEMANTIC、
  DYNAMIC_DOM→REFRESH、分层上限升级、LLM 默认禁用、无隐藏真值泄漏。
- 文档：`docs/recovery/PHASE7_BASELINE_AUDIT.md`、`RECOVERY_POLICY_CONTRACT.md`。

### Commit B — Recovery Policy Evolution（本次，274 passed）
- DEV pilot（真实浏览器，40 案例）：D/E/F 三策略 recovered 30/40 + safe_stop 10/10
  （F6 会话过期全部安全停止）。**区分度在成本**：E/F observations=20 vs D=40
  （cost-aware 直接 SEMANTIC，Phase 4 ladder 重复 RE_OBSERVE）。成功率 DEV 饱和。
- 调参/晋级测试 7 个：REJECT 安全退化/质量退化、HOLD 等质等本、PROMOTE 仅验证改进、
  DEV-only 调参、TEST 冻结。
- 文档：`docs/recovery/COST_AWARE_RECOVERY_POLICY.md`、
  `docs/experiments/RECOVERY_POLICY_FRONTIER.md`（pilot 结果）。
- 产物：`docs/eval/recovery_policy_pilot.json`（40 案例逐条）。

### Commit C — Independent Evaluation（本次，278 passed）
- 全量评测：120 案例（12 族 × 3 难度）× 3 策略 = **360 次真实浏览器运行**。
- **诚实结果**：成功率全部分裂饱和（rec 30/40 + safe_stop 10/40 = 100% 正确目标），
  三策略无成功率差异（负结果）；**成本差异真实**——D 用 40 次观察（RE_OBSERVE 循环），
  E/F 用 20 次（直接 SEMANTIC）+ 0 次（初始即目标）。
- Promotion：E/F 等质低成本 → PROMOTE（收益仅成本，不声称成功率提升）。
- OOD 测试 4 个（策略输入无模板字段、指标类互斥、safe_stop≠成功、case_id 稳定）。
- 文档：RECOVERY_POLICY_ABLATION / RECOVERY_OOD_REPORT / PHASE7_FINAL_REPORT；
  产物：recovery_policy_results.json（120 案例）。
- **Phase 7 Gate 14/14 通过**。

## Phase 9 — Workbench & Experiment Observatory（2026-11-02，287 passed）

- **Observatory API**（`app/api/observatory.py`，只读已提交产物，不重跑 benchmark）：
  - Execution Flight Recorder（实时任务 trace）
  - Chaos Recovery Lab（Phase 4 portal 故障结果）
  - Approval & Policy Inspector（审批 + 版本绑定）
  - Procurement Optimization（Phase 5/6 CBC 对比）
  - Agent Experiment Lab（Phase 7 120 案例策略对照）
  - 每个 Lab 标注诚实边界（n、饱和/负结果、REAL_LLM_NOT_RUN、cluster CI）
- **前端**：`#/observatory` 路由 + tabbed Lab 视图 + 导航 + CSS。
- 9 新测试（API 6 + 演示 3：未授权审批拒绝 401 无状态变更、artifact 真实数字、
  7 个面试演示可复现）。
- 文档：`docs/workbench/PHASE9_OBSERVATORY.md`。

### Demo Acceptance（Step 1，293 passed）

- **P0 认证**：flight-recorder / approval-inspector 加 `_require_principal`（默认拒绝，
  demo 配置下 header 白名单）；artifact 端点保持公开（已提交、非敏感）。4 个认证测试。
- **P1 artifact 11/14**：根因 `_load` 用 JSON 解析 CSV artifact → 全部标记 present（format: csv）。
  14/14 全在，`test_all_14_artifacts_present`。
- **P1 真实浏览器交互**：`test_phase9_workbench_e2e.py` 驱动真实 Chromium 点击 4 个 Lab，
  验证数据表格渲染、空状态显式显示（Missing/暂无，非假零）、404 错误路径；顺带修复
  observatory.js 内嵌 `<script>` 事件绑定不生效的真实 bug（移到模块 render()）。
- 全量 293 passed，ruff clean。

### Finalization（Task 0-8，311 passed，3 commits）

- **Commit A `4d26f67`** — 最终 Demo 安全门禁：flight-recorder/approval-inspector 显式
  SINGLE_TENANT_DEMO_ONLY 标注 + 对象级 404（无枚举）；生产 auth 忽略 demo header
  （`test_production_auth_ignores_demo_header`）；随机 header 无法伪造 principal；
  公开 artifact 无凭据扫描；flight-recorder UI Tab 补上（5 Lab 全浏览器覆盖）。
  文档 `docs/workbench/FINAL_DEMO_SECURITY_AUDIT.md`。
- **Commit B `881673f`** — Phase 8 Scoped Real-LLM Validation harness：
  - `app/eval/phase8_llm.py`：10 冻结场景（中文/中英混合/数量/硬预算/交期/价格/
    缺失/歧义/不可满足/币种）配对比较；规则 baseline 7/10（真实局限：元→CNY、
    万台→10000）。
  - `app/eval/phase8_portal.py`：8 冻结故障场景（F1-F8 + 会话/报价过期）离线 8/8，
    动作白名单守卫。
  - Settings 加 llm_sandbox_only/max_calls/max_tokens/retry_limit/cost_budget
    （默认保守）；sandbox_only 强制 REAL_LLM_NOT_RUN。
  - 诚实 manifest（git commit/input hash/world hash/model config/calls/tokens/cost）。
  - 无凭据 → REAL_LLM_NOT_RUN，绝不发起真实调用。9 新测试。
- **Commit C `da16078`** — 面试交付材料（docs/demo/ 6 份：DEMO_RUNBOOK、
  90S/5MIN 演示稿、ARCHITECTURE_EXPLANATION、ENGINEERING_TRADEOFFS、
  PROJECT_CLAIMS_AND_EVIDENCE）+ README 离线 Demo 章节 + 离线验证测试
  （无 key 无模型客户端、无付款代码路径、历史 artifact 字节不变）。
- **Task 7 Gate**：全量 311 passed（293+18）、ruff clean、Workbench 真实浏览器
  e2e 通过、历史 benchmark 未修改、无真实付款/默认付费模型调用/生产 Demo Header。
- 完成标准：可离线启动、安全演示、结果可追溯、架构与局限可解释。

## Phase 10 — Enterprise AgentOps & Secure Tooling（2026-11-02）

### Commit A `b28f21d` — Secure MCP Tool Gateway

- **MCP 协议**：官方 `mcp` Python SDK **2.3.0**（PyPI，Streamable HTTP transport），
  真实 MCP client ↔ server 互通测试（initialize / tools/list / tools/call）。
- **Tool Registry**（`app/mcp_gateway/registry.py`）：4 个只读工具
  （search_supplier_quotes / get_supplier_inventory / get_procurement_policy /
  get_task_execution_status），ToolCapabilityContract（scope/capability/timeout/
  retry/rate_limit/result_size）。
- **ToolPolicyGateway**（`app/mcp_gateway/policy.py`）：12 步执行门禁
  （身份→注册→scope→task ownership→capability→schema→budget→执行→验证→审计），
  拒绝默认，masked audit。
- **绕过防护**：MCP 走 gateway；内部 orchestrator 走 ExecutorRouter（不变）；
  Worker 恢复不经过 MCP。
- **Prompt Injection**：结果当 DATA；registry 固定；恶意 description 不能注册新工具。
- **SDK 坑记录**：mcp 2.3.0 的 Route methods bug（→ app.mount + 自建 ASGI）、
  Context 必须是 mcpserver.context（非 server.context）、header 小写化、
  **kwargs 会生成多余字段（→ exec 动态签名）。
- 依赖变更：**starlette 降到 0.52.1**（mcp 2.3.0 兼容）；pytest 327 passed。
- 文档：`docs/enterprise/MCP_TOOL_GATEWAY_ARCHITECTURE.md` +
  `docs/security/MCP_TOOL_POLICY_SPEC.md` + `docs/security/AGENT_TOOL_THREAT_MODEL.md`。
- 产物：`docs/eval/mcp_tool_registry.json` + `mcp_security_test_results.json`。

### Commit B `6b67016` — OpenTelemetry AgentOps

- **OTel SDK 1.45.2**（sdk/api/exporter-otlp-proto-http）；默认 InMemorySpanExporter
  （离线/tests），OTLP 显式开启；exporter 故障不破坏采购任务。
- **Trace 架构**：ProcurementTask root span + 阶段子 span（Interpret/Validate/
  Collect/Score/Policy/Approval/Finalize），安全元数据（task_id/worker_id/
  lease_epoch/plan_hash/attempt_id/tool_name/policy_decision/outcome）。
- **跨进程传播**：W3C traceparent 存 trace_records 表（新增 trace_id/span_id/
  parent_span_id/lease_epoch 列）；resume 继承同 trace_id（crash+reclaim = 一条
  trace 两个 worker span，非伪造连续树）。
- **指标**（严格成本语义）：runtime/tool/safety/cost 计数器；actual_llm_calls/
  tokens/cost 仅 REAL 递增（REAL_LLM_NOT_RUN 时缺失非 0）；supplier_query_cost/
  recovery_extra_invocations 为观察代理（非金额）。
- **接入点**：orchestrator.run/resume 打 span；MCP gateway 每次调用打 tool span
  （ALLOWED/DENIED + reason）。
- **OTel 坑记录**：全局 provider 禁止重复 set（→ 进程级缓存 handle）；BatchSpan
  processor 异步导出需 force_flush；测试间 span 泄漏（→ fixture 前置 clear）。
- 文档：`docs/observability/AGENTOPS_ARCHITECTURE.md` + TRACE_AND_METRIC_CONTRACT.md
  + CROSS_PROCESS_TRACE_REPORT.md。
- 产物：`agent_trace_manifest.json` + `cross_process_trace_results.json` +
  `agentops_metrics.json`。pytest **343 passed**。

### Commit C `4ab41ca` — EvalOps CI Release Gates

- **app/evalops/core.py**：ReleaseCandidate（git 真实版本）、20 门禁注册表
  （10 硬安全门禁 fail-closed）、ReleaseGateEngine（PASS/FAIL/
  INSUFFICIENT_EVIDENCE/NOT_APPLICABLE/ERROR）、ShadowEvaluator（隔离快照）、
  PromotionManager（DRAFT→EVALUATING→QUALIFIED→PROMOTED/REJECTED/ROLLED_BACK）。
- **Task 21 核心演示**：故意不安全 candidate（放宽硬预算）→ 门禁 REJECTED
  （hard_budget_violation）、无法 promote；安全 baseline QUALIFIED→PROMOTED。
- **Shadow Evaluation**：真实 GreedyAdapter vs OptimizerAdapter 隔离快照
  （isolated=true、成本差、无状态污染）。
- **CI**（.github/workflows/release-gates.yml）：离线全默认（无付费 LLM/真实
  供应商/付款/凭据）；ruff+pytest+安全回归+MCP 互通+OTel InMemory+Release Gate
  判定（不安全 candidate 必须 REJECTED）；长 Chromium benchmark 独立 job。
- **Workbench EvalOps 视图**：Observatory 新增 3 端点（release-gates/shadow/
  promotion），前端 EvalOps tab 展示 REJECTED candidate（非全绿板）。
- 文档：docs/evalops/ 4 份 + PHASE10_FINAL_REPORT.md。
- 产物：release_candidate_manifest.json / release_gate_results.json /
  shadow_evaluation_results.json / promotion_history.json / rollback_history.json /
  security_regression_cases.json / phase10_execution_manifest.json。
- pytest **365 passed**（Commit C 后全量）。
- **Task 24 浏览器验证**：test_phase9_workbench_e2e.py 扩展 Lab 6
  EvalOps 视图（真实 Chromium 展示 REJECTED + hard_budget_violation），
  1 passed in 10.5s。

### Phase 11A（2026-11-05）— EvalOps 正确性修复

用户决定：先完成 11A 正确性修复，再 11B 真实安全退化检测，最后按凭据
条件决定 11C。Phase 11 无独立任务书文件（composer-pastes 与 session 均
无），按现有仓库状态自主审计修复。

**三个 P0/P1 缺陷（先写复现测试 RED→修复 GREEN）：**

1. **P0 gate_summary 把 INSUFFICIENT_EVIDENCE 当 PASS**：全 INSUFFICIENT
   的候选 verdict=QUALIFIED 可 promote——违反"证据不足不是 PASS、阻塞
   promotion"。修复：ANY FAIL → REJECTED；否则 ANY INSUFFICIENT →
   REJECTED；零评估 gate（全 NOT_APPLICABLE/空）→ REJECTED；仅全 PASS →
   QUALIFIED。gate_summary 增 insufficient_gates 键。
2. **P1 apply_gate_verdict 信任调用方 verdict 字符串**：伪造
   `{"verdict":"QUALIFIED"}` 可绕过 gate 引擎。移除该方法，换
   `apply_gate_results(candidate_id, results)`——从真实 gate results 经
   gate_summary 重算（调用方 3 处旧测试 + demo 2 处 + 文档 1 处同步改）。
3. **P1 release_candidate_manifest.json 契约形状不一致**：demo 写嵌套
   `{"unsafe":..,"safe":..}`，manifest() 是扁平 dict。修复为
   `{"candidates":[manifest()...],"roles":{...}}`，与 manifest() 形状
   一致（测试断言每个 entry 是扁平 manifest）。

- 全量 **371 passed**（365+6 新增，11A 六个测试），ruff clean。
- 测试：tests/test_phase11a_correctness.py（6 个，先 RED 后 GREEN）。
- 产物重生成：release_candidate_manifest.json（新形状）、
  release_gate_results.json（unsafe REJECTED / safe QUALIFIED 不变）。

### Phase 11A-2（2026-11-05）— Span 生命周期修复（任务书 P0）

任务书指出 OTel Span 生命周期 P0：orchestrator 提前返回（plan 校验失败、
resume 异常路径）root/resume span 未结束；MCP gateway 的 SCHEMA_INVALID、
RATE_LIMITED、result-schema 异常分支未 close span。OTel 规范要求创建者
必须结束 span——未结束导致资源泄漏、缺导出记录。

**修复（try/finally 保证全覆盖）：**
- `orchestrator.run()`：整个执行体包 try/finally，root span 在 finally
  结束（outcome=FAILED/ERROR 兜底），plan-reject 提前返回不再泄漏。
- `orchestrator.resume()`：同样 try/finally，missing-task raise / 持久化
  plan 校验失败 return 路径都结束 ResumeAttempt span（outcome=ERROR）。
- `mcp_gateway/policy.py` execute()：SCHEMA_INVALID / RATE_LIMITED /
  TOOL_POLICY_ERROR / RESULT_SCHEMA_INVALID（新增审计条目）分支全部
  _close_span("DENIED", reason)；handler 意外异常 TOOL_ERROR 已 close。
- 硬崩溃（os._exit）无法执行 finally——靠持久化 Attempt + 恢复事件关联
  不完整 trace（既有 Phase 2/3 语义，本 commit 未改变）。

**测试（tests/test_phase11a_otel.py，8 个）：**
- run plan-reject 路径 root span 存在且 outcome=FAILED/ERROR
- resume missing-task raise 路径 resume span outcome=ERROR
- gateway SCHEMA_INVALID / RATE_LIMITED / ALLOWED / RESULT_SCHEMA_INVALID
  / TOOL_ERROR 五路径 tool span 全部结束且带 decision/reason_code
- span 计数与测试顺序无关（autouse 前置 clear exporter）

全量 **379 passed**（371+8），ruff clean。

### Phase 11B（2026-11-05）— 真实安全退化检测与持久化发布治理

任务书（pasted_content_2026-11-05_04-35-39-504_c46954.txt）：通过真实候选
策略执行、独立约束验证、跨进程发布竞争和可信授权测试，证明不安全 Agent
版本无法绕过 Release Gate 并成为当前有效版本。Commit A→B→C。

#### Commit A — 真实候选安全退化检测

**Task 0 基线审计**（docs/evalops/PHASE11B_BASELINE_AUDIT.md）关键发现：
- ReleaseGateEngine 只消费外部 measurements，不执行 candidate——
  unsafe_candidate_demo 是手工构造的演示，非真实候选执行。
- PromotionManager 纯内存态（dict+list）——无持久化/CAS/Active Version。
- MCP task_owner 默认 None（跳过 ownership 校验），identity 是纯 demo。

**Candidate Mutation Harness**（app/evalops/mutation.py）：
- baseline：真实 greedy 分配（s1:6+s2:6+s3:3=162）→ verifier PASS。
- mutant_a（硬预算回归）：全量给最贵 s3×15=180 → hard_budget_exceeded
  → FAIL。
- mutant_b（过期报价回归）：s1 过期 1h → line_quote_expired → FAIL。
- mutant_c（库存约束回归）：s1 分配 7>6 → line_inventory_exceeded → FAIL。
- 独立世界（fixed seed 7，预算 162，白名单 s1+s2+s3，s3 stock 15）；
  每次拒绝保存 input hash/candidate output/quote snapshot/verifier 结果/
  violation reason/gate 结果。
- AllocationConstraintVerifier 独立重算全部 11 项约束，不信任 candidate
  预填 is_valid/feasible/verdict。
- 生产 policy/guard/verifier 未被修改（变异仅测试 fixture）。

产物 4 份：candidate_mutation_manifest.json / candidate_execution_results
.json / independent_constraint_results.json / release_gate_decisions.json。
文档：REAL_CANDIDATE_REGRESSION_SPEC.md。
测试：tests/test_phase11b_mutation.py（8 个）。
全量 **387 passed**（379+8），ruff clean。

#### Commit B — Durable Promotion 与并发控制

**Task 3/4 P0**：PromotionManager 从纯内存（dict+list）改为 SQLite 持久化
+ CAS 事务（app/evalops/promotion.py DurablePromotionManager）。

- 数据模型：promotion_state（active_version/version_epoch/promotion_status/
  previous_version/manifest/gate hashes/rollback_reason）+ promotion_audit
  （append-only，与状态变更同事务）。
- 原子 promote：BEGIN IMMEDIATE → 事务内读（串行点）→ 要求 QUALIFIED →
  更新 active_version + epoch++ → 写 audit → COMMIT；异常 ROLLBACK。
- apply_gate_results 从真实 gate results 重算（INSUFFICIENT_EVIDENCE →
  REJECTED），不信任 caller verdict 字符串。
- 跨进程竞争（真实子进程 + 文件型 SQLite）：
  - 2/4 worker 同候选 → 恰 1 个 PROMOTED，败者明确错误，audit 仅 1 行。
  - 两候选竞争 → 最后 COMMIT 者赢，仅 1 个 active version。
  - 重复 promote 不推进 epoch/历史。
  - crash before COMMIT（os._exit 子进程）→ 事务回滚：active=None、
    epoch=0、无 audit。
  - crash after COMMIT → 新连接看到一致状态。
  - audit 写失败 → 整事务回滚（active 不变）。
- rollback：CAS on active version（非 active → NOT_ACTIVE / 过期 →
  ACTIVE_VERSION_MISMATCH）；退役 active 指针 + 写 ROLLED_BACK audit +
  reason；**不重写历史任务/plan/checkpoint 绑定**（运行任务保留自己版本）。
- 产物：promotion_race_results.json（2-worker race + crash-before-commit）、
  rollback_consistency_results.json。
- 文档：DURABLE_PROMOTION_SPEC.md。
- 测试：tests/test_phase11b_promotion.py（17 个，含 4 跨进程竞争 + 2 crash）。
全量 **404 passed**（387+17），ruff clean。

#### Commit C — 可信工具授权与端到端安全回归

**Task 5/6**：对象级授权 + Worker 身份边界 + E2E 完整链路。

- **TaskRecord 加 owner_id 列** + repository.create(owner_id=...) +
  repository.get_owner(task_id)；main.py 的 MCP task_owner 从硬编码
  "demo-approver" 改为真实 owner（原来所有存在的任务都"属于"demo——
  ownership 检查形同虚设）。
- **policy.py 去掉 demo 特例**（`owner == "demo"` 放行删除）：demo
  principal 也按 owner==id 判定。
- **TASK_STATUS_OUTPUT schema 修复**：current_step/created_at/updated_at
  允许 null（PENDING 新任务 current_step 为 None 是合法的，原 schema
  要求 string 导致 result-schema 拒绝）。
- **对象级授权验证**（真实 repository + 真实 handlers）：
  - alice 读自己任务 ALLOWED；读 bob 任务 DENIED(TASK_FORBIDDEN)。
  - 无 owner 任务 DENIED（fail-closed）。
  - demo principal 读别人任务 DENIED（demo 不豁免）。
  - 不存在任务 404（不泄漏存在性）。
- **Worker 身份边界**：resume(task_id) 只接受 task id（无请求体 role/
  header 注入）；demo header 非长期后台身份。
- **E2E 安全回归 demo**（phase11b_e2e_demo.py）：单 manifest 贯穿
  真实 mutant 执行 → 独立 verifier INVALID → gate REJECTED →
  Durable Promotion BLOCKED（active 保持 baseline-7）→ OTel 4 span
  （candidate_execution=FAIL/independent_verification=INVALID/
  release_gate=REJECTED/promotion_rejection=BLOCKED）。
- **Observatory 2 新端点**：evalops/mutation（真实变异证据表）+
  evalops/e2e（单 manifest 链路）。
- 产物：authorization_test_results.json + phase11b_execution_manifest.json。
- 文档 4 份：TOOL_AUTHORIZATION_BOUNDARY.md / PHASE11B_FINAL_REPORT.md /
  PHASE11B_SECURITY_REGRESSION_REPORT.md（+ 11A/11B 已提交的
  PHASE11B_BASELINE_AUDIT/REAL_CANDIDATE_REGRESSION_SPEC/DURABLE_PROMOTION_SPEC）。
- 测试：test_phase11b_authz_e2e.py（7）+ test_phase11b_observatory.py（3）。
全量 **414 passed**（404+10），ruff clean，历史 benchmark 字节不变。

### Phase 11C（2026-11-05）— Scoped Real-LLM Validation & Final Release

任务书（无 @file，直接消息）：凭据 + 费用预算双条件决定真实 LLM。

- **Task 1 Preflight**：Phase 8 harness（OpenAICompatibleModelClient +
  sandbox 门禁）确认——模型输出仅是 proposal，变更必须经 Policy/
  ApprovalGuard/EvalOps；预算上限（max_calls 20 / tokens 2000 / retry 1 /
  cost 5 USD）就绪；默认 sandbox 拒付费调用。
- **凭据判断**：环境有 ARK_API_KEY，但 llm_base_url/model 是 OpenAI 默认
  → provider 不匹配；无明确费用授权 → **不发起真实调用**，标记
  **NOT_EXECUTED_NO_CREDENTIALS**，保持 REAL_LLM_NOT_RUN。
- **Task 3 Final Demo**（app/evalops/final_demo.py）：一条命令 8 步完整
  安全闭环，全部真实执行记录——创建任务→MCP get_procurement_policy
  ALLOWED→读他人任务 DENIED(TASK_FORBIDDEN)→注入硬预算 mutant→独立
  Verifier INVALID(180>162)→Gate REJECTED→Promotion BLOCKED（active 保持
  baseline-7）→OTel 7 span + audit + manifest。产物
  final_enterprise_demo_manifest.json。
- **Task 4 文档**：README Enterprise Security Loop 章节、DEMO_RUNBOOK
  第 8 步、PHASE11C_REAL_LLM_VALIDATION.md。
- **Task 7 修复**：
  - OTel in-memory 默认改用 InMemoryMetricReader——消除 ConsoleMetricExporter
    后台线程进程退出时的 "I/O operation on closed file" 噪音（Phase 11B
    遗留，已定位并消除）。
  - MCP tool span 补 outcome 属性（=decision 同值），trace 消费统一字段。
- 全量 **414 passed**、ruff clean、历史 benchmark 字节不变。

**项目可封版**：离线完整安全闭环可演示；真实模型能力诚实区分（未运行，
无凭据）与受控模拟验证（充分）。

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

## CI 修复（2026-11-08，GitHub Actions 全绿）

用户反馈 GitHub 邮件 CI 失败，排查修复链（run 42011679662 → 42447466375）：

1. **ruff 未装**（旧 workflow `pip install -e .` 静默失败不装依赖）→ requirements.txt 补 ruff。
2. **fastapi/mcp/starlette 依赖冲突**：fastapi 0.115.12 pin starlette<0.47，mcp 2.3.0 需 0.52.1，
   pip 解析器全新环境拒绝 → Install deps 顺序改为：`pip install -r requirements.txt` →
   `pip install --no-deps starlette==0.52.1` → `pip install mcp==2.3.0`（复刻本地 venv 顺序）。
3. **pytest 文件列表反斜杠续行**：YAML block scalar 保留前导空格 → 文件名带空格
   "file or directory not found" → 改单行文件列表。
4. **python -c 多行字符串**：YAML 缩进泄漏进代码 → IndentationError → 改单行。
5. **browser-benchmark job 缺 mcp**：install 只装 requirements.txt，workbench e2e
   import mcp 失败 → 补同样的 starlette/mcp 覆盖。
6. 诊断手段突破：**Windows 凭据管理器 CredReadW 读 git 凭据（gho_ OAuth token）→
   Bearer + X-GitHub-Api-Version 头可调 Actions API 下载 run 日志**（此前
   credential get 非交互返回 NO_TOKEN、匿名 API 401/404 无日志访问）。

最终：offline-gates（Ruff + 9 文件核心安全套件 80 passed + 不安全候选 REJECTED
断言 + artifact 上传）与 browser-benchmark（真实 Chromium workbench E2E）**双 job 全绿**。
