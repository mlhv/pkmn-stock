# Web trigger, background jobs, backtest detail — design (Plan 2c)

**Goal:** Trigger a backtest from the web frontend — pick any registered
strategy, set its hyperparameters, declare starting cash and initial card
holdings, choose a window — run it as a background job, and land on a
backtest detail screen that reads the result against a buy-and-hold
benchmark computed over the identical starting state.

**Context:** Brainstormed 2026-07-27. Third and final sub-plan of the
triggerable-backtests decomposition in
`2026-07-25-triggerable-backtests-design.md`. Plan 2a (native-engine
initial-holdings seeding) merged as `d2384be`; Plan 2b (the
`run_single_backtest` capability plus the widened `pkmn backtest` CLI)
merged as `95019b5`. This plan adds the web layer over that seam. Local
single-user, no auth. Walk-forward triggering stays out of scope.

## What changes conceptually

The API has been read-only since Plan 1. This plan makes it write-capable
for the first time: `POST /api/backtest` starts real work that writes
artifacts and appends registry records. Everything it triggers goes through
Plan 2b's `run_single_backtest`, so the web layer adds no engine or
capability logic of its own.

## Job layer

**`src/pkmn_quant/api/jobs.py`** — a `JobManager` owning a
`ThreadPoolExecutor(max_workers=1)` and an ordered map of job records
guarded by a lock. A single worker keeps memory predictable (each run
builds its own `PreparedMarket`), makes `queued` a real observable state,
and keeps tests deterministic.

A `Job` carries: `job_id`, `status` (`queued` → `running` →
`succeeded`/`failed`), the submitted request echoed back, `submitted_at` /
`started_at` / `finished_at`, `run_id` and `benchmark_run_id` once known,
and `error` on failure.

The executor is **injected into `create_app`** so tests can pass an inline
executor and observe transitions synchronously — no sleeping, no polling in
the test suite.

### Every job runs two backtests

1. The user's strategy → primary `run_id`.
2. `buy-and-hold-sealed` over the **identical window, identical cash,
   identical holdings, identical impact setting** → benchmark `run_id`.

Identical starting state is the point. If the benchmark did not receive the
seeded holdings, the two curves would start at different equity levels
(`cash` vs `cash + Σ qty·mark`) and the overlay would measure starting
inventory rather than strategy performance. With both seeded, the only
difference between the curves is the trading policy. The seeded positions
ride along inside the benchmark, since buy-and-hold never exits.

The benchmark runs **second** so it can be tagged `benchmark_for:
<primary run_id>` in its recorded config. That tag is the only linkage:
`GET /api/backtest/{run_id}` finds its overlay by locating the registry
record that points back at it. This requires one additive optional
parameter on `run_single_backtest` — `config_extra: dict | None`, merged
into the recorded config — with no behavior change for existing callers.

### Validation splits in two

- **Synchronous (HTTP 400, before anything is queued):** unknown strategy,
  out-of-bounds or unknown params (via `resolve_params`), unparseable or
  inverted dates, negative cash, malformed holdings rows.
- **Deferred (surfaces as a failed job):** holding asset not in the
  backtest universe, and holding with no mark on the window's first trading
  day. Both require building a `PreparedMarket`, which is too expensive to
  do inside a request handler.

Accepted tradeoff: a user with an untradeable holding waits for a job to
fail rather than getting instant feedback. The failed job carries the
capability layer's existing error message verbatim.

### Restart semantics

In-flight jobs die with the process; the job list is in-memory only. No
results are lost — completed runs are already durable in the run registry
with their artifacts on disk. A restart loses the job list, not the work.

## Seeded-holding exits

Strategies already act on seeded positions: Plan 2a made `opened_on` a real
input, so exit rules (dip-buyer `hold_days`/take-profit, xs-momentum
rebalance, sealed-accumulation take-profit) evaluate seeded holdings
exactly as engine-bought ones. No new engine capability is needed and none
is added — no forced-liquidation date, no scheduled sale.

What this plan adds is **visibility**: a fill that sells an asset present in
the run's seeded holdings is flagged `seeded: true`, and those days are
returned as `seeded_exits` so the detail screen can mark them on the equity
chart. Derived from data the run already produces, by comparing fills
against `config["holdings"]`.

## Endpoint surface

### New — job flow

- `POST /api/backtest` — validate cheaply, enqueue, return `202` with
  `JobStatus`.
- `GET /api/jobs` — all jobs, newest first.
- `GET /api/jobs/{job_id}` — one job; `404` if unknown.

### New — screens

