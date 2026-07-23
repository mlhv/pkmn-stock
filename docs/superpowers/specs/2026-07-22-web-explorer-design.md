# Web research explorer — design (Plan 1 of 2: read-only viewer)

**Goal:** A real full-stack research explorer for pkmn-quant: a typed FastAPI
read-only API over the existing artifacts/registry, and a React + TypeScript
SPA that browses the registry, drills into walk-forward runs, and presents
the rigor/evaluate strategy-zoo verdict. Local-first, deploy-ready.

**Context:** Brainstormed 2026-07-22. The frontend work was decomposed into
two plans: this spec (Plan 1, read-only viewer) and a later Plan 2 that adds
the job-runner (POST endpoints to launch backtests/walkforwards with live
progress). The operational tabs (prices, portfolio, signals, daily), cloud
hosting, and retiring the Streamlit dashboard are out of scope for both.
Stack chosen by the user: FastAPI + React/TS, the genuine full-stack signal.

## Architecture

Two pieces in one repository, one-directional and read-only:

```
React (web/) --HTTP/JSON--> FastAPI (src/pkmn_quant/api/) --> existing
loaders (load_runs, load_walkforward_json) + polars --> gitignored data/
```

- **Backend** lives under `src/pkmn_quant/api/` so it is inside the Python
  package and covered by the existing `ruff`/`mypy --strict`/`pytest`
  gates (the Streamlit dashboard under `app/` is deliberately ungated; the
  API is not — it is real, tested engineering). It reuses `load_runs`,
  `load_walkforward_json`, and polars for parquet. New optional dependency
  group `api` (fastapi + uvicorn), mirroring `dashboard`/`viz`.
- **Frontend** lives in a new top-level `web/` directory: React + TypeScript
  + Vite, its own `package.json`/`tsconfig`/vitest, NOT part of the Python
  package. Not gitignored (committed source); `web/node_modules` and
  `web/dist` are gitignored.
- **Dev ergonomics:** two processes (`uvicorn pkmn_quant.api:app --reload`
  and `vite` proxying `/api` to the backend), wrapped in ONE command (a
  `justfile`/`Makefile` target or `docker-compose.yml`) so "run the app" is
  a single invocation. Exact wrapper chosen at plan time; the requirement is
  one command.
- The Streamlit dashboard (`app/dashboard.py`) is untouched and keeps
  working; parity + retirement is a later plan.

## Backend API

FastAPI with Pydantic response models (typed contracts that also produce the
OpenAPI schema the frontend types derive from). The registry is the index;
detail endpoints resolve a run's `artifact_path` from its registry record.

- `GET /api/runs` — registry records, newest first; optional `command` and
  `strategy` query filters. Returns the list of run summaries.
- `GET /api/runs/{run_id}` — one full run record (config, results, git
  sha/dirty, data fingerprint, runtime); 404 if unknown.
- `GET /api/walkforward/{run_id}` — the walk-forward artifact for that run:
  summary metrics, per-fold rows (IS/OOS windows, chosen params, IS/OOS
  returns), the rigor CI block if present, and the stitched OOS equity
  curve as a JSON series (date, equity). 404 if the run is not a
  walkforward or its artifact is missing.
- `GET /api/evaluate/{run_id}` — the evaluate artifact: per-strategy
  {total_return, CI, sharpe, deflated Sharpe}, the Reality Check p-value,
  benchmark name, aligned window (start/end/n_days), and bootstrap params.
- `GET /api/strategies` — the strategy registry names + THESIS text (from
  `live/report.py`), for the comparison view's labels.

Contracts: every endpoint has a Pydantic response model. Missing artifacts
and unknown ids are clean 404s with a JSON `detail`; bad query params are
422 (FastAPI default). No filesystem paths beyond artifact directory names
leak to the client. A `root`/data directory is resolved once at app
startup (defaults to the repo root, overridable by env for tests/deploy).

## Frontend

React + TypeScript + Vite SPA, three core routes:

1. **Runs browser** (`/`): a filterable, sortable table of registry runs —
   command, strategy, headline result (e.g. stitched OOS return or Reality
   Check p), git SHA (short), recorded-at date. Row click routes to the
   matching detail view by run_id.
2. **Walk-forward detail** (`/walkforward/:runId`): the stitched OOS equity
   curve with the bootstrap CI band (from the rigor block) as a shaded
   area; the fold table (IS window, OOS window, params, IS ret, OOS ret);
   and a summary-metrics panel.
3. **Rigor comparison** (`/evaluate/:runId`): the strategy-zoo verdict —
   a sortable table of each strategy's OOS return, 95% CI, annualized
   Sharpe, and deflated Sharpe, with the White's Reality Check p-value as
   the headline number and the mark-smoothing caveat shown inline.

- **Charting:** Recharts, styled to the dataviz palette (categorical slots,
  theme-aware light/dark, thin marks, recessive axes). Follow the dataviz
  skill's palette and mark specs.
- **Shared:** a typed API client module (types derived from / checked
  against the backend OpenAPI schema), a nav-shell layout, and explicit
  loading / empty / error states on every screen (mirroring the dashboard's
  "No runs found" info pattern).
- **Honesty carries over:** any Sharpe-derived figure shown in the UI
  carries the mark-smoothing caveat, as in the reports and findings.

## Error handling

- API: 404 (unknown run_id / missing artifact) and 422 (bad params) with
  JSON `detail`; never a stack trace or absolute path to the client. A run
  that exists in the registry but whose artifact directory is gone returns
  a 404 with a clear message, not a 500.
- Frontend: per-screen error, empty, and loading states; a failed fetch
  shows a retryable error, not a blank screen. Unknown route → a not-found
  screen.

## Testing

- **Backend:** pytest with FastAPI `TestClient` over synthetic artifacts
  written into a `tmp_path` data root (same fixture style as the CLI/eval
  tests): every endpoint's happy path, the 404 paths (unknown id, missing
  artifact, wrong command type), and the filter query params. The four
  gates (`pytest`/`ruff`/`ruff format`/`mypy --strict`) extend to the new
  Python automatically.
- **Frontend:** Vitest + React Testing Library component tests (table
  rendering/sorting/filtering, chart data shaping, loading/error states
  with a mocked client); `tsc --noEmit` type-check; and a contract test
  that the frontend API types match the backend's emitted OpenAPI schema so
  the two cannot silently drift.
- **CI:** a new Node job (install, `tsc --noEmit`, vitest, `vite build`)
  runs beside the existing Python gates; the Python API tests run in the
  existing job. Both must pass.

## Out of scope (later plans)

- The job-runner / trigger-runs (Plan 2): POST endpoints, background jobs,
  live progress, concurrency limits.
- Operational surfaces: prices, portfolio, signals, daily-runs tabs.
- Cloud hosting / live URL / committed data snapshot.
- Retiring the Streamlit dashboard.
- Auth (the read-only local app needs none).
