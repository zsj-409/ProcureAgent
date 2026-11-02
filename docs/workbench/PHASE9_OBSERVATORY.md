# Phase 9 — Workbench & Experiment Observatory

- **Date**: 2026-11-02
- **Baseline HEAD**: `bd1e2b9` (Phase 7 complete, 278 passed)

## 1. What was added

| lab | endpoint | source |
|---|---|---|
| Execution Flight Recorder | `/api/v1/observatory/flight-recorder` | live task trace (real executed steps) |
| Chaos Recovery Lab | `/api/v1/observatory/chaos-lab` | Phase 4 `portal_recovery_results.json` |
| Approval & Policy Inspector | `/api/v1/observatory/approval-inspector` | live approval records + version binding |
| Procurement Optimization | `/api/v1/observatory/optimization-comparison` | Phase 5 `optimization_benchmark.json` |
| Agent Experiment Lab | `/api/v1/observatory/experiment-lab` | Phase 7 `recovery_policy_results.json` (120 cases) |

Plus `/api/v1/observatory/artifacts` listing all 14 committed artifacts.
Frontend: new `#/observatory` route + `views/observatory.js` (tabbed lab
viewer), nav entry, CSS.

**Design rule**: the Observatory only READS committed machine-readable
artifacts (`docs/eval/`) — it never re-runs benchmarks, never fabricates
curves, and labels every lab with its honest experiment boundary
(DEV/CONFIRMATION/TEST, n, REAL_LLM_NOT_RUN, saturation/negative results).

## 2. Seven interview demos — all reproducible

| # | demo | mechanism |
|---|---|---|
| 1 | one normal task | `#/run` → task → recommendation |
| 2 | supplier failure + recovery | portal fallback (PortalExecutor) |
| 3 | process crash + checkpoint resume | durable worker (Phase 2/3 tests) |
| 4 | portal page drift self-healing | semantic extract (Phase 4 e2e) |
| 5 | unauthorized approval rejected | default-deny guard (tested: 401 AUTH_REQUIRED, no state change) |
| 6 | legal split-award optimization | Observatory → Optimization lab (real CBC numbers) |
| 7 | policy ablation comparison | Observatory → Experiment lab (Phase 7, 120 cases) |

Demos never execute real purchases. Numbers come from committed artifacts
or a fresh isolated task. Verified by `tests/test_phase9_demo.py`.

## 3. Honest boundaries displayed

- Optimization lab: "cost saving computed ONLY on jointly fully-feasible
  pairs; cluster bootstrap CI [0.0, 6.4] — not significant at family level".
- Experiment lab: "success saturated (all policies 100% correct goal);
  cost-aware policies cut observations 2-4x; REAL_LLM_NOT_RUN".
- All labs show the artifact name + source path so results are traceable.

## 4. Verification

- 9 new tests (observatory API 6 + demo 3); **287 total passed**, ruff clean.
- Live server smoke test: `/api/v1/observatory/*` 200, artifacts 11/14
  present, `/js/views/observatory.js` served, index.html 200.
- The Observatory does not modify any state (read-only endpoints).

## 5. Demo Acceptance (Step 1) — three gaps closed

| priority | gap | resolution |
|---|---|---|
| P0 | Observatory data access | flight-recorder + approval-inspector now require an authenticated principal (401 AUTH_REQUIRED default-deny when auth_enabled; demo header allowlist in isolated demo config). Artifact labs stay public (committed, non-sensitive). Tests: 4 new auth tests. |
| P1 | 11/14 artifacts "missing" | Root cause: `_load` tried to JSON-parse CSV artifacts (optimization_comparison.csv, paired_method_comparison.csv, allocation_results.csv) and marked them missing. Fix: CSV artifacts reported present with `format: csv`. All 14 now show present. Test: `test_all_14_artifacts_present`. |
| P1 | frontend real interaction | Real-browser e2e (`test_phase9_workbench_e2e.py`) drives Chromium against a live app: clicks all 4 tabs, asserts data tables render with committed numbers, empty states show "Missing/暂无" (never a fake zero), 404 path shows an error state. Also fixed a real bug: lab tab click handlers were inside an inline `<script>` (never executed in innerHTML) — moved to the module's `render()`. |

**Verification**: 293 passed (was 287; +6: 4 auth + 1 artifact-count + 1 browser e2e),
ruff clean.

## 6. Reproducible demo route (fixed)

1. `#/observatory` → artifact list (14/14 present)
2. Chaos Recovery Lab → Phase 4 real-browser fault table
3. Procurement Optimization → CBC comparison with honest boundary
4. Agent Experiment Lab → Phase 7 120-case policy table
5. Approval Inspector → live approvals (demo identity header) or "暂无" empty state
6. Flight Recorder → live task trace (demo identity header)

## 7. Remaining work (later phases)

- Chaos Recovery Lab live re-run button (would require a real browser
  runner — deferred to avoid re-running benchmarks by default).
- Flight Recorder richer visualization (timeline, not just step list).
- Experiment Lab drill-down per family/template (data is already in the
  artifact; the UI shows policy-level aggregates).
