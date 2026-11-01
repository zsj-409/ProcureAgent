# Changelog

## v2.0.0 — Deep-Dive Release

The v1 MVP implemented the procurement-agent idea only at its surface; v2 deepens it along the original browser-use intent.

### Adaptive portal runtime (the browser-use core, restored and bounded)

- New `app/executors/portal_agent.py`: numbered DOM snapshots (inputs/selects/buttons/tables/card grids), a strict action vocabulary (`fill`/`select`/`click`/`press_enter`/`done`), and deterministic quote extraction (header-keyword column mapping for tables, anchored regex parsing for cards). Elements are addressed by per-kind ordinal, so randomized or drifting markup cannot break the agent.
- Hard guards: bounded action budget (`PORTAL_MAX_ACTIONS`), no navigation actions, and form values restricted to the product name or enumerated select options — untrusted page text can never drive form input.
- Three-level escalation ladder in `PortalExecutor`: fixed selector script → heuristic agent (no LLM) → LLM agent over the snapshot. Structural failures escalate immediately (no wasted retries); only transient failures retry. One browser session is shared across the whole ladder.
- Every escalation and adaptive step is traced; each step saves a JPEG frame under `data/portal_frames/` for UI replay.

### Mock portal matrix (proves the self-healing)

- `/portal?layout=v2`: card-grid catalog without `<table>`, category select, pagination — the fixed script cannot work there.
- `?chaos=<seed>`: per-seed randomized ids/classes plus an interstitial session-check gate. Fixed script provably fails; adaptive agent provably recovers (`click gate → fill → extract`).

### Procurement depth

- Split-award optimization: when no single supplier covers the quantity, the order is split deterministically (greedy by weighted score, ≤3 lines) with an explicit `shortfall` field.
- `ExecutionValidator` no longer kills tasks on stock shortfall (that is what split award is for); a shortfall always routes to human approval instead of auto-approving a partial order.
- Supplier collection is bounded-concurrency parallel (`COLLECT_CONCURRENCY`), preserving per-supplier checkpoints and idempotency.

### Runtime productization

- `BackgroundTaskRunner`: background tasks run on dedicated worker threads with their own event loops (works under uvicorn and TestClient alike); `POST /api/v1/agent/run?wait=false` returns immediately for polling.
- New endpoints: task list, task reject, approval history, supplier health probes, portal replay manifests, overview counters.
- Per-channel step timeouts (portal 40 s / API 15 s) and a larger task budget (150 s) so browser-backed steps fit.
- Performance: a 5-supplier task dropped from ~80 s to ~5 s via fail-fast escalation, shared browser sessions, and fast selector probes (3 s) in the fixed script.
- Interpreter: the no-LLM fallback now strips English preference/budget phrases as well as Chinese ones.
- Added `REJECTED` task status and a rejection endpoint with approval history.

### Web Workbench

- Self-contained SPA (`app/webui/static/`, no build step, no CDN): overview, new-task flow with live progress polling, task list, task detail with quote comparison/split award/approval gate/execution trace, portal automation replay with per-step JPEG frames, and supplier channel cards with live health probes.
- `/portal-frames` static mount (registered before the SPA catch-all) serves replay frames.

### Quality

- Test suite: 23 → **38 passing** (portal agent, escalation, chaos self-healing, split award, background flow, reject flow, health probes, replay).
- Ruff configured (`pyproject.toml`) and clean; bilingual fullwidth punctuation intentionally allowed.
- Version bumped to 2.0.0 in `app/main.py`.
