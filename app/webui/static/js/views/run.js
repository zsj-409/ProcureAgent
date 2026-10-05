// New-task page: natural-language input, live progress, full result.

import { getJSON, postJSON } from "../api.js";
import { clearPoll, registerPoll } from "../app.js";
import { esc, fmtMoney, shortId, statusPill, stepFlow, toast } from "../format.js";
import { renderResult } from "./result.js";

const EXAMPLES = [
  "buy 2 MX Master 3S, prefer lowest price",
  "买 30 个 MX Master 3S，预算 2000",
  "buy 5 Logitech Brio 4K with budget under 1200",
  "买 8 个 MX Keys S，交期优先",
];

let currentTaskId = null;

export async function render(container) {
  currentTaskId = null;
  clearPoll();
  container.innerHTML = `
    <div class="page-head">
      <h1>新建采购</h1>
      <p class="sub">用一句自然语言描述采购需求 —— 解释器会拆出商品、数量、预算与偏好</p>
    </div>
    <div class="card">
      <label class="field"><span>采购需求（中英文皆可）</span>
        <textarea id="nl-input" placeholder="例如：买 30 个 MX Master 3S，预算 2000，价格优先"></textarea></label>
      <div class="chip-row" style="margin:2px 0 12px">
        ${EXAMPLES.map((example) => `<span class="chip clickable" data-example="${esc(example)}">${esc(example)}</span>`).join("")}
      </div>
      <button class="btn" id="submit-btn">提交采购任务</button>
      <span class="chip" style="margin-left:8px" id="llm-chip">?</span>
    </div>
    <div id="progress-zone"></div>
    <div id="result-zone"></div>
  `;

  const llmChip = container.querySelector("#llm-chip");
  try {
    const overview = await getJSON("/api/v1/overview");
    llmChip.textContent = overview.llm_configured ? "LLM 已配置" : "LLM 未配置 · 确定性回退";
  } catch {
    llmChip.textContent = "服务未连接";
  }

  container.querySelectorAll("[data-example]").forEach((chip) => {
    chip.addEventListener("click", () => {
      container.querySelector("#nl-input").value = chip.dataset.example;
    });
  });

  container.querySelector("#submit-btn").addEventListener("click", submit);

  async function submit() {
    const input = container.querySelector("#nl-input");
    const message = input.value.trim();
    if (!message) {
      toast("请输入采购需求", true);
      return;
    }
    const button = container.querySelector("#submit-btn");
    button.disabled = true;
    let payload;
    try {
      payload = await postJSON("/api/v1/agent/run?wait=false", { message });
    } catch (error) {
      toast(`提交失败：${error.message}`, true);
      button.disabled = false;
      return;
    }
    button.disabled = false;
    currentTaskId = payload.task_id;
    toast(`任务已创建：${shortId(payload.task_id)}`);
    showProgress(payload);
    pollOnce();
    registerPoll(pollOnce, 1500);
  }

  async function pollOnce() {
    if (!currentTaskId) return;
    let state;
    try {
      state = await getJSON(`/api/v1/tasks/${currentTaskId}`);
    } catch {
      return;
    }
    const progressZone = container.querySelector("#progress-zone");
    if (progressZone) {
      progressZone.innerHTML = `
        <div class="card">
          <h3>任务 <span class="mono">${esc(shortId(currentTaskId))}</span> ${statusPill(state.status)}</h3>
          ${stepFlow(state.status)}
          <p class="card-hint" style="margin:8px 0 0">当前步骤：${esc(state.current_step || "—")} ·
            <a href="#/tasks/${esc(currentTaskId)}">打开完整任务详情 →</a></p>
        </div>`;
    }
    const terminal = ["COMPLETED", "WAITING_APPROVAL", "FAILED", "APPROVED", "REJECTED"];
    if (terminal.includes(state.status)) {
      clearPoll();
      const zone = container.querySelector("#result-zone");
      if (zone) {
        await renderResult(zone, currentTaskId);
      }
    }
  }

  function showProgress(payload) {
    container.querySelector("#progress-zone").innerHTML = `
      <div class="card">
        <h3>任务 <span class="mono">${esc(shortId(payload.task_id))}</span> 已提交</h3>
        <div class="kv" style="margin-top:8px">
          <dt>解析结果</dt>
          <dd>${esc(payload.interpreted.product_name)} × ${esc(payload.interpreted.quantity)}
            ${payload.interpreted.max_budget ? `· 预算 ${esc(fmtMoney(payload.interpreted.max_budget))}` : ""}
            · 偏好 ${esc(payload.interpreted.preference)}</dd>
        </div>
        <div class="loading">采集报价中（API 并行 + 门户自适应）…</div>
      </div>`;
    container.querySelector("#result-zone").innerHTML = "";
  }
}
