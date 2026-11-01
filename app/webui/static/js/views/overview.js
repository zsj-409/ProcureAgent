// Overview page.

import { getJSON } from "../api.js";
import { esc, fmtMoney } from "../format.js";

export async function render(container) {
  container.innerHTML = `<div class="loading">加载总览数据…</div>`;
  let data;
  try {
    data = await getJSON("/api/v1/overview");
  } catch (error) {
    container.innerHTML = `<div class="error-box">加载失败：${esc(error.message)} — 请确认 mock 服务与应用都已启动。</div>`;
    return;
  }

  const statusChips = Object.entries(data.by_status || {})
    .map(([status, count]) => `<span class="chip">${esc(status)} × ${count}</span>`)
    .join(" ");

  container.innerHTML = `
    <section class="hero">
      <h1>ProcureAgent · 可靠执行的企业采购智能代理</h1>
      <p>自然语言下单 → LLM 辅助规划（带校验回退）→ API 优先、门户自适应兜底的并行报价采集 →
      确定性评分与拆分授标 → 预算审批门 → 全程步骤级检查点与执行留痕。</p>
      <p>LLM 只处理不确定性；评分、预算、重试、检查点、策略与审批全部由确定性代码持有最终执行权。</p>
      <div class="actions">
        <a class="btn" href="#/run">▶ 发起一次采购</a>
        <a class="btn ghost" href="#/tasks">查看任务记录</a>
        <a class="btn ghost" href="#/suppliers">供应商通道</a>
      </div>
    </section>

    <div class="grid cols-5">
      ${stat("任务总数", data.tasks_total, "全部采购任务", "accent")}
      ${stat("供应商通道", data.suppliers_total, `${data.api_suppliers} API · ${data.portal_suppliers} 门户`, "ok")}
      ${stat("审批阈值", fmtMoney(data.approval_threshold), "超过需人工审批", "warn")}
      ${stat("LLM 增强", data.llm_configured ? "已配置" : "离线可用", data.llm_configured ? "结构化规划已启用" : "确定性回退运行", "info")}
      ${stat("自适应门户", data.portal_adaptive_enabled ? "开启" : "关闭", "脚本失败自动升级", "accent")}
    </div>

    <h2 class="section">任务状态分布</h2>
    <div class="card">
      ${statusChips || '<div class="empty">还没有任务 —— 到「新建采购」发起一次</div>'}
    </div>

    <h2 class="section">执行流水线</h2>
    <div class="card">
      <div class="step-flow" style="gap:8px">
        <span class="step-node done">解释器<br><small>自然语言 → 结构化请求</small></span><span class="step-arrow">→</span>
        <span class="step-node done">规划器<br><small>LLM + 校验回退</small></span><span class="step-arrow">→</span>
        <span class="step-node done">并行采集<br><small>API / 门户自适应</small></span><span class="step-arrow">→</span>
        <span class="step-node done">归一化·评分<br><small>确定性加权</small></span><span class="step-arrow">→</span>
        <span class="step-node done">拆分授标<br><small>库存不足时</small></span><span class="step-arrow">→</span>
        <span class="step-node done">审批门<br><small>预算策略</small></span><span class="step-arrow">→</span>
        <span class="step-node done">留痕<br><small>SQLite 检查点</small></span>
      </div>
    </div>

    <h2 class="section">设计原则</h2>
    <div class="grid cols-2">
      <div class="card">
        <h3>LLM 处理不确定性，代码负责确定性</h3>
        <p style="margin:0;font-size:12.5px;color:var(--text-2)">
          规划由 LLM 提出，但必须通过 PlanValidator 白名单校验，失败即回退规则规划；
          评分/预算/审批全部是可解释的确定性规则；LLM 只解释已经算出的结果。
        </p>
      </div>
      <div class="card">
        <h3>门户自动化的三级升级</h3>
        <p style="margin:0;font-size:12.5px;color:var(--text-2)">
          ① 配置化固定脚本（最快、全确定性）→ ② 启发式自适应代理（DOM 快照 + 有界动作循环，无需选择器）→
          ③ LLM 门户代理（快照进模型，结构化动作出）。页面文本不可信：表单值只允许商品名与枚举选项。
        </p>
      </div>
      <div class="card">
        <h3>失败即检查点</h3>
        <p style="margin:0;font-size:12.5px;color:var(--text-2)">
          每个供应商的每次尝试都落库（StepExecution），成功结果幂等复用；
          失败任务一键 Resume，已成功的供应商不会重复调用。
        </p>
      </div>
      <div class="card">
        <h3>内建评估</h3>
        <p style="margin:0;font-size:12.5px;color:var(--text-2)">
          pytest + Eval Runner 覆盖任务成功率、规划校验、故障注入恢复、执行器行为、
          延迟与 LLM 调用次数；故障注入是刻意设计且不隐藏。
        </p>
      </div>
    </div>

    <p class="footer-note">ProcureAgent Workbench · 默认仅本机访问 · 数据存储于 SQLite（procure_agent.db）</p>
  `;
}

function stat(label, value, foot, tone) {
  return `<div class="stat ${tone}"><div class="label">${esc(label)}</div>
    <div class="value">${esc(String(value))}</div><div class="foot">${esc(foot)}</div></div>`;
}
