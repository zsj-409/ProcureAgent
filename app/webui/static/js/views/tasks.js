// Tasks list page.

import { getJSON } from "../api.js";
import { esc, fmtMoney, fmtTime, shortId, statusPill } from "../format.js";

export async function render(container) {
  container.innerHTML = `<div class="loading">加载任务记录…</div>`;
  let tasks;
  try {
    tasks = await getJSON("/api/v1/tasks?limit=100");
  } catch (error) {
    container.innerHTML = `<div class="error-box">加载失败：${esc(error.message)}</div>`;
    return;
  }

  container.innerHTML = `
    <div class="page-head">
      <h1>任务记录</h1>
      <p class="sub">全部采购任务 · 点击任意一行进入详情（检查点、留痕、门户回放）</p>
    </div>
    <div class="filters" style="display:flex;gap:10px;margin-bottom:12px">
      <select id="f-status" style="width:auto;min-width:160px">
        <option value="">全部状态</option>
        ${["PENDING", "PLANNING", "COLLECTING", "SCORING", "WAITING_APPROVAL", "COMPLETED", "APPROVED", "REJECTED", "FAILED"]
          .map((status) => `<option>${status}</option>`).join("")}
      </select>
      <input type="text" id="f-query" placeholder="搜索商品 / 供应商 / 任务 ID…" style="min-width:230px">
      <span class="chip" id="f-count"></span>
    </div>
    <div class="card" id="tasks-table"></div>
  `;

  const statusSelect = container.querySelector("#f-status");
  const queryInput = container.querySelector("#f-query");
  statusSelect.addEventListener("change", draw);
  queryInput.addEventListener("input", draw);

  draw();

  function draw() {
    const status = statusSelect.value;
    const query = queryInput.value.trim().toLowerCase();
    const rows = tasks.filter((task) => {
      if (status && task.status !== status) return false;
      if (query) {
        const haystack = [
          task.task_id,
          task.request?.product_name,
          task.recommended_supplier,
        ].filter(Boolean).join(" ").toLowerCase();
        if (!haystack.includes(query)) return false;
      }
      return true;
    });
    container.querySelector("#f-count").textContent = `${rows.length} / ${tasks.length}`;
    const table = container.querySelector("#tasks-table");
    if (!rows.length) {
      table.innerHTML = `<div class="empty">没有匹配的任务 —— 到 <a href="#/run">新建采购</a> 发起一次</div>`;
      return;
    }
    table.innerHTML = `<div class="table-wrap"><table class="data">
      <thead><tr>
        <th>任务</th><th>商品 × 数量</th><th>状态</th><th>推荐供应商</th>
        <th class="num">预估总额</th><th class="num">得分</th><th>拆分</th><th>创建时间</th>
      </tr></thead>
      <tbody>${rows.map((task) => `
        <tr class="clickable" data-task="${esc(task.task_id)}">
          <td class="mono">${esc(shortId(task.task_id))}</td>
          <td>${esc(task.request?.product_name || "—")} × ${task.request?.quantity ?? "?"}</td>
          <td>${statusPill(task.status)}${task.partial_result ? ' <span class="pill pill-warn">部分</span>' : ""}</td>
          <td>${task.recommended_supplier ? `<strong>${esc(task.recommended_supplier)}</strong>` : '<span class="dim">—</span>'}</td>
          <td class="num">${task.estimated_total ? esc(fmtMoney(task.estimated_total)) : "—"}</td>
          <td class="num">${task.score ? esc(task.score) : "—"}</td>
          <td>${task.has_split ? '<span class="pill pill-accent">拆分</span>' : '<span class="dim">—</span>'}</td>
          <td class="dim">${esc(fmtTime(task.created_at))}</td>
        </tr>`).join("")}
      </tbody></table></div>`;
    table.querySelectorAll("[data-task]").forEach((row) => {
      row.addEventListener("click", () => { location.hash = `#/tasks/${row.dataset.task}`; });
    });
  }
}
