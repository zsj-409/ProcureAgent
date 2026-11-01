// SPA router: hash-based.

import { esc } from "./format.js";
import * as overview from "./views/overview.js";
import * as run from "./views/run.js";
import * as tasks from "./views/tasks.js";
import * as taskDetail from "./views/taskDetail.js";
import * as suppliers from "./views/suppliers.js";

const routes = [
  { pattern: /^#\/$/, view: overview.render, nav: "#/" },
  { pattern: /^#\/run$/, view: run.render, nav: "#/run" },
  { pattern: /^#\/tasks$/, view: tasks.render, nav: "#/tasks" },
  { pattern: /^#\/tasks\/([\w-]+)$/, view: taskDetail.render, nav: "#/tasks", params: (m) => ({ id: m[1] }) },
  { pattern: /^#\/suppliers$/, view: suppliers.render, nav: "#/suppliers" },
];

const page = document.getElementById("page");

let pollTimer = null;
export function registerPoll(fn, intervalMs) {
  clearPoll();
  pollTimer = setInterval(fn, intervalMs);
}
export function clearPoll() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

async function route() {
  clearPoll();
  const hash = location.hash || "#/";
  const match = routes.find((route_) => route_.pattern.test(hash));
  document.querySelectorAll("#nav a").forEach((link) => {
    link.classList.toggle("active", match ? link.dataset.route === match.nav : false);
  });
  if (!match) {
    page.innerHTML = `
      <div class="empty" style="margin-top:60px">
        <p style="font-size:15px;margin:0 0 6px">页面不存在：${esc(hash)}</p>
        <a href="#/">← 返回总览</a>
      </div>`;
    return;
  }
  const params = match.params ? match.params(hash.match(match.pattern)) : {};
  try {
    await match.view(page, params);
  } catch (error) {
    page.insertAdjacentHTML("beforeend", `<div class="error-box">页面渲染出错：${esc(error.message)}</div>`);
  }
  window.scrollTo(0, 0);
}

window.addEventListener("hashchange", route);
route();
