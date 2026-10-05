// Task detail page: state flow, request, plan, result, approvals, replay, trace.

import { getJSON, postJSON } from "../api.js";
import { registerPoll, clearPoll } from "../app.js";
import { esc, fmtMoney, fmtTime, shortId, statusPill, stepFlow, toast } from "../format.js";
import { renderResult } from "./result.js";

const ACTIVE_STATUSES = ["PENDING", "PLANNING", "COLLECTING", "SCORING"];

export async function render(container, params) {
  clearPoll();
  container.innerHTML = `<div class="loading">加载任务详情…</div>`;
  await draw(container, params.id, true);

  async function draw(containerArg, taskId, firstLoad) {
    let state;
    try {
      state = await getJSON(`/api/v1/tasks/${taskId}`);
    } catch (error) {
      containerArg.innerHTML = `
        <div class="error-box">加载失败：${esc(error.message)}</div>
        <a class="btn ghost" href="#/tasks">← 返回任务列表</a>`;
      return;
    }
    const summary = await getSummary(params.id);
    const requestData = summary ? summary.request : null;
    const plan = summary ? summary.plan : null;

    if (firstLoad) {
      containerArg.innerHTML = `<div id="detail-body"></div>`;
    }
    const body = containerArg.querySelector("#detail-body");
    body.innerHTML = `
      <div class="page-head">
        <div class="row">
          <h1>采购任务 <span class="mono" style="font-size:15px;color:var(--text-3)">${esc(shortId(state.task_id))}</span></h1>
          ${statusPill(state.status)}
          ${state.partial_result ? '<span class="pill pill-warn">部分结果</span>' : ""}
        </div>
        <p class="sub">创建于 ${esc(fmtTime(state.created_at))} · 更新于 ${esc(fmtTime(state.updated_at))}</p>
        ${stepFlow(state.status)}
        ${state.error ? `<div class="error-box" style="margin-top:10px">${esc(state.error)}
          ${state.status === "FAILED" ? '<button class="btn sm" id="resume-btn" style="margin-left:10px">↻ 从检查点恢复</button>' : ""}</div>` : ""}
      </div>

      <div class="grid cols-2">
        <div class="card">
          <h3>采购请求（解释器输出）</h3>
          ${requestData ? `<div class="kv">
            <dt>商品</dt><dd><strong>${esc(requestData.product_name)}</strong></dd>
            <dt>数量</dt><dd>${requestData.quantity}</dd>
            <dt>预算上限</dt><dd>${requestData.max_budget ? esc(fmtMoney(requestData.max_budget)) : "未设置"}</dd>
            <dt>偏好</dt><dd>${esc(requestData.preference)}</dd>
          </div>` : '<div class="empty">未持久化请求</div>'}
        </div>
        <div class="card">
          <h3>执行计划（${plan ? plan.steps.length : 0} 步）</h3>
          ${plan ? `<div class="chip-row">${plan.steps.map((step) =>
            `<span class="chip">${esc(step.action)}${step.supplier_id ? ` · ${esc(step.supplier_id)}` : ""}</span>`
          ).join("")}</div>
          <p class="card-hint" style="margin:10px 0 0">COLLECT 步骤有界并发执行；非 COLLECT 步骤由确定性流水线完成。</p>`
          : '<div class="empty">计划尚未生成</div>'}
        </div>
      </div>

      <h2 class="section">结果</h2>
      <div id="result-zone"></div>
    `;

    const resume = body.querySelector("#resume-btn");
    if (resume) {
      resume.addEventListener("click", async () => {
        resume.disabled = true;
        try {
          await postJSON(`/api/v1/tasks/${taskId}/resume`);
          toast("已从检查点恢复执行");
          await draw(containerArg, taskId, false);
        } catch (error) {
          toast(`恢复失败：${error.message}`, true);
          resume.disabled = false;
        }
      });
    }

    const zone = body.querySelector("#result-zone");
    if (ACTIVE_STATUSES.includes(state.status)) {
      zone.innerHTML = '<div class="card"><div class="loading">执行中 —— 每一步都在写入检查点…</div></div>';
      registerPoll(() => draw(containerArg, taskId, false), 1800);
    } else {
      clearPoll();
      await renderResult(zone, taskId);
    }
  }

  async function getSummary(taskId) {
    try {
      const tasks = await getJSON(`/api/v1/tasks?limit=200`);
      return tasks.find((item) => item.task_id === taskId) || null;
    } catch {
      return null;
    }
  }
}
