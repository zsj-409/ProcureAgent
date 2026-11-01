// Shared result renderer: recommendation, quotes, split award, approval gate, replay.

import { getJSON, postJSON } from "../api.js";
import { esc, fmtMoney, frameUrl, toast } from "../format.js";

export async function renderResult(zone, taskId, { compact = false } = {}) {
  let recommendation = null;
  let trace = [];
  let replay = { suppliers: {} };
  try {
    recommendation = await getJSON(`/api/v1/tasks/${taskId}/result`);
  } catch {
    /* recommendation may not exist for failed tasks */
  }
  try {
    trace = await getJSON(`/api/v1/tasks/${taskId}/trace`);
  } catch { /* optional */ }
  try {
    replay = await getJSON(`/api/v1/tasks/${taskId}/portal-steps`);
  } catch { /* optional */ }

  const parts = [];
  if (recommendation) {
    parts.push(recommendationCard(recommendation));
    parts.push(quotesTable(recommendation));
    if (recommendation.award_split && recommendation.award_split.length) {
      parts.push(splitCard(recommendation));
    }
    parts.push(approvalCard(taskId, recommendation));
  }
  parts.push(portalReplayCard(replay));
  parts.push(traceCard(trace));
  zone.innerHTML = parts.join("") || `<div class="empty">尚无结果数据</div>`;

  wireApprovals(zone, taskId);
}

function recommendationCard(recommendation) {
  const best = (recommendation.quotes || []).find(
    (quote) => quote.supplier_id === recommendation.recommended_supplier
  );
  return `
    <div class="card" id="recommendation">
      <h3>采购推荐</h3>
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
        <strong style="font-size:16px">${esc(recommendation.recommended_supplier)}</strong>
        ${recommendation.approval_required
          ? '<span class="pill pill-warn">需人工审批</span>'
          : '<span class="pill pill-ok">自动批准</span>'}
        ${recommendation.award_split && recommendation.award_split.length
          ? `<span class="pill pill-accent">拆分授标 ×${recommendation.award_split.length}</span>` : ""}
        ${recommendation.shortfall > 0
          ? `<span class="pill pill-warn">缺口 ${esc(recommendation.shortfall)} 件</span>` : ""}
      </div>
      <div class="grid cols-4" style="margin-top:10px">
        <div class="stat accent"><div class="label">综合得分</div><div class="value" style="font-size:18px">${esc(String(recommendation.score))}</div></div>
        <div class="stat ok"><div class="label">预估总额</div><div class="value" style="font-size:18px">${esc(fmtMoney(recommendation.estimated_total))}</div></div>
        <div class="stat"><div class="label">推荐单价</div><div class="value" style="font-size:18px">${best ? esc(fmtMoney(best.unit_price)) : "—"}</div></div>
        <div class="stat"><div class="label">交期</div><div class="value" style="font-size:18px">${best ? esc(best.delivery_days) + " 天" : "—"}</div></div>
      </div>
      <p style="font-size:13px;color:var(--text-2);border-left:3px solid var(--accent);padding-left:10px;margin:12px 0 0">${esc(recommendation.reason)}</p>
      ${recommendation.summary ? `<p class="card-hint" style="margin:10px 0 0"><strong>说明：</strong>${esc(recommendation.summary)}</p>` : ""}
    </div>`;
}

function quotesTable(recommendation) {
  const quotes = recommendation.quotes || [];
  if (!quotes.length) return "";
  return `
    <div class="card">
      <h3>报价对比（${quotes.length}）</h3>
      <div class="table-wrap"><table class="data">
        <thead><tr>
          <th>供应商</th><th>商品</th><th class="num">单价</th><th class="num">库存</th>
          <th class="num">交期</th><th>通道</th><th class="num">评分贡献</th>
        </tr></thead>
        <tbody>${quotes.map((quote) => {
          const isBest = quote.supplier_id === recommendation.recommended_supplier
            && !recommendation.award_split.length;
          return `
          <tr class="${isBest ? "quote-best" : ""}">
            <td><strong>${esc(quote.supplier_id)}</strong>${isBest ? ' <span class="pill pill-ok">最优</span>' : ""}</td>
            <td>${esc(quote.product_name)}</td>
            <td class="num">${esc(fmtMoney(quote.unit_price))}</td>
            <td class="num">${quote.available_stock}</td>
            <td class="num">${quote.delivery_days} 天</td>
            <td>${quote.source_type === "portal"
              ? '<span class="pill pill-accent">门户</span>'
              : '<span class="pill pill-info">API</span>'}</td>
            <td class="num">${scoreBars(recommendation, quote)}</td>
          </tr>`;
        }).join("")}
        </tbody></table></div>
      <p class="card-hint" style="margin:8px 0 0">评分 = 价格 50% + 交期 30% + 库存 20%（按偏好调整权重，相对最优值归一）</p>
    </div>`;
}

function scoreBars(recommendation, _quote) {
  return `<span class="dim">${esc(String(recommendation.score))}</span>`;
}

