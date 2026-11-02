# ProcureAgent 3.0 — Engineering Tradeoffs

## 1. SQLite 而非 PostgreSQL/分布式队列

- **选 SQLite WAL + busy_timeout 30s**：单机 demo 场景下足够；原子 Claim 用
  `UPDATE ... WHERE status='PENDING'` 的 rowcount==1 判定，天然防并发。
- 代价：横向扩展受限、写并发有锁。**如果未来多 worker 高并发**，迁移 Postgres
  只涉及 repository 层（接口已抽象）。
- 面试要点：这不是偷懒——是"用现有数据库实现最小正确闭环，再按需迁移"的工程判断。

## 2. 确定性优先，LLM 可选

- Planner/Interpreter/Portal Agent 都有**规则 fallback**；LLM 只在配置 key 时启用。
- 理由：采购审批场景中**可解释性 > 创造力**。规则路径可测试、可证明；LLM 路径
  被 Schema 校验 + 修复重试 + 确定性 fallback 包裹。
- 代价：复杂自然语言理解弱于 LLM（Phase 8 规则 baseline 7/10）。这正是 Phase 8
  量化对比的意义。

## 3. 两阶段词典序 + Decimal 复核（优化器）

- BIG_M 单阶段无法严格保证覆盖优先 → **先最大化覆盖、再固定覆盖最小化成本**。
- 求解器浮点解必须 Decimal 复核（预算容差 0.005），违反→INVALID。
- 代价：两次求解略慢（80 供应商 0.094s，可忽略）；收益：覆盖数量永不因成本牺牲。

## 4. 独立 Oracle 而非信任求解器

- CBC 报 OPTIMAL 不等于业务正确 → SmallWorldOracle 穷举（不调 PuLP）做 ground
  truth，Oracle 与 Optimizer 一致性即正确性证明。
- 代价：Oracle 只支持小世界（≤4 供应商 ≤12 数量）；收益：核心正确性有独立证据。

## 5. 语义状态 + 动作白名单（而非截图/CSS）

- 页面观测单位是**语义状态**（8 类）+ 稳定签名，不是裸截图——截图无法断言，
  状态可以。
- 动作被**白名单 + 前置/后置条件**约束，模型永远不能执行任意 JS。
- 代价：语义提取需要维护 hints（表头、控件指纹）；收益：OOD 模板（t2_ood）下
  依然能正确分类。

## 6. 成功率饱和时的诚实成本测量

- Phase 7 三策略成功率相同（100% 正确目标）——**成功率无差异是事实**。
- 可测量的差异在**观察次数**（obs 40→20/0），但观察次数≠金额成本。
- 结论：不宣称"更聪明策略提升成功率"；宣称"成本感知策略在无质量损失下减少
  运行开销"。这是诚实的边界。

## 7. 真实 LLM 未运行（REAL_LLM_NOT_RUN）

- 所有 LLM 相关实验用 SCRIPTED/MOCK 客户端，**严格标注**。
- 理由：没有凭据时，伪造调用结果是最严重的学术/工程错误。
- Phase 8 提供**默认离线的真实 LLM harness**：有 key 且显式关沙箱即可跑，预算
  门禁（max_calls/cost_budget）保护。

## 8. 单租户 Demo（SINGLE_TENANT_DEMO_ONLY）

- 不宣称多租户隔离已实现；API 响应显式标注 authz_model。
- 面试要点：明确"什么没做"和"为什么没做"（Demo 不需要），比假装做了更有说服力。

## 9. 测试与评测的分离

- 单元/集成测试（pytest）保证代码正确性；
- tests/eval/ 的独立 World + Evaluator 保证**评测正确性**（ground truth 隔离、
  快照 hash、种子注册、DEV/CONFIRMATION/TEST 划分）。
- 代价：评测基建较大（portal_world 394 行等）；收益：任何算法改动都能被独立
  验证，不依赖自证。
