// Observatory: 5 labs reading committed experiment artifacts (no re-run).

import { esc } from "../format.js";

async function api(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

function tab(name, fn) {
  return `<button class="obs-tab" data-obs-tab="${name}">${fn.name}</button>`;
}

function renderLabs(body) {
  const labs = [
    {
      id: "chaos",
      label: "Chaos Recovery Lab",
      desc: "真实浏览器故障注入结果（Phase 4）",
      load: () => api("/api/v1/observatory/chaos-lab"),
    },
    {
      id: "optimization",
      label: "Procurement Optimization",
      desc: "Greedy / Cheapest / Optimizer 对比（Phase 5/6）",
      load: () => api("/api/v1/observatory/optimization-comparison"),
    },
    {
      id: "experiment",
      label: "Agent Experiment Lab",
      desc: "恢复策略对照（Phase 7，120 案例 × 3 策略）",
      load: () => api("/api/v1/observatory/experiment-lab"),
    },
    {
      id: "approvals",
      label: "Approval & Policy Inspector",
      desc: "审批记录 + 版本绑定（实时）",
      load: () => api("/api/v1/observatory/approval-inspector"),
    },
  ];
  return `
  <div class="obs-tabs">
    ${labs.map((l) => `<button class="obs-tab" data-lab="${l.id}">${esc(l.label)}</button>`).join("")}
  </div>
  <div id="obs-content" class="obs-content">
    <p class="dim">选择一个 Lab 查看已提交的实验产物与诚实边界说明。</p>
  </div>
  <script>
  document.querySelectorAll("[data-lab]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const labs = ${JSON.stringify(labs.map((l) => ({ id: l.id, label: l.label, desc: l.desc })))};
      const lab = labs.find((l) => l.id === btn.dataset.lab);
      const content = document.getElementById("obs-content");
      content.innerHTML = "<p class='dim'>加载中…</p>";
      try {
        const body = await api("/api/v1/observatory/" + lab.id === "approvals"
          ? "approval-inspector"
          : lab.id === "chaos" ? "chaos-lab"
          : lab.id === "optimization" ? "optimization-comparison"
          : "experiment-lab");
        renderLabContent(content, lab, body);
      } catch (err) {
        content.innerHTML = "<p class='err'>加载失败：" + esc(String(err)) + "</p>";
      }
    });
  });

  function renderLabContent(el, lab, body) {
    const boundary = body.honest_boundary ? \`<div class="obs-boundary">边界：\${esc(body.honest_boundary)}</div>\` : "";
    if (lab.id === "chaos") {
      const rows = body.data?.per_case || [];
      const table = rows.length
        ? "<table><tr><th>case</th><th>fault</th><th>verified</th><th>recovery</th><th>containment</th></tr>"
          + rows.map((r) => \`<tr><td>\${esc(r.case)}</td><td>\${esc(r.fault)}</td><td>\${r.verified_success ? "✅" : "❌"}</td><td>\${r.recovery_success ? "✅" : "—"}</td><td>\${r.safe_containment ? "🛑" : "—"}</td></tr>\`).join("")
          + "</table>"
        : "<p class='dim'>无数据</p>";
      el.innerHTML = boundary + table;
    } else if (lab.id === "optimization") {
      const cases = body.data?.per_case || [];
      const table = cases.length
        ? "<table><tr><th>case</th><th>greedy</th><th>cheapest</th><th>optimizer</th><th>feasibility</th></tr>"
          + cases.map((c) => \`<tr><td>\${esc(c.case)}</td><td>\${esc(c.greedy_cost)}</td><td>\${esc(c.cheapest_cost)}</td><td>\${esc(c.optimizer_cost)}</td><td>\${esc(c.optimizer_feasibility)}</td></tr>\`).join("")
          + "</table>"
        : "<p class='dim'>无数据</p>";
      el.innerHTML = boundary + table;
    } else if (lab.id === "experiment") {
      const rows = body.data?.results || [];
      const byPolicy = {};
      rows.forEach((r) => { byPolicy[r.policy] = byPolicy[r.policy] || { n: 0, rec: 0, stop: 0 }; byPolicy[r.policy].n++; if (r.recovered) byPolicy[r.policy].rec++; if (r.safe_stop_ok) byPolicy[r.policy].stop++; });
      const table = "<table><tr><th>policy</th><th>n</th><th>recovered</th><th>safe_stop</th></tr>"
        + Object.entries(byPolicy).map(([k, v]) => \`<tr><td>\${esc(k)}</td><td>\${v.n}</td><td>\${v.rec}/\${v.n}</td><td>\${v.stop}/\${v.n}</td></tr>\`).join("")
        + "</table>";
      el.innerHTML = boundary + table;
    } else if (lab.id === "approvals") {
      const byTask = body.by_task || {};
      const count = body.approval_count || 0;
      const table = "<table><tr><th>task</th><th>approvals</th></tr>"
        + Object.entries(byTask).map(([k, v]) => \`<tr><td>\${esc(k)}</td><td>\${v.length} 条</td></tr>\`).join("")
        + "</table>";
      el.innerHTML = \`<div class="obs-boundary">审批总数：\${count}（绑定 recommendation version，可检测失效版本）</div>\` + (table || "<p class='dim'>暂无审批记录</p>");
    }
  }
  </script>
  `;
}

export async function render() {
  const body = document.getElementById("page");
  body.innerHTML = `<h1>Experiment Observatory</h1>
    <p class="dim">只读展示已提交的机器可读实验产物（docs/eval/），不重新运行 Benchmark。</p>
    ${renderLabs()}`;
}