function splitCard(recommendation) {
  return `
    <div class="card">
      <h3>拆分授标方案</h3>
      <p class="card-hint">单一供应商库存不足以覆盖全部数量时，按综合分贪心拆分（最多 3 家）</p>
      <div class="table-wrap"><table class="data">
        <thead><tr><th>#</th><th>供应商</th><th class="num">数量</th><th class="num">单价</th><th class="num">行小计</th><th class="num">交期</th></tr></thead>
        <tbody>${(recommendation.award_split || []).map((line, index) => `
          <tr>
            <td>${index + 1}</td>
            <td><strong>${esc(line.supplier_id)}</strong></td>
            <td class="num">${line.quantity}</td>
            <td class="num">${esc(fmtMoney(line.unit_price))}</td>
            <td class="num">${esc(fmtMoney(line.line_total))}</td>
            <td class="num">${line.delivery_days} 天</td>
          </tr>`).join("")}
        </tbody></table></div>
      ${recommendation.shortfall > 0
        ? `<div class="note warn">总库存仍不足：还有 ${esc(recommendation.shortfall)} 件无法授标，请调整数量或补充供应商。</div>`
        : '<div class="note">拆分后已覆盖全部采购数量。</div>'}
    </div>`;
}

function approvalCard(taskId, recommendation) {
  if (!recommendation.approval_required) return "";
  return `
    <div class="card" id="approval">
      <h3>审批门</h3>
      <p class="card-hint">预估总额达到或超过审批阈值，需要人工决定</p>
      <div style="display:flex;gap:10px">
        <button class="btn" id="approve-btn">✓ 批准下单</button>
        <button class="btn danger" id="reject-btn">✗ 驳回</button>
      </div>
      <div id="approval-state"></div>
    </div>`;
}

function wireApprovals(zone, taskId) {
  const approve = zone.querySelector("#approve-btn");
  const reject = zone.querySelector("#reject-btn");
  if (approve) {
    approve.addEventListener("click", async () => {
      approve.disabled = true;
      try {
        await postJSON(`/api/v1/tasks/${taskId}/approve`);
        toast("已批准，任务完成");
        zone.querySelector("#approval-state").innerHTML =
          '<div class="note" style="margin-top:10px">✓ 已批准 —— 任务 COMPLETED</div>';
      } catch (error) {
        toast(`批准失败：${error.message}`, true);
        approve.disabled = false;
      }
    });
  }
  if (reject) {
    reject.addEventListener("click", async () => {
      reject.disabled = true;
      try {
        await postJSON(`/api/v1/tasks/${taskId}/reject`);
        toast("已驳回");
        zone.querySelector("#approval-state").innerHTML =
          '<div class="note warn" style="margin-top:10px">✗ 已驳回 —— 任务 REJECTED</div>';
      } catch (error) {
        toast(`驳回失败：${error.message}`, true);
        reject.disabled = false;
      }
    });
  }
}

function portalReplayCard(replay) {
  const suppliers = replay.suppliers || {};
  const entries = Object.entries(suppliers);
  if (!entries.length) return "";
  return `
    <div class="card">
      <h3>门户自动化回放</h3>
      <p class="card-hint">自适应门户代理的每一步动作与页面快照 —— 无人值守自愈的可见证据</p>
      ${entries.map(([supplierId, manifest]) => `
        <h3 style="margin:12px 0 6px;font-size:13px">${esc(supplierId)}
          <span class="pill pill-accent">${(manifest.steps || []).length} 步</span></h3>
        <div class="replay-strip">
          ${(manifest.steps || []).map((step) => {
            const url = frameUrl(step.frame_path);
            return `
            <div class="replay-item">
              ${url ? `<img src="${esc(url)}" alt="step ${step.sequence}" loading="lazy">`
                   : '<div class="empty" style="padding:24px">无截图</div>'}
              <div class="replay-cap">
                <span class="seq">${step.sequence}</span>
                <span><strong>${esc(step.action)}</strong> ${esc(step.detail || "")}</span>
              </div>
            </div>`;
          }).join("")}
        </div>`).join("")}
    </div>`;
}

function traceCard(trace) {
  if (!trace.length) return "";
  return `
    <div class="card">
      <h3>执行留痕（${trace.length} 条）</h3>
      <div class="table-wrap"><table class="data">
        <thead><tr><th>#</th><th>步骤</th><th>组件</th><th>动作</th><th>状态</th><th class="num">耗时</th><th>错误 / 说明</th></tr></thead>
        <tbody>${trace.map((row, index) => `
          <tr>
            <td class="mono dim">${index + 1}</td>
            <td class="mono">${esc(row.step_name)}</td>
            <td class="dim">${esc(row.component)}</td>
            <td>${esc(row.action)}</td>
            <td>${row.status === "SUCCESS"
              ? '<span class="pill pill-ok">SUCCESS</span>'
              : `<span class="pill pill-bad">ERROR</span>`}</td>
            <td class="num">${row.duration_ms} ms</td>
            <td class="dim" style="max-width:340px">${esc(row.error || "")}</td>
          </tr>`).join("")}
        </tbody></table></div>
    </div>`;
}
