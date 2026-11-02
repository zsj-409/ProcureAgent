# ProcureAgent 3.0 — Demo Runbook

完全离线、可复现的演示流程。**不调用真实付费 API、不购买任何商品。**

## 启动

```bash
# 1. 依赖（Python 3.11）
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # Windows
# Playwright 浏览器（仅 portal 演示需要）
.venv/Scripts/playwright install chromium

# 2. 离线配置（Demo 模式）
#   auth_enabled=False + demo_identity_header 显式设置 → 隔离 Demo 身份
#   不设 PROCUREAGENT_LLM_API_KEY → 无任何真实模型调用
export PROCUREAGENT_AUTH_ENABLED=false
export PROCUREAGENT_DEMO_IDENTITY_HEADER=X-Demo-Actor
export PROCUREAGENT_WEB_ALLOW_LAUNCH=false   # 不自动打开浏览器

# 3. 启动
.venv/Scripts/python -m app.main   # 或 uvicorn app.main:app
# → http://127.0.0.1:8000
```

Demo 身份：请求头 `X-Demo-Actor: demo-approver`（仅此值被接受，其余 401）。
生产配置（auth_enabled=True）下该 header 被忽略——它不是登录凭据。

## 固定演示流程（7 步）

| # | 演示 | 操作 | 证据 |
|---|---|---|---|
| 1 | 正常采购任务 + 需求解析 | Workbench → 新建采购 → 输入中文需求 | 任务创建 → 推荐 → WAITING_APPROVAL |
| 2 | 持久步骤与状态 | 任务详情 / Observatory → Flight Recorder | 每步 PENDING→CLAIMED→SUCCEEDED |
| 3 | Worker Crash / Lease / Fencing / UNKNOWN_OUTCOME | 任务详情看崩溃恢复轨迹 | `test_full_task_recovery.py`：跨进程恢复、外部效果恰 2 次 |
| 4 | Portal State-Aware Recovery | Observatory → Chaos Recovery Lab | Phase 4 真实浏览器消融表（7 故障） |
| 5 | 未授权审批拒绝 / 审批版本 / 硬预算 | 无 header 点审批 → 401；有 header 点审批 | AUTH_REQUIRED / CAS + 版本绑定 |
| 6 | Greedy vs Constrained Optimizer | Observatory → Procurement Optimization | CBC 对比表 + 诚实边界 |
| 7 | 正结果、负结果与统计限制 | Observatory → Agent Experiment Lab | Phase 7 120 案例表 + 边界说明 |

## 关键诚实标注（演示时主动说明）

- **Phase 4**：State-Aware 与 Current Ladder 在 n=7 成功率持平，P50 低约 35%——
  收益在延迟与安全验证，不是成功率。
- **Phase 6**：优化器硬约束处理有优势（18/28 FULLY、0 violation），但 Cluster
  Bootstrap CI [0.0, 6.4] 含零——**不能宣称总体成本节省显著**。
- **Phase 7**：三策略成功率相同（30/40 + safe_stop 10/40）；Cost-Aware 减少的是
  额外**观察次数**（obs 40→20/0），观察次数不是真实金额成本。
- **F Tuned 与 E Cost-Aware 结果相同**——不宣传 F 相比 E 有额外提升。
- **REAL_LLM_NOT_RUN**：所有 LLM 相关实验均为 SCRIPTED/MOCK，真实 LLM 未运行。

## 测试命令

```bash
.venv/Scripts/python -m pytest               # 全量（>300）
.venv/Scripts/python -m pytest tests/test_phase9_workbench_e2e.py   # 真实浏览器 Workbench
.venv/Scripts/python -m ruff check app/ tests/
```

## 截图位置（如有）

`docs/demo/screenshots/`（未提交截图，演示时现场截图即可）。

## 演示账户限制

- Demo 身份仅 `demo-approver`（role=approver, tenant=demo, is_demo=True）。
- 无多租户：SINGLE_TENANT_DEMO_ONLY（API 响应中显式标注）。
- 无真实登录：auth_enabled=False 时 header 是唯一身份来源；auth_enabled=True
  时所有审批写操作默认拒绝。
