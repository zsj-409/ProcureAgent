// Shared helpers for the ProcureAgent workbench.

export const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));

export const fmtTime = (iso) => {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
};

export const fmtMoney = (value) => {
  if (value === null || value === undefined || value === "") return "—";
  const num = Number(value);
  if (!Number.isFinite(num)) return String(value);
  return `$${num.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
};

export const shortId = (id) => String(id ?? "").slice(0, 10);

export const STATUS_TONES = {
  COMPLETED: "ok", APPROVED: "ok", SUCCEEDED: "ok",
  FAILED: "bad", REJECTED: "bad",
  WAITING_APPROVAL: "warn", PENDING: "muted",
  PLANNING: "info", COLLECTING: "info", SCORING: "info",
};

export const pill = (text, tone = "muted") =>
  `<span class="pill pill-${esc(tone)}">${esc(text)}</span>`;

export const statusPill = (status) => pill(status ?? "—", STATUS_TONES[status] ?? "muted");

export const STEP_ORDER = ["PLANNING", "COLLECTING", "SCORING", "WAITING_APPROVAL", "APPROVED", "COMPLETED"];
export const STEP_LABELS = {
  PLANNING: "规划", COLLECTING: "采集", SCORING: "评分",
  WAITING_APPROVAL: "待审批", APPROVED: "已批准", COMPLETED: "完成",
};

export function stepFlow(status) {
  if (status === "FAILED") {
    return `<div class="step-flow"><span class="step-node failed">执行失败</span></div>`;
  }
  if (status === "REJECTED") {
    return `<div class="step-flow"><span class="step-node failed">已驳回</span></div>`;
  }
  const activeIndex = STEP_ORDER.indexOf(status);
  return `<div class="step-flow">${STEP_ORDER.slice(0, 5).map((step, i) => {
    let cls = "";
    if (activeIndex > i) cls = "done";
    else if (activeIndex === i) cls = status === "COMPLETED" || status === "APPROVED" ? "done" : "active";
    if (status === "WAITING_APPROVAL" && step === "WAITING_APPROVAL") cls = "active";
    if (status === "COMPLETED" && (step === "APPROVED" || step === "COMPLETED")) cls = "done";
    return `<span class="step-node ${cls}">${esc(STEP_LABELS[step])}</span>${i < 4 ? '<span class="step-arrow">→</span>' : ""}`;
  }).join("")}</div>`;
}

export function toast(message, isError = false) {
  const node = document.getElementById("toast");
  node.textContent = message;
  node.classList.toggle("err", Boolean(isError));
  node.classList.remove("hidden");
  clearTimeout(node._timer);
  node._timer = setTimeout(() => node.classList.add("hidden"), 3200);
}

// Convert a stored filesystem frame path into the /portal-frames URL.
export function frameUrl(framePath) {
  if (!framePath) return null;
  const marker = "portal_frames/";
  const pos = String(framePath).replace(/\\/g, "/").indexOf(marker);
  if (pos < 0) return null;
  return `/portal-frames/${String(framePath).replace(/\\/g, "/").slice(pos + marker.length)}`;
}
