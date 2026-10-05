// Suppliers page: channels with live health probes.

import { getJSON } from "../api.js";
import { esc } from "../format.js";

export async function render(container) {
  container.innerHTML = `<div class="loading">探测供应商通道…</div>`;
  let suppliers;
  try {
    suppliers = await getJSON("/api/v1/suppliers");
  } catch (error) {
    container.innerHTML = `<div class="error-box">加载失败：${esc(error.message)}</div>`;
    return;
  }

  const healthy = suppliers.filter((item) => item.healthy).length;
  container.innerHTML = `
    <div class="page-head">
      <h1>供应商通道</h1>
      <p class="sub">${suppliers.length} 个通道 · ${healthy} 个健康 · API 优先，门户走三级升级（脚本 → 启发式代理 → LLM 代理）</p>
    </div>
    <div class="grid cols-2">
      ${suppliers.map((supplier) => `
        <div class="card">
          <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
            <strong style="font-size:15px">${esc(supplier.display_name)}</strong>
            ${supplier.source_type === "api"
              ? '<span class="pill pill-info">API 通道</span>'
              : supplier.adaptive
                ? '<span class="pill pill-accent">门户 · 自适应代理</span>'
                : '<span class="pill pill-accent">门户 · 固定脚本 + 升级</span>'}
            ${supplier.healthy === true
              ? `<span class="pill pill-ok">健康 ${supplier.latency_ms ?? "?"}ms</span>`
              : '<span class="pill pill-bad">不可达</span>'}
          </div>
          <div class="kv" style="margin-top:10px">
            <dt>供应商 ID</dt><dd class="mono">${esc(supplier.supplier_id)}</dd>
            <dt>基础地址</dt><dd class="mono" style="overflow-wrap:anywhere">${esc(supplier.base_url)}</dd>
            <dt>入口</dt><dd class="mono">${esc(supplier.search_endpoint || supplier.search_path || "—")}</dd>
            <dt>优先级</dt><dd>${supplier.priority}</dd>
          </div>
          ${supplier.source_type === "portal" && supplier.adaptive
            ? '<p class="card-hint" style="margin:10px 0 0">该门户未配置选择器 —— 自适应代理直接接管：DOM 快照 → 有界动作循环 → 确定性提取。</p>'
            : ""}
        </div>`).join("")}
    </div>

    <h2 class="section">为什么 API 优先</h2>
    <div class="card">
      <p style="margin:0;font-size:13px;color:var(--text-2)">
        浏览器自动化不应是默认通道：API 更快、更稳、更易测。只有完全暴露不了接口的老门户才走
        Playwright；即便如此，每个门户动作也在预算与守卫之内（动作数上限、表单值白名单、步骤截图留证）。
      </p>
    </div>

    <p class="footer-note">健康探测：GET {base_url}/health · 2.5s 超时 · 每次打开本页实时探测</p>
  `;
}
