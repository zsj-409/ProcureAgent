// Observatory: 5 labs reading committed experiment artifacts (no re-run).

import { esc } from "../format.js";

async function api(path) {
  const res = await fetch(path, {
    headers: { "X-Demo-Actor": "demo-approver" },  // isolated demo identity
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

const LABS = [
  {
    id: "flight",
    label: "Execution Flight Recorder",
    desc: "每任务真实执行步骤（live，需认证）",
    path: "flight-recorder",
  },
  {
    id: "chaos",
    label: "Chaos Recovery Lab",
    desc: "真实浏览器故障注入结果（Phase 4）",
    path: "chaos-lab",
  },
  {
    id: "optimization",
    label: "Procurement Optimization",
    desc: "Greedy / Cheapest / Optimizer 对比（Phase 5/6）",
    path: "optimization-comparison",
  },
  {
    id: "experiment",
    label: "Agent Experiment Lab",
    desc: "恢复策略对照（Phase 7，120 案例 × 3 策略）",
    path: "experiment-lab",
  },
  {
    id: "approvals",
    label: "Approval & Policy Inspector",
    desc: "审批记录 + 版本绑定（实时）",
    path: "approval-inspector",
  },
  {
    id: "evalops",
    label: "EvalOps Release Gates",
    desc: "候选版本门禁 / Shadow / Promotion（Phase 10）",
    path: "evalops/release-gates",
  },
];

function renderLabContent(el, lab, body) {
  const boundary = body.honest_boundary
    ? `<div class="obs-boundary">边界：${esc(body.honest_boundary)}</div>`
    : "";
  if (lab.id === "flight") {
    const flights = body.flights || [];
    const table = flights.length
      ? "<table><tr><th>task</th><th>status</th><th>step</th><th>created</th></tr>"
        + flights.map((f) => `<tr><td>${esc(f.task_id)}</td><td>${esc(f.status)}</td><td>${esc(f.current_step)}</td><td>${esc(f.created_at)}</td></tr>`).join("")
        + "</table>"
      : "<p class='missing'>暂无任务轨迹（空状态，非零结果）</p>";
    const authz = body.authz_model ? `<div class="obs-boundary">授权模型：${esc(body.authz_model)}（单租户 demo；多租户对象级隔离 NOT IMPLEMENTED）</div>` : "";
    el.innerHTML = authz + table;
  } else if (lab.id === "chaos") {
    const rows = body.data?.per_case || [];
    const table = rows.length
      ? "<table><tr><th>case</th><th>fault</th><th>verified</th><th>recovery</th><th>containment</th></tr>"
        + rows.map((r) => `<tr><td>${esc(r.case)}</td><td>${esc(r.fault)}</td><td>${r.verified_success ? "✅" : "❌"}</td><td>${r.recovery_success ? "✅" : "—"}</td><td>${r.safe_containment ? "🛑" : "—"}</td></tr>`).join("")
        + "</table>"
      : "<p class='missing'>Missing：无 portal 恢复结果数据（artifact 缺失或为空）</p>";
    el.innerHTML = boundary + table;
  } else if (lab.id === "optimization") {
    const cases = body.data?.per_case || [];
    const table = cases.length
      ? "<table><tr><th>case</th><th>greedy</th><th>cheapest</th><th>optimizer</th><th>feasibility</th></tr>"
        + cases.map((c) => `<tr><td>${esc(c.case)}</td><td>${esc(c.greedy_cost)}</td><td>${esc(c.cheapest_cost)}</td><td>${esc(c.optimizer_cost)}</td><td>${esc(c.optimizer_feasibility)}</td></tr>`).join("")
        + "</table>"
      : "<p class='missing'>Missing：无优化对比数据（artifact 缺失或为空）</p>";
    el.innerHTML = boundary + table;
  } else if (lab.id === "experiment") {
    const rows = body.data?.results || [];
    const byPolicy = {};
    rows.forEach((r) => {
      byPolicy[r.policy] = byPolicy[r.policy] || { n: 0, rec: 0, stop: 0 };
      byPolicy[r.policy].n++;
      if (r.recovered) byPolicy[r.policy].rec++;
      if (r.safe_stop_ok) byPolicy[r.policy].stop++;
    });
    const table = Object.keys(byPolicy).length
      ? "<table><tr><th>policy</th><th>n</th><th>recovered</th><th>safe_stop</th></tr>"
        + Object.entries(byPolicy).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${v.n}</td><td>${v.rec}/${v.n}</td><td>${v.stop}/${v.n}</td></tr>`).join("")
        + "</table>"
      : "<p class='missing'>Missing：无策略对照数据（artifact 缺失或为空）</p>";
    el.innerHTML = boundary + table;
  } else if (lab.id === "approvals") {
    const byTask = body.by_task || {};
    const count = body.approval_count || 0;
    const table = "<table><tr><th>task</th><th>approvals</th></tr>"
      + Object.entries(byTask).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${v.length} 条</td></tr>`).join("")
      + "</table>";
    el.innerHTML =
      `<div class="obs-boundary">审批总数：${count}（绑定 recommendation version，可检测失效版本）</div>`
      + (Object.keys(byTask).length ? table : "<p class='missing'>暂无审批记录（空状态，非零结果）</p>");
  } else if (lab.id === "evalops") {
    const unsafe = body.data?.unsafe_candidate;
    const safe = body.data?.safe_candidate;
    if (!unsafe) {
      el.innerHTML = `<div class="obs-boundary">${esc(body.honest_boundary || "")}</div>
        <p class="missing">Missing：无 Release Gate 结果（artifact 缺失）</p>`;
      return;
    }
    // Unsafe candidate MUST be shown as REJECTED — never an all-green board.
    const verdictBadge = (v) => v === "REJECTED"
      ? "<span style='color:#d33;font-weight:bold'>⛔ REJECTED</span>"
      : v === "QUALIFIED" ? "<span style='color:#3a3;font-weight:bold'>✅ QUALIFIED</span>"
      : esc(v);
    const gateRows = (c) => (c?.gates || []).map((g) =>
      `<tr><td>${esc(g.gate_name)}</td><td>${esc(g.gate_status)}</td>
       <td>${esc(g.measured_value ?? "-")}</td><td>${esc(g.baseline_value ?? "-")}</td>
       <td>${esc(g.failure_reason || "-")}</td></tr>`).join("");
    el.innerHTML = `
      <div class="obs-boundary">${esc(body.honest_boundary || "")}</div>
      <h3>Unsafe Candidate（故意放宽硬预算）: ${verdictBadge(unsafe.summary.verdict)}</h3>
      <p class="dim">失败门禁: ${esc((unsafe.summary.failed_gates || []).join(", ") || "无")}</p>
      <table><tr><th>gate</th><th>status</th><th>measured</th><th>baseline</th><th>reason</th></tr>
        ${gateRows(unsafe)}</table>
      <h3>Safe Baseline: ${verdictBadge(safe.summary.verdict)}</h3>
      <table><tr><th>gate</th><th>status</th><th>measured</th><th>baseline</th><th>reason</th></tr>
        ${gateRows(safe)}</table>
      <p class="dim">每个数字对应 Evidence Artifact（docs/eval/release_gate_results.json）。</p>`;
  }
}

export async function render() {
  const page = document.getElementById("page");
  page.innerHTML = `<h1>Experiment Observatory</h1>
    <p class="dim">只读展示已提交的机器可读实验产物（docs/eval/），不重新运行 Benchmark。</p>
    <div class="obs-tabs">
      ${LABS.map((l) => `<button class="obs-tab" data-lab="${l.id}">${esc(l.label)}</button>`).join("")}
    </div>
    <div id="obs-content" class="obs-content">
      <p class="dim">选择一个 Lab 查看已提交的实验产物与诚实边界说明。</p>
    </div>`;

  const content = document.getElementById("obs-content");
  document.querySelectorAll("[data-lab]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const lab = LABS.find((l) => l.id === btn.dataset.lab);
      if (!lab) return;
      content.innerHTML = "<p class='dim'>加载中…</p>";
      try {
        const body = await api("/api/v1/observatory/" + lab.path);
        renderLabContent(content, lab, body);
      } catch (err) {
        content.innerHTML = `<p class="err">加载失败（${esc(String(err))}）。若 artifact 缺失，请检查 docs/eval/。</p>`;
      }
    });
  });
}