- `GET /api/backtest/{run_id}` → `BacktestDetail`: config echo, summary
  metrics, the run's equity curve, the linked benchmark's `{run_id,
  equity_curve}` (nullable — a run predating this plan has none), `n_fills`,
  and `seeded_exits`.
- `GET /api/backtest/{run_id}/fills?offset=&limit=` — paginated fills, each
  row flagged `seeded`. Fills live on their own endpoint so a thousand-fill
  run does not bloat the detail payload.
- `GET /api/products?q=&limit=` → `ProductHit`: `product_id`, `name`,
  `kind`, available `sub_types`, `last_priced_on`. Backs the holdings
  autocomplete; `last_priced_on` lets the UI warn about a product with no
  recent marks before submission.

### Changed — existing

- `GET /api/strategies` gains a `params` array per strategy (`name`, `kind`,
  `default`, `low`, `high`, `log`) straight from Plan 2b's `ParamSpec`s, so
  the form generates itself and a seventh strategy needs no frontend
  change. It must also start including **`buy-and-hold`**, which the CLI
  accepts but which is absent from `REGISTRY` and therefore missing from
  today's catalog.
- `GET /api/runs` gains `include_benchmarks` (default `false`), hiding
  paired benchmark records so the runs list does not double in length.

### Contract discipline

Every new response model gets a committed fixture in `web/tests/fixtures/`
validated against its Pydantic model by `tests/api/test_contract.py`, as
with the Plan 1 endpoints. This is what stops the Python and TypeScript
sides drifting.

## Frontend

Three new routes alongside the existing runs browser, walk-forward detail,
and rigor compare.

**`/new` — run form.** Selecting a strategy generates its param inputs from
the `ParamSpec`s (number inputs bounded by `low`/`high`, placeholder showing
the default). Plus window, cash, impact toggle, and `kind` — shown only for
buy-and-hold, since it is ignored for every other strategy. The holdings row
editor does debounced type-ahead on product name, populates the `sub_type`
dropdown from the selected product, and takes quantity / avg_cost /
opened_on per row. Submit posts and navigates to the jobs view.

**`/jobs` — jobs table.** Polls roughly once a second **only while a job is
queued or running**, then stops. Succeeded rows link to the detail screen;
failed rows show the error inline, which is where deferred holdings
validation surfaces.

**`/backtest/:runId` — detail screen.** Metric tiles (total return, CAGR,
Sharpe, max drawdown), the equity chart carrying both the run and its
benchmark on shared axes with markers at seeded exits, the paginated fills
table with seeded sales flagged, and a config summary sufficient to
reproduce the run.

**Existing components:** `RunsBrowser` makes `command == "backtest"` rows
clickable (they link nowhere today). `EquityChart` gains a second series and
marker support. `Layout` gains the two nav links. `useAsync`/`AsyncState`
are reused; polling is a small addition alongside them.

## Deferred cleanup (carried from Plan 2b)

`app/dashboard.py`'s `benchmark_runs()` returns any non-`wf-` directory
containing `equity.parquet`, so every triggered run and every paired
benchmark would appear in its overlay selector under an opaque run id. It
gets driven off the run registry instead, labeled by strategy and window.

## Testing

**Python.** `jobs.py` unit-tested with an inline executor: state
transitions, failure capture, id uniqueness — deterministic, no sleeps. API
tests through `TestClient` over the small synthetic warehouse the existing
tests build: each 400 case (unknown strategy, out-of-bounds param, inverted
window, negative cash, malformed holding row); the happy path asserting
**two** runs land with the benchmark tagged `benchmark_for` and identical
cash/holdings/window/impact; detail returns the benchmark curve; fills
pagination and `seeded` flagging; products search including
`last_priced_on`; strategy params exposure including `buy-and-hold`; and the
`include_benchmarks` filter.

**Web.** Vitest per screen against a mocked client: the form re-renders its
inputs when the strategy changes; autocomplete debounces and selection
populates `sub_type`; submit posts the expected payload; the jobs view stops
polling when jobs go idle; the detail screen renders both series and pages
fills; backtest rows in the runs browser link through. Gated by the existing
`npm run check` and `npm run build` CI job.

## Out of scope

- Job cancellation, websockets/SSE, run deletion, auth.
- Walk-forward triggering (excluded by the parent spec).
- Any forced-liquidation or scheduled-sale engine capability.
- Retiring the Python engine (still the parity oracle; a separate future
  plan).

## Size note

Six endpoints touched and three new screens — comparable to the Plan 1 web
explorer. Expect roughly seven tasks. The natural checkpoint, if one is
wanted, is backend-complete (jobs, all endpoints, contract fixtures,
verifiable by tests and curl) before any React lands.
