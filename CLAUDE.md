# pkmn_quant — Pokemon card backtesting system

Algo-trading research system for Pokemon card prices (TCGplayer data via
tcgcsv.com). Design spec: `docs/superpowers/specs/2026-06-09-pkmn-quant-design.md`.

## Status (2026-07-20)

- Plans 1-4 merged to main. Plan 4 closed out the v1 spec: `walkforward.json`
  artifacts, `pkmn signals`, Streamlit dashboard, README.
- Plan 5 complete on feat/reinvest-loop (213 tests): reinvest loop ships the
  manual-entry JSONL ledger feeding real holdings into `pkmn signals`
  (strategy exit rules produce SELLs), `pkmn portfolio` CLI, `pkmn daily`
  scheduled loop with macOS notifications + launchd template
  (`scripts/com.pkmn-quant.daily.plist`), dashboard Portfolio tab with
  daily-runs alerts strip, paper-trading mode (`--paper`,
  executor-fidelity auto-recorded fills).
- Plan 6 complete on feat/short-horizon-research (235 tests):
  `Position.opened_on` engine field; dip-buyer and xs-momentum retrofitted
  from internal clocks onto `opened_on` (stateless, live-safe — documented
  bug fixes that change backtest numbers); new cost-aware-reversion strategy
  (entries must clear the round-trip cost hurdle); all four strategies are
  now PORTFOLIO_SAFE_STRATEGIES (usable with `pkmn signals --portfolio` and
  `pkmn daily --paper`).
- Walk-forward findings in `docs/research-findings-2026-07.md`: nothing beat
  buy-and-hold sealed (+151% OOS); sealed-accumulation +13.6% was the only
  positive active strategy. 2026-07-10 re-runs after opened_on fixes logged
  in the same file (new Plan 6 section).
- Plan 7 complete on feat/paper-dashboard-cleanup (245 tests + 3 dashboard tests;
  a fresh `uv run pytest` shows 245 passed, 3 skipped — the dashboard tests need
  `uv run --group dashboard pytest tests/test_dashboard.py`): `live/paper.py` extracts `plan_paper_fills`
  planner from cli.py; paper `daily.json` `n_buys`/`n_sells` now count recorded
  fills, not recommendations (a paper day where everything clips to zero sends
  no notification); public ledger API `load_events`/`replay`; dashboard Portfolio
  tab has a Real/Paper toggle; headless dashboard tests in `tests/test_dashboard.py`.
- Plan 8 complete on feat/ml-ranker (270 tests; a fresh `uv run pytest` shows
  267 passed, 3 skipped — the 3 dashboard tests need
  `uv run --group dashboard pytest tests/test_dashboard.py`): engine perf
  (~1.9x, marks cursor + date partition); `research/features.py` (8
  leakage-bounded features, regression-tested); `strategies/ml_ranker.py`
  (HistGradientBoostingRegressor trained in-loop behind the anti-look-ahead
  wall, stateless); one real sklearn 1.9 bug found and fixed (all-NaN feature
  columns from early folds crash the binner — fixed by fitting/predicting on
  not-all-null subset, commit ca77f47); scikit-learn added as a main
  dependency. Walk-forward result: +6.0% stitched OOS, +7.75 CAGR-pt
  overfitting gap; second-best active strategy, first besides
  sealed-accumulation with positive OOS return; not close to buy-and-hold
  sealed (+151.1%). Full findings in `docs/research-findings-2026-07.md`.
- Plan 9 complete on feat/impact-and-runs (306 tests): walk-the-spread market
  impact cost model (`engine/quotes.py`) — buys walk from `market` toward
  `mid`, sells from `market` toward `low`, scaled by `qty/(2*daily_liquidity_
  cap)`; ON by default for `pkmn backtest`/`walkforward`/`daily`, engine
  default off, opt out per-command with `--no-impact`. Experiment registry
  (`research/runs.py`): every backtest/walkforward appends a record (run_id,
  config hash, git SHA+dirty, data fingerprint, results) to
  `data/runs/registry.jsonl`, inspectable via `pkmn runs list`/`pkmn runs
  show`. Impact-on re-run (2026-07-14) confirms the Plan 9 hypothesis
  strongly: buy-and-hold sealed barely moves (+186.0% flat-cost → +183.7%
  impact-on backtest total return; ~39 fills total), while BOTH previously-
  positive active strategies flip negative OOS (sealed-accumulation +13.6% →
  −7.4% stitched; ml-ranker +6.0% → −7.5% stitched) — their apparent edge was
  living inside the friction the impact model now prices in. Full findings in
  `docs/research-findings-2026-07.md` (Plan 9 section).
- Plan 10 complete on feat/cpp-engine (328 tests + 3 dashboard tests + 23
  Catch2 tests): a native C++ engine (`cpp/`, nanobind-bound as
  `pkmn_quant._engine`) with ports of all five strategies, selectable via
  `--engine cpp` on `pkmn backtest`/`walkforward` at the time (Plan 2b later
  removed `--engine` from `pkmn backtest`, which is now native-only;
  `pkmn walkforward` still has it); anything else (a raw
  Python `Strategy`, e.g. ml-ranker) still runs correctly on the C++ event
  loop through a per-bar callback bridge. Full-data acceptance
  (`scripts/parity_full.py`, real 874-day warehouse, 2024-03-01..2026-06-30):
  all five native strategies plus the ml-ranker bridge PASS bit-for-bit
  (exact equity curve and exact per-fill day/asset/quantity/price/fees/
  impact). Measured speedup (`scripts/bench_engines.py`, best of 3, full
  range, impact on; total wall-clock including the one-time polars
  load/flatten, not engine-loop-only): buy-and-hold 2.4x, sealed-accumulation
  3.4x, dip-buyer 7.6x. The first full-data run found a real bug the
  synthetic fixtures never exercised: `products.parquet` is missing rows
  for 40 of 4,687 priced product_ids inside the backtest window (7,565
  price rows). Warehouse-wide, past the window, the gap is far larger —
  re-measured 2026-07-29 at 2,017 of 6,665. The cause originally recorded
  here (the one-time catalog fetch missing new sets) was wrong: measured,
  that explains only 14 of 2,017. The real cause is that TCGplayer
  **re-files products between groups**, so a promo whose prices were
  archived under a tracked set now lists under an untracked group
  (`Miscellaneous Cards & Products`, `Blister Exclusives`, `Nintendo
  Promos`, …), and the catalog fetch only ever asked the 23 price-tracked
  sets. Fixed by the catalog-completeness plan (2026-07-29): the catalog
  now covers all 217 groups on every ingest. This in-window gap is what
  crashed `NativeBacktest.run()` (`KeyError`), where the Python engine
  silently excludes uncataloged assets from
  kind-filtered strategies; fixed by tagging missing catalog rows kind
  "other" (commit `091b663`), which also correctly keeps them tradeable for
  cost-aware-reversion (no kind filter), with a differential regression test
  proving both engines agree on inclusion/exclusion bit-for-bit. Research
  conclusions are unchanged by construction (parity is bit-for-bit); what
  changed is the cost of producing them and that the C++ core has no Python
  dependency — the native path now releases the GIL (Plan 11); the callback
  bridge, which calls back into Python per bar, still keeps it held. Full
  findings in `docs/research-findings-2026-07.md` (Plan 10 section).
- Plan 11 complete on feat/parallel-walkforward (346 tests + 1 skipped
  pytest + 25 Catch2 tests): fold-parallel walk-forward — `run_walkforward(
  ..., workers=)` (`0`=auto `min(folds, cores)`, `1`=serial, `N`=N threads;
  library default `1`, CLI default `0`); each fold owns its own optuna
  study and `PreparedMarket` windows sliced from shared read-only frames
  loaded once, so nothing mutable crosses fold workers; the native engine
  genuinely releases the GIL during each fold's C++ run (bridged
  strategies, e.g. ml-ranker, still hold it for the whole per-bar loop).
  Real-data acceptance (`scripts/bench_walkforward.py`, sealed-accumulation,
  2024-03-01..2026-06-30, is=180/oos=60/trials=15): cpp serial vs cpp
  workers=auto is bit-for-bit identical (equity curve, summary, per-fold
  params) — PASS. Measured wall-clock: python serial 359.5s, cpp serial
  20.0s (18.0x), cpp workers=auto 20.5s (17.5x) — the 18x win vs status quo
  is almost entirely the native engine plus the per-run load/prep hoist,
  not threads; fold-level parallelism itself measured no gain on this
  workload (11 folds, 8 cores) because each fold's Python-side prep holds
  the GIL and, at 15 trials/fold, the GIL-released C++ region is too small
  a fraction of the work for Amdahl's law to pay out here. The acceptance
  property (provably correct, genuinely concurrent optuna studies, free to
  enable) is the substantive result, not a wall-clock win on this
  particular config. Full findings in `docs/research-findings-2026-07.md`
  (Plan 11 section).
- Rigor pack complete on feat/rigor-pack (369 tests + 1 skipped): `research/
  stats.py` — seeded stationary block bootstrap CIs, deflated Sharpe ratio,
  and White's Reality Check (one joint cross-correlation-preserving resample
  over the whole strategy zoo, not per-strategy); `pkmn evaluate` discovers
  every `wf-*` walkforward artifact plus the matching buy-and-hold benchmark
  and writes a `data/results/evaluate-<date>/` report + registry record.
  Real-data run (registry `20260719T145313Z-dd9f28`, 5 strategies vs
  `buy-and-hold-sealed-2024-03-01-2026-06-30`, 660 aligned days): every
  strategy's OOS total return point estimate is negative (-7.4% to -25.1%),
  every deflated Sharpe (probability the true Sharpe exceeds zero after
  selection correction) is far under even the 0.5 coin-flip point, let
  alone the conventional 0.95 confidence bar (0.000-0.010), and the joint
  Reality Check comes back p = 1.0000 — the observed best excess return sat
  at or below every one of 10,000 luck-only (recentered) resample maxima,
  i.e. the best candidate underperforms even a typical best-of-five luck
  draw. Sharpens the Plan 9 conclusion: mixed cost
  regimes across the five artifacts (sealed-accumulation and ml-ranker are
  impact-on re-runs, the other three flat-cost) mean the comparison isn't
  perfectly apples-to-apples, but nothing here is close enough to positive
  for that caveat to matter. Full findings in `docs/research-
  findings-2026-07.md` (Rigor pack section).
- ml-ranker-v2 complete on feat/ml-ranker-v2 (388 tests + 1 skipped):
  `strategies/ml_ranker_v2.py` — friction/momentum-shape/cross-sectional-
  rank features (`research/features.py` `FEATURE_COLS_V2`), training labels
  net of per-row round-trip cost, in-loop purged validation
  (`research/purged.py`: embargoed most-recent-dates split, fixed
  `(max_iter, learning_rate)` grid, `early_stopping=False` throughout — the
  sklearn auto-split is random and leaks under correlated labels); v1
  (`ml_ranker.py`) frozen as the ablation baseline. One declared walkforward
  (registry `20260720T033422Z-858699`): stitched OOS total return -14.72%,
  worse than v1's impact-on -7.52%; overfitting gap (IS CAGR mean - OOS CAGR
  mean) is essentially zero and slightly negative (-0.58 CAGR-pts, vs v1's
  +0.33), and this time IS is honestly negative too (-7.61% vs OOS -7.02%)
  rather than the regime-wide-collapse artifact that made v1's small gap
  misleading — the net-of-cost labels and the unconditional
  `early_stopping=False` appear to have removed in-sample self-deception,
  but the resulting model still carries no positive edge. Mechanism caveat:
  the in-loop grid selection was inert in this run — at the per-fold horizons
  (>= 23d) an OOS rebalance yields too few strided validation dates to clear
  `min_val_dates`, so `select_config` fell back to `grid[0]` (max_iter=100,
  lr=0.1) at every OOS rebalance; the measured result ablates the features +
  net labels + early-stopping closure at that fixed config, and exercising
  the selection at real scale is a Plan B item. Full-zoo `pkmn evaluate` now covers six strategies
  (registry `20260720T034508Z-241dd9`): joint White's Reality Check
  unchanged at p = 1.0000; ml-ranker-v2's deflated Sharpe (0.017) is the
  highest of the six but still far below the 0.5 coin-flip point. Negative/
  null result, reported plainly. Full findings in
  `docs/research-findings-2026-07.md` (ml-ranker-v2 section).
- Web explorer complete on feat/web-explorer (401 tests + 1 skipped Python;
  8 web tests via `npm run check`): a read-only FastAPI + React/TS research
  explorer — `src/pkmn_quant/api/` (5 endpoints: runs list/detail,
  walkforward detail, evaluate, strategies) serving Pydantic-typed JSON, and
  `web/` (3 screens: runs browser, walk-forward detail with fold table/
  equity curve/rigor CI, cross-strategy rigor compare). The committed
  `web/tests/fixtures/*.json` are the cross-language contract: `tests/api/
  test_contract.py` validates each fixture against its Pydantic response
  model (`RunSummary`/`WalkForwardResponse`/`EvaluateResponse`), so the
  Python and TypeScript sides cannot silently drift. `make web` runs the API
  and the web dev server together for one-command local dev; CI gained a
  second `web` job (Node 20, `npm ci && npm run check && npm run build`)
  alongside the existing `checks` job. This is a viewer only — no run-
  triggering yet (planned Plan 2), no data writes, no strategy claimed to
  beat buy-and-hold.
- Plan 2b complete on feat/parameterized-backtest (438 tests + 1 skipped;
  a fresh `uv run pytest` shows 438 passed, 1 skipped): `pkmn backtest` is
  no longer buy-and-hold-only. `research/registry.py` strategy
  hyperparameters are now declarative `ParamSpec(name, kind, default, low,
  high, log)` tuples per `RegistryEntry`, with the optuna search space
  derived from them (`RegistryEntry.space` is now a property backed by
  `_space_from_params`); the six hand-written `_*_space` functions are gone,
  and `tests/research/test_space_reproducibility.py` is a frozen-oracle
  guard proving the derived space is bit-identical to them, so past
  walk-forward runs stay reproducible. New `research/backtest_run.py`
  (`run_single_backtest`) is the parameterized single-strategy capability
  the CLI (and a future web trigger) both call: native engine only,
  validates/coerces/default-fills params against the strategy's
  `ParamSpec`s, validates any starting holdings (must be in the backtest
  universe and have a mark on the window's first trading day), writes
  `equity.parquet`/`fills.parquet` to `data/results/<run_id>/`, and records
  a registry entry under that same run id (`research/runs.py` gained
  `new_run_id()` plus a `record_run(..., run_id=)` parameter for this).
  `pkmn backtest` gained `--strategy`, repeatable `--param k=v`,
  `--holdings <csv>`, `--kind sealed|single`, and `--cash`; `--engine` is
  removed from `pkmn backtest` (native engine only) but unchanged on
  `pkmn walkforward`. Real-data smoke test (`pkmn backtest --strategy
  sealed-accumulation --param take_profit=2.0 --start 2026-05-01 --end
  2026-06-30` against the live 874-day warehouse): 3 fills, total_return
  -1.66%, run recorded and visible in `pkmn runs list`. No new research
  finding — this is capability work, not a new walk-forward result.
- Plan 2c complete on feat/web-trigger-jobs (493 tests + 1 skipped Python —
  a fresh `uv sync --group api && uv run pytest` shows 493 passed, 1 skipped
  — plus 5 dashboard tests via `uv run --group dashboard pytest
  tests/test_dashboard.py` and 26 web tests via `npm run check`; note the
  `api` group is required, as CI does, or `tests/api` fails to collect):
  the web explorer can trigger runs, not
  just view them. `api/jobs.py`'s single-worker `JobManager` (injected
  `Executor`, in-memory `Job` state) backs `POST /api/backtest` (202 + job
  id), `GET /api/jobs`, and `GET /api/jobs/{job_id}`; every triggered run is
  paired with an identical-starting-state buy-and-hold benchmark
  (`research/backtest_run.py`'s new `run_with_benchmark`/`PairedRunResult`)
  tagged `config["benchmark_for"] = <primary run_id>` — the only linkage.
  `GET /api/backtest/{run_id}` and `/fills` serve the paired detail (fills
  carry a `seeded` flag for exits on starting holdings); `/api/strategies`
  now carries ParamSpecs plus `buy-and-hold`, `/api/runs` gained
  `include_benchmarks` (default off, hides paired benchmarks), and new
  `/api/products` does case-insensitive name search against a
  process-cached index — 11 endpoints total, up from 5. `web/` gained
  three screens (`/new` self-generating param form + holdings editor,
  `/jobs` self-stopping polling table, `/backtest/:runId` detail with the
  benchmark overlaid on the equity chart and seeded-exit markers) for 6
  total. Dashboard fix: `benchmark_runs()` is now registry-driven (filters
  `command == "backtest"` and `strategy.startswith("buy-and-hold")`, and
  skips records tagged `benchmark_for` — the same rule `/api/runs` applies)
  instead of directory-scanning, so triggered runs and their paired
  benchmarks no longer clutter the overlay selector with opaque ids.
  Starting holdings are guarded against an `opened_on` after the window
  start (`research/backtest_run.py`'s `validate_opened_on`, re-checked
  synchronously in the API for a 400, and defaulted to the form's start date
  in the web holdings editor): the five duration-gated strategies compute
  `(ctx.today - pos.opened_on).days`, which goes negative for a future
  `opened_on`, so the age/rebalance gate never fires and the run silently
  trades nothing while still reporting success.
  Real-data end-to-end check (server against the live 874-day warehouse):
  `GET /api/products?q=surging` took 0.42s cold (first DuckDB pass over the
  full warehouse to build the process-cached index) and 0.003s warm on a
  second query — comfortably under the multi-second concern threshold, but
  the cold cost is real and worth knowing about before judging first-query
  latency. `POST /api/backtest` (sealed-accumulation, 2026-05-01..2026-06-30,
  $5,000 cash) reached `succeeded` in ~1.8s with `run_id
  20260729T035305Z-e61086` and `benchmark_run_id 20260729T035306Z-b67712`;
  `GET /api/backtest/<run_id>` returned a 61-point equity curve on both the
  primary and the benchmark (3 fills, total_return -1.55%). Honest caveats:
  job state is in-memory (a restart loses the job list, never the results,
  already durable in the run registry); the product index is cached per
  process, so a fresh ingest is invisible until the server restarts;
  holdings that fail universe/mark validation surface as a failed job
  rather than a synchronous 400; and `/api/products` inner-joins `products`
  to `prices`, so a product_id with price history but no catalog row is
  silently absent from search even though a backtest will still trade it.
  At the time of Plan 2c that hid a large slice of the universe (the Plan 10
  figure, re-measured 2026-07-29 at 2,017 of 6,665 warehouse-wide) and was
  scheduled as follow-up work. **The catalog-completeness branch below closed
  it**: the all-groups refresh took uncataloged priced products from 2,017 to
  8 of 6,665 (99.6% of the gap), so the inner join now hides 8 products.
  The join is still an inner join — a defensible ~0.1% blind spot, no longer
  pending work.
- Catalog completeness complete on feat/catalog-completeness (502 tests +
  1 skipped Python via a bare `uv run pytest`; the skip is
  `tests/test_dashboard.py`, which needs `streamlit` — run
  `uv run --group dashboard pytest tests/test_dashboard.py` separately for
  it, 5 passed; 26 web tests via `cd web && npm run check`): the product
  catalog now covers all 217 tcgcsv groups on every `pkmn ingest` (previously
  23, the price-tracked ones only), fixing the drift where TCGplayer
  re-files products between groups and priced products silently lose their
  `rarity`/`kind`; new `pkmn refresh-products` repairs the catalog from all
  217 groups without a price re-ingest. Real-warehouse refresh (2026-07-29):
  UNCATALOGED 2,017 → 8, `single` 4,077 → 5,383 (+1,306), `sealed` 436 → 953
  (+517), `excluded` 135 → 321 (+186); catalog rows 4,673 → 32,506; total
  priced universe invariant at 6,665 (reclassified, not a different
  population); 0 products lost. **The correction is almost entirely
  prospective, not retrospective**: of the 4,687 products priced on or
  before the standard 2024-03-01..2026-06-30 research window's end, only 32
  changed `kind` (all to `single`, none to `sealed`) — the other ~1,978
  reclassified products only started pricing in July 2026, after that
  window. Full walk-forward re-run of all six strategies plus the
  buy-and-hold benchmark over that same window, split by mechanism: the two
  hard `kind`-filtered sealed-universe strategies (buy-and-hold,
  sealed-accumulation) are bit-for-bit unchanged (0 new sealed products
  in-window); cost-aware-reversion is provably invariant (it never reads
  `ctx.products`); the two hard `kind`-filtered single-universe strategies
  (dip-buyer, xs-momentum) move small and bounded amounts confirmed by a
  clean same-cost-regime A/B (+0.62 pts, +1.24 pts); the two ML strategies
  (ml-ranker, ml-ranker-v2), which have no hard `kind` filter and instead
  feed `kind`/`released_on` into features that were `NaN` for the 32
  affected rows before the fix, move by +2.47 pts and −8.43 pts respectively
  — large, opposite-signed, and confirmed deterministic by an independent
  cross-process re-run reproducing each result to the full float. `pkmn
  evaluate` before vs after: joint White's Reality Check p = 1.0000 both
  times (unchanged); every deflated Sharpe stays far below the 0.5
  coin-flip point both times. **No research conclusion changes: nothing
  beats buy-and-hold sealed** (+183.67% impact-on this window, bit-identical
  before and after the fix) — the catalog fix is capability and
  data-quality work, not a new research result, and the ML strategies'
  large swing from a 0.7%-of-universe metadata correction is evidence of
  their fragility, not of an edge. Full findings in
  `docs/research-findings-2026-07.md` (Catalog completeness section).
- Final-review fix on the same branch (`uv run pytest` observed at 514
  passed with the dashboard group installed — the same suite reads
  509 passed + 1 skipped in a venv without it):
  refreshing the catalog on **every** ingest made a partial fetch dangerous,
  because `refresh_products` overwrote `products.parquet` with whatever it
  managed to fetch. That fired for real on the first unattended run under the
  new code — one `[Errno 54] Connection reset by peer` on group 2534
  (`data/results/daily.log`) silently dropped that group and the 09:00
  `pkmn daily` job still reported success, leaving the live catalog at
  32,218 rows / 213 groups. It then happened **again the next morning**
  (2026-07-31 09:43, still on the pre-fix code since this fix was not yet
  committed): a reset on group 1404 dropped that group while the successful
  re-fetch of 2534 restored it, leaving 32,410 rows / 213 groups — two
  silent losses in 48 hours, which is the frequency argument for the fix.
  Both were harmless only by luck (neither group holds priced products, so
  warehouse-wide `UNCATALOGED` measured 8 of 6,666 after the second one);
  the same reset on group 2374 (1,244 products, many priced) would have
  quietly regressed the priced universe toward the exact pre-fix state.
  Fixed by **merging** the fetch into the
  stored catalog instead of replacing it (fresh rows win on `product_id`, so
  upstream updates still land, while a group that failed this run keeps its
  previous rows), plus a **completeness floor**: a fetch holding less than
  `CATALOG_FLOOR` (90%) of the rows already on disk raises instead of
  writing. Two adjacent holes closed at the same time: the per-group
  exception filter now catches `(httpx.HTTPError, ValueError, KeyError)` —
  a 200 carrying HTML (`json.JSONDecodeError`) or a payload with no
  `results` used to abort the whole ingest before one price row landed —
  and failures are now reported, not just warned to stderr:
  `refresh_products` returns `CatalogRefresh(rows, failed_groups)`,
  `ingest_range` returns `IngestReport(days, catalog_failures)`,
  `pkmn refresh-products` prints the failed-group count, and `pkmn daily`
  records `catalog_failures` in `daily.json` and sends a "catalog degraded"
  notification (the run still succeeds — merging means nothing was lost).
  The live catalog is deliberately left as the unattended runs left it
  (32,410 rows / 213 groups, group 1404 missing, as of 2026-07-31); repairing
  it is scheduled as a first real-data exercise of this code. Note the fix
  only starts protecting the 09:00 job once it is committed and installed —
  until then every unattended run is another coin flip.
- Full set coverage complete on feat/full-set-coverage (523 passed +
  1 skipped Python via a bare `uv run pytest` after `uv sync --group api`;
  the skip is `tests/test_dashboard.py`, which needs `streamlit` — run
  `uv run --group dashboard pytest tests/test_dashboard.py` separately for
  it, 5 passed, i.e. 528 with the dashboard group installed; 26 web tests
  via `cd web && npm run check` plus a clean `npm run build`): **prices now
  cover all 217 tcgcsv groups, not 23.** `MIN_SET_RELEASE` is deleted and
  `tracked_groups` has no date bound in either direction;
  `Paths(root, warehouse_name=)` supports a shadow warehouse;
  `scripts/rebuild_warehouse.py` rebuilds the whole history from the cached
  raw archives (17m08s for 907 days, resumable) and `scripts/
  verify_rebuild.py` gates the swap on every pre-existing product
  reproducing exactly — prices, quarantine decisions, **and** catalog
  membership (the catalog check was added in the final review: a shadow's
  catalog is one fresh fetch with nothing to merge into and no
  `CATALOG_FLOOR` to refuse it, so a single reset connection could otherwise
  ship a catalog smaller than the live one and pass the gate). Measured at
  swap: 3,197,600 → 36,337,465 price rows
  (**11.36x**) and 6,671 → 31,712 priced products (**4.75x**) over the same
  907 days; verification PASSED first try, so **the change is purely
  additive** — no pre-existing row moved. Priced universe by kind: 28,175
  `single`, 2,439 `sealed`, 986 `excluded`, 112 `null` (priced, no catalog
  row; engine tags them kind `other`). The supported reading of that 112 is
  **delisted upstream** — all of them stopped pricing before the warehouse's
  last day — but **that reading was checked for consistency, not proven**,
  and no fix is proposed either way. Full re-run of all six
  strategies plus the benchmark, impact-on both sides, same window
  (2024-03-01..2026-06-30): buy-and-hold sealed **+183.67% → +10.53%**;
  sealed-accumulation −7.36% → −8.59%; xs-momentum −9.30% → −38.72%;
  dip-buyer −21.15% → −99.80%; cost-aware-reversion −22.45% → −98.70%;
  ml-ranker −5.05% → −81.64%; ml-ranker-v2 −23.15% → −99.81%. `pkmn
  evaluate` (`20260804T204825Z-fa07b8`, each era against its own benchmark):
  joint White's Reality Check **p = 1.0000 pre-swap → p = 0.8038 post-swap**
  — not "unchanged"; the post-swap best sat at or below 8,038 of 10,000
  luck-only resample maxima rather than all 10,000. Both are far from the
  p <= 0.05 bar, so the conclusion holds, but the extreme phrasing does not.
  (The first post-swap run, `20260804T184401Z-cf292c`, published p = 1.0000
  against a **stale pre-swap benchmark** — see the Gotchas entry; the error
  made the test harder, never easier, so nothing was flattered.) Every
  deflated Sharpe is still far below the 0.5 coin-flip point (highest 0.254,
  ml-ranker, whose 95% CI is [−99.58%, +331.57%] — wide, not good); all six
  DSR values are identical to the full float across the stale and corrected
  runs, and `deflated_sharpe` takes no benchmark argument — but the aligned
  date set is seeded from the benchmark curve, so that independence holds
  here because both benchmarks cover the same 852 days, not
  unconditionally. **The benchmark's movement and two
  of the four wipeouts were traced; the two ML wipeouts were not.** The
  benchmark's fall is a capital-deployment artifact: `BuyAndHold`
  equal-weights with integer quantities, day-1 sealed assets went 45 →
  1,315, per-asset budget went $222.22 → $7.60, and deployed capital went
  $6,012.55 (60.1%) → $466.78 (4.7%) — the run is 95% cash. **The old bar
  was not fully deployed either** (37% cash): on the slice actually at risk
  the two runs returned +291.7% and +191.1% (gain / all-in capital consumed,
  i.e. $18,367.25/$6,296.27 and $1,052.73/$550.77 — the denominator is the
  all-in figure incl. fees and impact, not the $6,012.55/$466.78 notional),
  so **~85%** of the 17x headline collapse is the sizing rule, not the
  assets. That 85% is the log
  (Törnqvist) share of `R = deployment x invested-slice return`; Shapley
  gives 80.1% and order-dependent sequential decompositions span 63.4-96.8%,
  so deployment dominates on every basis but the earlier "~90%" sat above
  three of the four. Derivation in the findings doc. The dilution reading is
  **falsified**: over the same window the newly-added sealed products
  returned a median +212.3% vs +170.8% for the pre-existing ones — but note
  the basis, which is narrower than it sounds: that compares only the 1,133
  of 2,389 in-window sealed assets that priced across the **whole** window
  (a survivorship-conditioned sample; the statistic is not a claim about the
  1,256 assets that started or stopped pricing mid-window). It is the right
  population for the dilution question specifically, because the benchmark
  only ever bought day-1-priced assets and held them. The
  near-total losses of dip-buyer and cost-aware-reversion are adversarial
  selection into inverted quotes: 92-97% of their buy-side impact comes from
  fills where `market < low` (the market print sits below the lowest
  listing), where the impact model's market→mid walk correctly charges a
  multiple of notional — buy impact reached 194% and 225% of buy notional,
  $35,533 of frictions on a $10,000 account for dip-buyer. Both selectors
  maximize over exactly the quantity an inverted quote corrupts, and the
  wide universe supplies 6x more such rows with a 12x fatter tail. **Those
  impact/friction figures come from fixed-parameter diagnostic probes**
  (fold-1 params over the full window), not from the walk-forward runs whose
  returns are listed above — they reproduce the loss ordering, not the exact
  numbers. **ml-ranker (−81.64%) and ml-ranker-v2 (−99.81%) were NOT
  diagnosed** — no probe was run for them (75-140 min each through the
  Python bridge), so do not cite a mechanism for those two numbers; see the
  findings doc's three explicitly-open questions (untraced ML strategies;
  whether `market < low` is a tcgcsv defect or a real thin print; what a
  liquidity floor would do). **Honest conclusion: nothing beats
  buy-and-hold sealed — the standing result holds — but the bar is now
  95% cash, so it means much less.** No new strategy result; what shipped is
  a verified data foundation plus two surfaced defects (benchmark sizing,
  and the impact model's "market is the front of the book" premise). Next
  work, in order: a liquidity/quote-sanity layer, then a benchmark sizing
  fix; until both land no wide-universe cross-strategy comparison is a
  research result. Full findings in `docs/research-findings-2026-07.md`
  (Full set coverage section) — that section supersedes **every** earlier
  section in the file, all of which were computed on 23 groups.
- Instrument repair + horizon-matched evaluation complete on
  feat/instrument-repair-horizon (`uv sync --group api && uv run pytest`
  reads **583 passed + 1 skipped** without the dashboard group and **588
  passed** once it is installed — the skip is `tests/test_dashboard.py`'s 5
  tests, which also pass standalone via `uv run --group dashboard pytest
  tests/test_dashboard.py`; ruff clean, mypy clean over 57 source files;
  26 web tests + a clean `npm run build`; 37 Catch2 tests via ctest — all
  four counts superseded by the final-review fix wave in the next bullet).
  Two broken measurement instruments repaired and one
  missing measurement added, all on the same 217-group, 908-day warehouse and
  the same 2024-03-01..2026-06-30 window, impact-on. **(1) The benchmark is
  now actually invested.** `buy_and_hold` gained a liquidity screen
  (`research/screen.py`: print density desc, relative spread `(mid-low)/mid`
  asc, then `product_id`/`sub_type`) plus an equal-dollar allocation and a
  deficit sweep, C++-ported bit-for-bit: **4.7% → 74.2% of the account in
  product, 5.5% → 99.9% all-in** (never mix the two bases)
  ($7,423.07 notional + $38.00 fees + $2,530.36 impact = $9,991.43 of
  $10,000), total return **+10.53% → +24.93%** (run
  `20260807T135424Z-8362bc`, 38 fills, Sharpe 0.63, max drawdown −25.73%;
  the figure carries an implicit `warmup_days=0` — see Conventions).
  **(2) Buys price at `max(market, low)`.** tcgcsv's `market` is
  sales-derived and `low` is the cheapest current listing, so `market < low`
  is ordinary (2.19M in-window rows) and the old model priced fills at a
  number nothing was offered at; this **closes findings-doc open question 2**
  — the answer was neither "data defect" nor "thin print" but a category
  error in the consumer. **(3) `pkmn horizon <run-id>`** (`research/
  horizon.py`) pairs a backtest's fills into FIFO round trips and scores each
  against holding the sealed index over that trip's **own** entry/exit dates.
  Full zoo re-run, all six strategies, before → after (before = the Full set
  coverage figures, same universe/window/cost regime; the buy floor is the
  only instrument difference for the strategy rows): sealed-accumulation
  −8.59% → −10.31%; xs-momentum −38.72% → −54.46%; ml-ranker −81.64% →
  −84.48%; ml-ranker-v2 −99.81% → −99.43%; cost-aware-reversion −98.70% →
  −99.98%; dip-buyer −99.80% → −100.00%. **Five of six got worse and that is
  the instrument working, not a regression** — they now pay the cheapest
  listing that actually existed (the design spec predicted this in writing
  and committed to not treating it as failure); ml-ranker-v2's small
  improvement is noise at that magnitude. `pkmn evaluate`
  (`20260809T013934Z-a453f5`, 660 aligned days, against the repaired
  benchmark): joint White's Reality Check **p = 1.0000**, every deflated
  Sharpe far below the 0.5 coin-flip point — the move from the prior
  branch's 0.8038 is the bar getting harder, not the strategies getting
  worse. `parity_full.py` PASSes bit-for-bit on all five native strategies
  after both repairs (the ml-ranker bridge case did **not** appear in that
  run's output and is not claimed re-verified). **Horizon result, the number
  this branch exists to produce: over the owner's target 1-3 month band,
  none of the six beats holding sealed, and not narrowly.** Median 1-3m edge
  per trade: cost-aware-reversion −16.57% (41 trips), ml-ranker −37.96%
  (109), xs-momentum −45.33% (99), dip-buyer −51.21% (116), ml-ranker-v2
  −84.32% (20, and it beat the index on **0 of 20** trades). **The one
  positive 1-3m figure is sealed-accumulation's +13.90% over exactly ONE
  round trip**, against its own all-trip median of −10.98% — never quote it
  without the sample size; same for xs-momentum's 3-6m +55.40% (5 trips) and
  sealed-accumulation's 3-6m +6.91% (8). Method caveat that changes what
  these mean: **walk-forward artifacts carry no `fills.parquet`**, so horizon
  ran on **default-parameter backtests** over the same window, not the
  walk-forward parameter sets. Honest conclusion: this branch delivered a bar
  worth measuring against, fills priced at something you could transact at,
  and a per-trade measurement matched to the holding period that matters —
  and **no strategy**. Full findings in `docs/research-findings-2026-07.md`
  (Instrument repair section), which supersedes the Full set coverage
  section's benchmark figure and every strategy number in it.
- **Final-review fix wave on the same branch (2026-08-09): every benchmark
  and horizon figure in the bullet above is superseded and pending a
  re-run.** `uv run pytest` now reads **611 passed** with the dashboard and
  api groups installed; 40 Catch2 tests; 35 parity tests still bit-for-bit;
  26 web tests. Six behaviour changes, all measurement-instrument repairs,
  none of them a new research result:
  (1) the deficit sweep had **no per-asset bound** — `deficit = target -
  qty*price` saturates for an asset too expensive to hold one unit of, so
  the most expensive product always won it, putting **50.05% of the
  account** into one box (91594) that had not printed for 19 months; an
  affordability filter now keeps the largest k whose k cheapest members each
  cost <= `cash / k` and re-targets over those (measured on the real first
  bar: 99.9% deployed at a 1.95% largest weight, against 99.9% at 35%
  before, and against 84.2% deployed if the narrower per-candidate guard is
  used instead).
  (2) the screen's spread tiebreaker ranked `low == mid` — one listing, no
  depth — as the **tightest** quote, which is how 91594 got to the top;
  non-positive relative spreads now sort last.
  (3) the liquidity tier for a buy is selected from the **floored base**, not
  the print (346,378 in-window rows, 15.8% of the inverted ones, fall in a
  different tier under `low`); the sell side keeps the print, having no
  floor.
  (4) `pkmn evaluate`'s benchmark glob is deleted — see the bullet below.
  (5) `pkmn horizon` scores a still-open trip **net of a modelled exit**;
  gross scoring flattered open trips by ~13 points and hit
  sealed-accumulation and cost-aware-reversion hardest.
  (6) `pkmn signals` reports an executable price and outlay, not a notional
  at the sales print (it would print $71.88 where the model says $1,800).
  Items 1, 2, 3 and 5 all move published numbers; nothing here changes a
  conclusion, because every strategy figure moves against a bar that moves
  too.
- **Plan A (IEX order book) complete on feat/iex-book (615 passed pytest via
  `uv sync --group api --group dashboard && uv run pytest`; 130 Catch2 tests
  via ctest on macOS and 130 in the OrbStack VM; ruff, format and mypy clean
  over 58 source files).** Plan A of 4 in the market-data / CLOB track (spec:
  `docs/superpowers/specs/2026-10-22-market-data-and-clob-design.md`, plan `docs/superpowers/plans/2026-10-22-iex-book.md`).
  **This branch was cut from `feat/instrument-repair-horizon`, not main, by
  owner decision, because that branch is not yet merged; merging this one
  carries those commits with it.** Shipped, all C++20 under `cpp/` with no
  Python dependency: a price-time `OrderBook` (`cpp/src/pkmn_book`), a
  streaming pcapng-over-zlib reader, IEX-TP framing with sequence tracking,
  DEEP+ and DEEP decoders and the TOPS Quote decoder, a per-symbol `BookSet`
  (`cpp/src/pkmn_md`), tools
  `iex_decode`, `pcap_count`, `iex_oracle`, `iex_bench` (`cpp/tools`), the
  HIST downloader `scripts/iex_hist.py`, and `scripts/bench_iex.sh`. Plans B-D
  are NOT STARTED. **Oracle result, stated plainly: the spec's success
  criterion ("matches at every checked point on at least one full real day")
  is NOT met on 2024-12-23.** `iex_oracle` rebuilds the book from DEEP+ and
  checks it against DEEP price levels and TOPS best quotes: 96,980,017
  checkpoints evaluated, 5,472 halt zero-quotes excluded by name
  (`tops_unavailable_zero_quote`), 96,974,545 checked, of which 7,870
  mismatch (0.008%) in 8 symbols. Every validity precondition held, so the
  run exits 1 (exit codes: 0 pass, 1 mismatch, 3 inconclusive when a
  precondition such as gaps, `other_stream` or zero TOPS checks fails).
  **Coverage: 2024-12-23 is an incident day, not a full session.** Every
  symbol is operationally halted at 10:22:29 ET, all three feeds restart at
  11:32:50 ET, DEEP+ carries 80,992 messages after the restart against
  50,878,184 before, and the last book event is at 13:45:40 ET against a
  16:00 ET close. Even a zero-mismatch run on these files would arguably not
  meet the "full real day" criterion; the second oracle day should be a
  normal session (no incident, no mid-day restart, not a DST boundary).
  7,819 of the 7,870 trace to an undocumented DEEP+ order-id rewind starting
  at 09:48:38 ET (926,703 Adds below their symbol's running maximum id across
  901 symbols; only 5 symbols mismatch: DRIP, OTEX, CNH, KBE, WMT). The other
  51 are round-lot (NVR, MKL, SEB), which vanish only with the opt-in
  `--round-lot NVR=10 --round-lot MKL=10 --round-lot SEB=1`; those values
  were inferred from TOPS itself, which is why the option is not the
  default. The book applies DEEP+ correctly (an independent Python replay
  agreed message for message); what is open is a second oracle day, asking
  IEX, or a named rule approved by the owner, and none has been taken. **Mid-day
  restart finding:** all three feeds restart together at send_time
  1734971570036197067 (11:32:50 ET), data resumes 11:39 ET, so the day is
  not one continuous stream; the oracle resolves pending checks and clears
  every book at the restart. Full detail: `docs/iex-oracle.md`. **Benchmark**
  (`docs/benchmarks/iex-book.md`; DEEP+ decode + `BookSet::apply` on the
  2024-12-23 DPLS file, 50,959,176 messages, so measured on that incident
  day's message mix, not a normal session's; Apple M1 Pro, 7-vCPU OrbStack
  Ubuntu 24.04 arm64 VM, g++ 13.3.0, `-O3 -DNDEBUG -ffp-contract=off`, timer
  cntvct_el0 at 24 MHz = 41.7 ns per tick; empty read pair 9.5-11.9 ns mean across the three runs, 10.5 ns in the run the percentiles come from).
  The Task 6 optimization (pooled order arena, open-addressing id map,
  contiguous levels, commit bc6e805) was kept on an interleaved A/B of 9
  pairs: best-of-9 ram throughput 4,926,764 -> 7,584,533 msgs/s, median
  timed-region mean 184.3 -> 121.5 ns, B faster in all 9 pairs. **VM noise is
  10% or more**: within-session spread was 32.4% (A) and 46.8% (B) on msgs/s,
  and median msgs/s moved only +27.5%, inside those spreads; the keep rests
  on the pairing and the non-overlapping timed-mean ranges, not on a clean
  margin. The clean generated run at bc6e805 reads 7,342,902 msgs/s best of
  3 (spread 13.5%), latency p50 83 ns (2 ticks, **quantized: +/-50%**), p99
  626 ns, p99.9 6,448 ns, max 40,436,760 ns (the VM being descheduled is the
  likely cause, not verified), and 2,497,490 msgs/s end to end including
  gunzip. Tail (p99.9, max) did not move in the A/B. Next profile target:
  `OrderBook::level_pos` at 22.7% self time. IEX data results carry the
  required citation: Data provided for free by IEX. By accessing or using IEX
  Historical Data, you agree to the IEX Historical Data Terms of Use
  (`docs/iex-hist-terms.md`).

- **Plan B (Pokemon order book) complete on feat/pokemon-book (668 passed
  pytest via `uv sync --group api --group dashboard && uv run pytest`; 29
  vitest via `cd web && npm run check`; 163 Catch2 tests via ctest, on macOS
  and in the OrbStack VM; counts after the final-review fix wave).** Plan B
  of 4 in the market-data / CLOB track. **Cut
  from `feat/instrument-repair-horizon`, not main (same owner ruling as Plan
  A); Plans C-D are NOT STARTED.** `pkmn backtest`, `pkmn walkforward` and
  `POST /api/backtest` now default to `--execution book`: fills run against
  daily ladders of **modelled resting orders (synthetic, built from the day's
  tcgcsv quote)**. tcgcsv publishes one quote per product per day, not an
  order book, so every resting order is a model, never market data. The
  library default (`NativeBacktest.execution`, C++ `CostModel::execution`)
  stays formula, and so do the live paths. `--execution formula` selects the
  walk-the-spread formula; `--no-impact` is flat cost under either mode and
  is recorded as formula. Shipped: `ladder.*` and `execution_book.*` in
  `cpp/src/pkmn_engine`, the ITCH-style wire protocol in `cpp/src/pkmn_wire`
  (`docs/pokemon-wire-protocol.md`), `pkmn wire encode`, the `pokemon_replay`
  tool, and the equivalence and benchmark scripts. **Equivalence (real
  warehouse, 2024-03-01..2026-06-30, 949-day warehouse):** the test-only
  exact ladder matches `buy_impact`/`sell_impact` on 34,092,324 rows and
  11,165,181,052 `(used, qty)` checks with max abs error 4.66e-10 dollars
  (tolerance 1e-6); on the five native strategies at default params the
  formula and exact-ladder runs agree on every fill (0 mismatches), and
  **zero fills fell on two-sided asset-days** (a statement about those
  strategies at default params, not about the change). The whole-cent default
  ladder moves per-run impact by cents to about $1.38 and leaves every fill
  count unchanged. **Identical books:** encoding the window to the wire and
  replaying it builds the same ladders as the backtester's in-memory path, 0
  mismatches over 1,315,694 asset-days (June 2026; the command, output, date
  and SHA are recorded in `docs/benchmarks/pokemon-book.md` section 7b).
  The pricing gate now also checks that each side's book depth equals the
  cost model's `max_daily_qty` (`depth_mismatches`, 0 on the same 34,092,324
  rows). **Costs:** encode of 949
  days, 38,250,316 messages, 890,723,220 bytes in 10.8 s (n=1); replay in
  the VM 1,472,031 msgs/s median with ladders built for every quote (decode
  only is far faster but its spread is 70.6%, page-cache dominated); backtest
  book vs formula is inside the run-to-run noise on a swapping 16 GB laptop;
  sealed-accumulation walk-forward 266.24 s book vs 266.33 s formula (n=1
  each). `parity_full.py` still PASSes bit-for-bit on the formula path. Full
  detail and raw JSON: `docs/benchmarks/pokemon-book.md`,
  `docs/benchmarks/pokemon-book-raw/`. **No research number was re-run: that
  is Plan D.** Every pre-Plan-B registry record reads as formula (a missing
  `config["execution"]` is formula). The benchmark and horizon figures from
  the instrument-repair final-review fix wave above are **still pending the
  owner's re-run**; nothing here supplies them. **That re-run is a formula
  baseline, and the user-facing defaults are now book, so it must say so
  explicitly:** `pkmn backtest ... --execution formula`, `pkmn walkforward
  ... --execution formula`, then `pkmn evaluate --execution formula`
  (`pkmn horizon` follows the backtest's recorded mode). Run without the
  flag, it silently produces book numbers that are not comparable to
  anything in the instrument-repair bullets. Every report, the evaluate and
  horizon stdout, the dashboard caption and the web explorer now state the
  mode, book with the synthetic label. Book evaluates write
  `data/results/evaluate-<date>-book/`; formula keeps `evaluate-<date>/`.
- **Formula baseline re-run done (2026-10-24/25), on code `3617c61` and
  catalog sha256 `4cf6c3a6...` (32,887 rows).** This is the re-run the
  final-review fix wave left pending; it supersedes every benchmark and
  horizon figure in the instrument-repair bullets. All `--execution formula`,
  impact on, 2024-03-01..2026-06-30. Bar: **+126.84%** (run
  `20261024T170908Z-99ee95`, 60 fills, Sharpe 3.50, max drawdown -14.27%,
  86.1% notional / 99.95% all-in, largest position 2.23%), bit-identical to
  August's `7c776b`. `pkmn evaluate` (`20261025T195117Z-04d2b6`,
  `data/results/evaluate-2026-10-25/`): Reality Check **p = 1.0000**, every
  DSR at or below 0.010; stitched OOS from -10.31% (sealed-accumulation) to
  -99.94% (ml-ranker-v2). Horizon, 1-3m median edge per trade: none beats
  holding sealed (sealed-accumulation -11.96% over 2 trips, cost-aware-
  reversion -22.66% over 35, ml-ranker -44.62% over 104, xs-momentum
  -49.00% over 159, dip-buyer -52.88% over 115). **ml-ranker-v2 has no
  horizon result**: its backtest was killed after a 21-hour swap thrash.
  **Catalog drift finding:** the native walk-forwards and three of five
  default backtests match August bit for bit; sealed-accumulation's backtest,
  ml-ranker's backtest and both ML walk-forwards do not. ML is deterministic
  (a same-catalog re-run of `74aaba` is bit-identical). The cause is
  `released_on` drifting upstream (104 values changed since 2026-08-03,
  102 of them to null), which sealed-accumulation's age gate and the ML age
  features read. Restoring those 104 dates in a scratch root reproduces
  August's sealed-accumulation backtest bit for bit; the same A/B on
  ml-ranker moves it (-99.813% to -99.780%) but does not reproduce August
  (-99.820%), so the ML mechanism is not isolated. No conclusion changes.
  Full write-up: findings doc, "Formula baseline" section.

- **Live ingest and phone alerts on feat/live-ingest (2026-10-25; 753
  passed pytest via `uv sync --group api --group dashboard && uv run
  pytest`).** Cut from `feat/instrument-repair-horizon` (owner ruling: the
  launchd job runs that checkout). Spec:
  `docs/superpowers/specs/2026-10-25-live-ingest-design.md`. tcgcsv took its
  price archive offline: every `/archive/...ppmd.7z` URL 403s, and `pkmn
  daily`'s archive ingest failed every day from 2026-09-14 on. The warehouse
  stops at 2026-09-13. `data/live.py` now captures each tcgcsv update from
  the live per-group `/prices` endpoints (raw responses kept under
  `data/raw/live/<day>/`, resumable, each group fetched at most once per
  update, only failed groups re-fetched) and finalizes it through the same
  `prices_frame` + `apply_quality_gates` path as an archive day. `pkmn daily`
  calls `run_live_ingest` instead of `ingest_range`, probes the archive at
  most once a local day (an "archive is back" alert if it returns 200), runs
  signals and paper fills only when the newest stored day differs from the
  last one they ran on for that mode (so four runs a day never re-notify or
  re-record fills), and applies a rate-limited alert policy
  (`live/alerts.py`, state in `data/results/daily-state.json`): a capture
  problem (capture incomplete, the update's day not stored, or the ingest
  or a day's write raised) alerts only on a run at or after 21:00 local or
  when the warehouse is stale (`STALE_AFTER` = 28h after 20:00 UTC on the
  newest stored day, i.e. 00:00 UTC two days later, judged after the run's
  ingest, so a morning run alerts while the missed update can still be
  captured); partial days, abandoned days and two updates on one UTC day
  alert; non-ingest failures alert once, then at most daily; each cleared
  condition sends one "recovered". A superseded day (an older captured day,
  or a day holding an earlier update of the same UTC date) is written only
  if it holds at least `LIVE_FLOOR` (90%) of its groups, counted as held /
  (held + failed + never attempted) from that day's own manifest, and at
  least one row. Otherwise it is **abandoned**: nothing is written, the raw
  files stay, the manifest gets an `abandoned` record, it is never retried,
  and `pkmn daily` alerts once (key `abandoned:<date>`). A capture run stops
  fetching after `MAX_CONSECUTIVE_FAILURES` (10) failed groups in a row; the
  untried groups have no manifest entry, count as failed, and are fetched on
  the next run. Days are written one at a time; the first write that raises
  stops the run and lands in the report's `finalize_errors`, which `pkmn
  daily` treats as an ingest error. The archive probe asks for the day after
  the newest archive-stored day and is skipped when that day is already
  stored (no gap left). Alerts go to a macOS banner plus an ntfy phone push
  (`live/notify.py`). `daily.json` gains `live` (null under
  `--skip-ingest`; it lists `abandoned` days) and `signals_skipped`. The launchd template runs at
  09:00, 13:00, 17:00 and 21:00. **The days 2026-09-14 to 2026-10-23 are
  lost**: the live endpoints serve only the current update, so nothing can
  recover them unless tcgcsv republishes the archive. **2026-10-24 and
  2026-10-25** were captured by an owner-approved throwaway stopgap script
  outside the repo (`capture_raw.py`) into `data/raw/live/<day>/`, in the
  same layout `data/live.py` reads, so the first real run finalizes them as
  ordinary days (2026-10-24's capture, 2026-10-25 16:33 UTC: 220 of 220
  groups held, 45,400 price rows, 5 held-empty). Stopgap manifests carry no
  `tracked` list, so their missing set is read from their own entries
  only. **Task 6, run with owner approval on 2026-10-26:** `pkmn
  live-ingest --root /Users/ml3787/pkmn-stock` finalized both stopgap days
  into the real warehouse, 2026-10-24 as 44,597 clean + 803 quarantined
  (= the manifest's 45,400) and 2026-10-25 as 44,602 + 797 (= 45,399).
  Every non-empty held group (215 of 220 each day) has product ids in the
  stored partitions and no held product id is missing; the check maps
  product ids back to each group's raw file, because the price partition
  has no `group_id`. The catalog refresh in that run left
  `products.parquet` at sha256 `4cf6c3a6...`, unchanged. ntfy is
  configured (`data/config/notify.toml`, gitignored) and `pkmn notify-test`
  reached the owner's phone. The launchd job is reinstalled with the four
  daily runs; a `launchctl kickstart` run finished `status: ok` (no new
  update to store, signals on 2026-10-25, 0 buys / 0 sells, archive probe
  recorded). The job first ran from the `pkmn-stock-live-ingest` worktree
  (with `--root /Users/ml3787/pkmn-stock`) and was repointed at the main
  checkout when this branch merged into `feat/instrument-repair-horizon` on
  2026-10-26.

- **Plan C (feed handler) complete on feat/feed-handler (753 passed pytest
  via `uv sync --group api --group dashboard && uv run pytest`; 204 Catch2
  tests via ctest in the OrbStack VM, all passing, and 204 on macOS with 5
  skipped because the loopback-multicast tool tests need the VM; ruff,
  format and mypy clean over 62 source files).** Plan C of 4 in the market-
  data / CLOB track (spec: `docs/superpowers/specs/2026-10-26-feed-handler-
  design.md`). **Cut from `main` (3ce6d42), unlike Plans A and B**; Plan D is
  NOT STARTED. No Python files changed. Shipped, all C++20 under `cpp/`
  outside the Python wheel: a canonical FNV-1a book digest
  (`pkmn_md/book_digest.*`, `OrderBook::for_each_order`, `BookSet::
  for_each_book`) and the reference tool `iex_digest`; a 12-byte LE framed
  TCP retransmit protocol (`recovery_proto.*`), an epoch-keyed retransmit
  ring (`retransmit_ring.*`) and a seeded random/burst drop injector
  (`drop_injector.hpp`); `RecoveringHandler` (`gap_recovery.*`, socket-free:
  epoch classification and parking, hold-and-request, tail requests, loud
  failure on an unserved or rejected retransmit or an end overshoot); the
  tools `feed_publisher` (replays the capture as UDP multicast, each IEX-TP
  payload byte for byte unchanged) and `feed_handler`, with `net_util.hpp`;
  mid-epoch checkpoint digests every 100,000 sequence numbers on both
  sides; `scripts/bench_feed.sh`; `docs/benchmarks/feed-handler.md` (raw JSON
  in `feed-handler-raw/`). Tasks 6 (burst run) and 7 (throughput sweep) both
  shipped; nothing was cut. **Real-day results, 2024-12-23 DEEP+ (42,492,551
  packets, 50,959,176 messages; an incident day, two sequence epochs), Apple
  M1 Pro, 7-vCPU OrbStack Ubuntu 24.04 arm64 VM, loopback multicast, one
  host:** 1% seeded random loss (commit 2c5ef85): 425,623 packets dropped,
  421,146 gaps recovered, 0 unrecoverable, 657,789 msgs/s end to end, held
  max 5,079 packets, all 508 mid-session checkpoints and both epoch digests
  equal to the direct-replay reference. Burst loss (100-packet bursts;
  commit 4c5a815): 20,189 bursts started, 2,018,900 packets dropped, 20,175
  gaps detected (adjacent bursts merge, zero-message packets open no gap),
  2,389,439 messages re-requested, 0 unrecoverable, 677,599 msgs/s, 508 of
  508 checkpoints match. Throughput sweep (payloads preloaded in RAM, no
  injected loss, n=1 per rate, five targets incl. unthrottled): digest match
  in all five; best 768,150 msgs/s unthrottled; the 8M-target run lost 3,399
  datagrams to receive-buffer overflow and recovered them with 1 gap. **The
  publisher, not the handler, was the limit** (it never reached 95% of any
  paced target), so 768,150 is a floor and the handler's ceiling was not
  found; VM noise is 10% or more. **Known limitations, stated plainly:**
  (1) tiny-epoch misclassification: rule 2 needs a new epoch's first seen seq
  to sit below the old epoch's highest seq, and a backed-up publisher TCP
  send buffer can delay EpochStart into that window; it fails loudly, never
  silently. (2) Retransmit only, no snapshot recovery. (3) Loopback in one
  VM, not a network: no NIC, no wire latency, loss injected by the
  publisher. (4) Checkpoints are samples every 100,000 messages; the
  80,992-message post-restart epoch has none and its books are compared only
  at its empty end (both epochs end with empty books on this day). (5) The
  reference shares the DEEP+ decoder and `OrderBook` with the handler, so a
  pass shows independence from transport and recovery, not from the book,
  and says nothing about matching IEX (the oracle question in
  `docs/iex-oracle.md` is still open). (6) Throughput is publisher-limited.
  Full detail: `docs/benchmarks/feed-handler.md`. IEX data results carry the
  required citation: Data provided for free by IEX. By accessing or using
  IEX Historical Data, you agree to the IEX Historical Data Terms of Use
  (`docs/iex-hist-terms.md`).

## Commands

```bash
uv sync                      # install (uv manages everything; never pip)
uv run pytest                # test suite
uv run ruff check . && uv run ruff format --check . && uv run mypy
                             # lint + format + strict typecheck (src/ plus the two
                             # operator scripts: rebuild_warehouse, verify_rebuild)
uv run pkmn ingest --start 2026-07-01 --end 2026-07-31   # archive backfill/rebuild path (archive offline since 2026-09-14: 403s)
uv run pkmn live-ingest                                  # capture the current tcgcsv update from the live endpoints once; exit 1 if incomplete
uv run pkmn notify-test                                  # send a test ntfy push (needs data/config/notify.toml)
uv run pkmn refresh-products                             # repair the catalog from all 217 groups, no price ingest
uv run python scripts/rebuild_warehouse.py --root .      # rebuild the warehouse into data/warehouse-full/ from cached archives (~17 min, resumable; leave --start at its default)
uv run python scripts/verify_rebuild.py --root .         # gate the swap: every pre-existing product must reproduce exactly, prices + quarantine + catalog (exit 0 = safe)
uv run pkmn backtest --start 2024-03-01 --end 2026-06-30 # buy-and-hold benchmark (default strategy; default --execution book)
# The formula baseline (done 2026-10-25) used --execution formula on backtest and
# walkforward, then `pkmn evaluate --execution formula`. Any re-run of it must too:
# without the flag every one of them defaults to book (see the Plan B bullet).
uv run pkmn backtest --start 2024-03-01 --end 2026-06-30 --execution formula   # formula baseline benchmark
uv run pkmn backtest --strategy sealed-accumulation --start 2024-03-01 \
    --end 2026-06-30 --param take_profit=2.0 --holdings holdings.csv   # any strategy + cash/holdings
uv run pkmn walkforward --strategy sealed-accumulation \
    --start 2024-03-01 --end 2026-06-30 --trials 15      # research run (cpp, fold-parallel auto)
uv run pkmn walkforward --strategy sealed-accumulation \
    --start 2024-03-01 --end 2026-06-30 --trials 15 --workers 1  # serial reference run
caffeinate -dimsu uv run pkmn walkforward --strategy ml-ranker \
    --start 2024-03-01 --end 2026-06-30 --trials 15 --workers 1  # ML: --workers 1 REQUIRED, -dimsu not -i
uv run pkmn signals --strategy sealed-accumulation       # live recommendations
uv run --group dashboard streamlit run app/dashboard.py  # results explorer
uv run pkmn portfolio show                               # real positions + P&L
uv run pkmn daily --skip-ingest                          # the loop, offline
uv run pkmn daily --skip-ingest --paper                  # paper mode dry-run
uv run pkmn runs list                                     # experiment registry: recorded runs
uv run pkmn runs show <run-id>                             # full record for one run
uv run pkmn evaluate                                     # cross-strategy rigor: CIs, DSR, Reality Check (default book)
uv run pkmn horizon <backtest-run-id>                     # per-trade edge vs holding sealed over each trip's OWN dates
uv run pkmn horizon <backtest-run-id> --benchmark <run-id> # pin the index run explicitly (registry ids, never a directory)
uv run pkmn backtest --start ... --end ... --no-impact    # flat-cost, skip market-impact model
uv run pkmn walkforward --strategy sealed-accumulation --start ... --end ... \
    --trials 15 --engine python --execution formula      # reference (slow) Python engine, walkforward only (python + book is rejected)
uv run pkmn walkforward ... --execution book|formula     # default book: modelled resting orders (synthetic, built from the day's tcgcsv quote)
uv run pkmn evaluate --execution formula                 # score formula walk-forwards (default book; one mode per run); REQUIRED for the formula baseline re-run
uv run pkmn wire encode --start 2024-02-08 --end 2026-09-13 --out data/wire/full.pkw  # warehouse -> ITCH-style quote file
cpp/build-linux-release/pokemon_replay data/wire/full.pkw [--no-ladders] [--repeat N]   # (in the VM) replay a wire file, rebuild each day's ladders
uv run python scripts/book_equivalence.py --start 2024-03-01 --end 2026-06-30   # book vs formula pricing, real data
uv run python scripts/bench_pokemon_book.py              # encode, backtest and walk-forward cost, book vs formula
scripts/bench_pokemon_replay.sh                          # replay throughput in the VM
cmake -S cpp -B cpp/build -DPKMN_BUILD_TESTS=ON && cmake --build cpp/build -j && ctest --test-dir cpp/build
                             # C++ unit tests (Catch2), independent of pytest
uv run python scripts/parity_full.py                      # full-data bit-for-bit acceptance, both engines
uv run --group viz python scripts/render_readme_chart.py   # regenerate README hero chart from local artifacts
make web                                                  # run the API + web dev server together
```

All four gates must pass before every commit. CI runs them with
`uv sync --frozen` (stale uv.lock fails the build — commit pyproject.toml and
uv.lock together).

```bash
# Plan A (IEX order book); the VM is the Linux build/benchmark host
orb -m ubuntu bash -lc 'cd /Users/ml3787/pkmn-stock && cmake -S cpp -B cpp/build-linux -DPKMN_BUILD_TESTS=ON && cmake --build cpp/build-linux -j && ctest --test-dir cpp/build-linux'
uv run python scripts/iex_hist.py --date 20241223 --feeds DPLS DEEP TOPS --dest data/iex   # download (gzip kept on disk)
orb -m ubuntu bash -lc 'cd /Users/ml3787/pkmn-stock && cmake -S cpp -B cpp/build-linux-release -DCMAKE_BUILD_TYPE=Release && cmake --build cpp/build-linux-release -j --target iex_oracle'   # builds the Release iex_oracle (bench_iex.sh builds only iex_bench)
cpp/build-linux-release/iex_oracle --deep-plus data/iex/20241223_IEXTP1_DPLS1.0.pcap.gz \
    --deep data/iex/20241223_IEXTP1_DEEP1.0.pcap.gz --tops data/iex/20241223_IEXTP1_TOPS1.6.pcap.gz  # exit 0 pass, 1 mismatch, 3 inconclusive (currently 1, see docs/iex-oracle.md)
scripts/bench_iex.sh [--profile] [--no-record]            # re-execs in the VM, prepends a section to docs/benchmarks/iex-book.md
```

```bash
# Plan C (feed handler); needs the VM (loopback multicast) and the 2024-12-23 DPLS capture in data/iex/
scripts/bench_feed.sh random|burst|sweep [--no-record]    # full-day publisher + handler run vs the iex_digest reference; prepends a section to docs/benchmarks/feed-handler.md
cpp/build-linux-release/iex_digest data/iex/20241223_IEXTP1_DPLS1.0.pcap.gz --checkpoint-every 100000 [--out FILE]   # (in the VM) reference digests by direct file replay
cpp/build-linux-release/feed_publisher data/iex/20241223_IEXTP1_DPLS1.0.pcap.gz --drop random:0.01 --seed 1 --rate 0   # (in the VM) UDP multicast replay + TCP retransmit server; --drop defaults to none; run a tool with no args for usage
cpp/build-linux-release/feed_handler --expect <reference.digests> --checkpoint-every 100000 --timeout-s 300 --rcvbuf 7500000   # (in the VM) receiver; start it, then the publisher
```

## Layout

- `src/pkmn_quant/data/` — tcgcsv client, transforms, quality gates, Parquet/
  DuckDB warehouse (`Warehouse.query()` gives SQL over `prices`/`products`).
- `src/pkmn_quant/engine/` — event-driven backtester: costs, portfolio, data
  view, execution, strategy ABC, metrics, backtest loop. T+1 fills, long-only.
  `quotes.py`: per-day `Quote` (mid/low) feeding the walk-the-spread market-
  impact cost model; engine default off, CLI default on, `--no-impact` opts
  out.
- `src/pkmn_quant/strategies/` — Strategy implementations: buy_and_hold,
  sealed_accumulation, dip_buyer, momentum, cost_aware_reversion, ml_ranker
  (HistGradientBoostingRegressor trained in-loop, stateless), ml_ranker_v2
  (friction/momentum-shape/cross-sectional-rank features, net-of-cost
  labels, in-loop purged validation via `research/purged.py`; v1 is frozen
  as the ablation baseline). All six active strategies are in
  PORTFOLIO_SAFE_STRATEGIES (support `--portfolio` exit signals against a
  real ledger and the paper daily loop).
- `src/pkmn_quant/research/` — walk-forward layer: folds, seeded optuna search,
  runner/stitcher, strategy registry, markdown report, `walkforward.json`
  artifacts (the research → live bridge). `features.py`: 8 leakage-bounded
  features for ml-ranker (scikit-learn), plus `FEATURE_COLS_V2`
  (friction/momentum-shape/cross-sectional-rank) and
  `build_training_frame_v2` (net-of-cost labels) for ml-ranker-v2;
  regression-tested. `purged.py`: embargoed, most-recent-dates validation
  split and deterministic fixed-grid model selection
  (`select_config`/`_make_model`, `early_stopping=False` always) for
  ml-ranker-v2's in-loop selection. `registry.py` strategy hyperparameters
  are declarative `ParamSpec(name, kind, default, low, high, log)` tuples —
  the single source for CLI/web param validation, the derived optuna search
  space (`RegistryEntry.space`), and (Plan 2b) the parameterized single-
  strategy backtest capability. `backtest_run.py`: `run_single_backtest` —
  the parameterized single-strategy backtest capability the CLI and the web
  trigger both call; native engine only. Plan 2c added `run_with_benchmark`
  beside it: runs the strategy, then buy-and-hold-sealed over the identical
  starting state (same window, cash, holdings, impact), tagging the
  benchmark's registry record `config["benchmark_for"] = <primary run_id>`
  (the only linkage; `benchmark=None` when the primary already is
  buy-and-hold-sealed). `runs.py`:
  experiment registry — every backtest/walkforward appends a record (run_id,
  config hash, git SHA+dirty, data fingerprint, results) to
  `data/runs/registry.jsonl`; `pkmn runs list`/`show` inspect it.
  `stats.py`: seeded bootstrap statistics (CIs, deflated Sharpe, Reality
  Check) feeding `pkmn evaluate` and walkforward reports.
  `screen.py`: `rank_assets(history, today)` — tradeability ranking (print
  density desc, then relative spread `(mid-low)/mid` asc, then
  `product_id`/`sub_type`), leakage-bounded by `today` and mirrored
  bit-for-bit by the C++ `BuyAndHold`. Used by the benchmark's allocation
  today; it is also the machinery the not-yet-run liquidity-floor experiment
  (findings-doc open question 3) needs, so keep it strategy-agnostic. Any
  change to the sort key or the undefined-spread rule must land on both
  language sides in the same commit. `horizon.py`: FIFO round-trip pairing
  of a backtest's `fills.parquet` plus per-trip edge against the sealed
  index over each trip's own entry/exit dates (`horizon_report`); Python-only
  by design — it reads artifacts after a run and is not on the hot path.
- `src/pkmn_quant/live/` — `pkmn signals`: one on_bar at the latest warehouse
  date using the last fold's params from the latest walk-forward artifact;
  markdown + JSON reports that carry the strategy's OOS record.
  `ledger.py`: append-only JSONL trade ledger replayed through the engine
  Portfolio; single source of truth, marks never stored. `notify.py`:
  osascript banners, argv-passing. `paper.py`: `plan_paper_fills` planner
  (extracted from cli.py) — pure function mapping signals + ledger state to
  recorded paper fills via the CostModel.
- `app/dashboard.py` — Streamlit results explorer (dependency group
  `dashboard`; not mypy'd, not imported by src/ or tests — demo tool only).
  Headless tests in `tests/test_dashboard.py`; run via
  `uv run --group dashboard pytest tests/test_dashboard.py` (skip without the group).
- `cpp/` — the native engine: `pkmn_engine_core` (event loop, portfolio,
  execution, cost model, product table, all five strategies — C++20, no
  Python dependency) plus a nanobind binding module built by
  scikit-build-core into `pkmn_quant._engine`. `cpp/tests/` is a Catch2
  suite (25 tests, including a Plan 11 thread-safety smoke test) exercising
  the core in isolation, independent of pytest;
  `cmake -S cpp -B cpp/build -DPKMN_BUILD_TESTS=ON && cmake --build
  cpp/build -j && ctest --test-dir cpp/build` runs it. `src/pkmn_quant/
  engine/native.py`'s `NativeBacktest` is the Python-side adapter: shapes
  `MarketData` into flat numpy arrays (once per run — see the "measured
  speedup" note below on why this bounds the end-to-end gain), crosses the
  boundary once, and repackages the result into the same `Result` type the
  Python engine returns, so callers can't tell engines apart.
  The native engine additionally supports initial-holdings seeding
  (`SeededHolding` on `NativeBacktest`; C++ `Portfolio::seed` /
  `SeedPosition`) — a C++-only capability the Python engine deliberately
  lacks (Plan 2a, first step toward a C++-only engine), so it is pinned by
  Catch2 goldens + `tests/test_native_seeding.py`, not by parity. A
  `NativeStrategySpec` names one of the five native strategies (or falls
  back to a per-bar Python callback bridge for anything else, e.g.
  ml-ranker). Parity with the Python engine is bit-for-bit by design
  (`tests/test_native_parity.py`, `scripts/parity_full.py`) — see the
  Plan 10 status bullet above for the one real gap the full-warehouse run
  found and how it was closed.
- `data/` — gitignored. Contains 908 ingested days (2024-02-08..2026-08-03,
  36,381,803 price rows, 31,713 distinct priced products, 417 MB) plus raw
  archives. Do not delete; a full re-ingest is hours, but a rebuild from the
  cached archives is ~17 min (`scripts/rebuild_warehouse.py`).
  **The catalog and the price history now cover the same 217 tcgcsv groups**
  — the deliberate scope split documented here through 2026-08-03 (catalog
  wide, prices `MIN_SET_RELEASE`-onward only) is gone, and so is
  `MIN_SET_RELEASE`: `tracked_groups` in `ingest.py` returns every group
  tcgcsv publishes, with no date bound in either direction (see the gotcha
  below on why the upper bound had to go too). The catalog
  (`products.parquet`) reads 32,527 rows / 214 groups as of 2026-08-04;
  it lags prices at 217 groups because a group with no products, or one
  dropped by a transient fetch failure, contributes no catalog rows.
  `data/warehouse-old/` is the **retained pre-swap 23-group warehouse**
  (38 MB, 907 days, 3,197,600 rows / 6,671 products) — it is the only copy
  of the exact data every pre-2026-08-04 number in
  `docs/research-findings-2026-07.md` was computed on, and the rollback
  target if the swap ever needs undoing. Do not delete it.
  `data/warehouse-full-failed-197groups/` is a superseded failed shadow
  build, also retained deliberately. Those catalog figures are
  **point-in-time**: the
  catalog is re-fetched on every ingest, so unattended `pkmn daily` runs move
  them, and on the pre-fix code they moved it twice in 48 hours (a connection
  reset on 2026-07-30 left 32,218 rows / 213 groups; another on 2026-07-31
  left 32,410 / 213). Expect the live file to differ from any figure pinned
  here. Since the final-review fix that movement can no longer
  silently degrade the priced universe: `refresh_products` merges into the
  stored catalog rather than replacing it, and refuses to write a fetch
  holding under 90% of the rows already on disk. `data/portfolio/` holds the
  gitignored real and paper
  ledgers. `data/runs/` holds the gitignored experiment registry
  (`registry.jsonl`).
- `src/pkmn_quant/api/` — FastAPI research explorer API (dependency group
  `api`): `models.py` has the Pydantic response models (also the
  cross-language contract checked by `tests/api/test_contract.py`); 11
  endpoints over the experiment registry and walkforward/evaluate/backtest
  artifacts. No longer read-only as of Plan 2c: `POST /api/backtest` (202 +
  job id) plus `GET /api/jobs`/`GET /api/jobs/{job_id}` are backed by
  `jobs.py`'s single-worker `JobManager` (an injected `Executor`; job state
  is in-memory only — a restart loses the job list, never the results,
  which are already durable in the run registry). Every triggered run is
  paired with an identical-starting-state buy-and-hold benchmark
  (`research/backtest_run.py`'s `run_with_benchmark`), linked solely by
  `config["benchmark_for"]`. Run standalone with `uv run --group api
  uvicorn pkmn_quant.api:app --port 8000`, or via `make web` alongside the
  frontend dev server.
- `web/` — React/TypeScript SPA (Vite), its own toolchain (`web/package.json`,
  `web/package-lock.json` committed) independent of the `uv`/Python gates:
  6 screens consuming the API above — runs browser (`/`), new-run trigger
  form (`/new`, generated from the strategy's ParamSpecs, with a holdings
  editor and product autocomplete), jobs queue (`/jobs`, self-stopping
  polling), walk-forward detail (`/walkforward/:runId`), backtest detail
  (`/backtest/:runId`, benchmark overlaid on the equity chart, seeded-exit
  markers, paginated fills table), and cross-strategy rigor compare
  (`/evaluate/:runId`). `npm run check` (tsc --noEmit + vitest, 29 tests)
  and `npm run build` (production build) gate it; CI runs both in a
  dedicated `web` job alongside the Python `checks` job.
- `cpp/src/pkmn_book/` — `OrderBook`: price-time order book (per-book
  pooled order arena, open-addressing order-id table, per-side contiguous
  price levels with the best price at the back). No Python or zlib dependency.
- `cpp/src/pkmn_md/` — IEX market data: streaming pcapng reader over zlib,
  IEX-TP framing + sequence tracker (a seq-1 packet with a newer send time is
  a stream restart), DEEP+ and DEEP decoders, the TOPS Quote decoder,
  `BookSet` (one `OrderBook` per
  symbol), `timing.hpp` (cntvct_el0 timer). Plan C adds `book_digest.*`
  (canonical FNV-1a book digest), `recovery_proto.*` (framed TCP retransmit
  protocol), `retransmit_ring.*`, `drop_injector.hpp` and `gap_recovery.*`
  (`RecoveringHandler`, socket-free). Built into `md_tests`, separate
  from `engine_tests`, and kept out of the Python wheel build.
- `cpp/tools/` — `iex_decode`, `pcap_count`, `iex_oracle` (book vs DEEP/TOPS,
  see `docs/iex-oracle.md`), `iex_bench` (driven by `scripts/bench_iex.sh`).
  Plan C adds `iex_digest` (reference digests by direct replay),
  `feed_publisher`, `feed_handler` and `net_util.hpp`
  (driven by `scripts/bench_feed.sh`; results in `docs/benchmarks/feed-handler.md`).
- `cpp/src/pkmn_engine/ladder.*`, `execution_book.*` — Plan B: the daily
  ladder builder (modelled resting orders, synthetic, built from the day's
  tcgcsv quote) and book execution against it. `cpp/src/pkmn_wire/` — the
  ITCH-style Pokemon quote wire format: encoder, decoder, `replay.hpp`.
  `docs/pokemon-wire-protocol.md` specifies it; `docs/benchmarks/
  pokemon-book.md` (raw JSON in `pokemon-book-raw/`) records the
  measurements. `src/pkmn_quant/wire.py` feeds the encoder.
- `docs/benchmarks/` — `iex-book.md` (dated results, newest first, plus the
  hand-written Task 6 A/B), flamegraphs and folded stacks for the baseline
  (`-c9e60e0`) and the optimized book. Also `docs/iex-oracle.md` and
  `docs/iex-hist-terms.md` (IEX data terms; the citation line is required
  wherever IEX data results are published).
- `data/iex/` — gitignored (covered by `/data/`); the three 2024-12-23
  `.pcap.gz` files (DPLS, DEEP, TOPS) are about 4.5 GiB on disk (`du -sh`).
  Never decompress to disk; every tool streams the gzip.

## Conventions and gotchas

- Frozen dataclasses for value objects; mutable state in the smallest scope.
  Copy mutable containers at trust boundaries (see Context construction).
- Strategies must be reset-safe: `Backtest.run()` calls `strategy.reset()`.
- Golden regression test (`tests/test_cli_backtest.py`) pins exact engine
  numbers — if a deliberate change shifts results, update the goldens in the
  same commit with a hand-derivation in the docstring.
- tcgcsv.com 401s httpx's default User-Agent — always use `tcgcsv.make_client()`.
- **The catalog is a mutable research input.** Every ingest merges a fresh
  fetch into `products.parquet` and upstream rows win, so `released_on`,
  `name` or `group_id` can change under a fixed price history (104 release
  dates changed between 2026-08-03 and 2026-10-25, 102 of them to null).
  sealed-accumulation and both ML strategies read `released_on`, so their
  results move with it. Record the catalog sha256 with any run you intend to
  reproduce (runs recorded since the live-ingest merge carry
  `catalog_sha256` in `data_fingerprint`), and snapshot the file before a
  long batch.
- **The tcgcsv price archive is offline (403 since 2026-09-14).** New days
  come only from the live per-group endpoints, which serve only the current
  update: a day not captured while it is current is lost for good. `pkmn
  ingest`/`ingest_range` remain for rebuilds and a backfill if the archive
  returns; `pkmn daily` no longer calls them. A day written from live data is
  final, and a restored archive must never overwrite it.
- **Live snapshots are dated by the UTC calendar date of tcgcsv's update
  timestamp** (`last-updated.txt`), never by the local date or the fetch
  time: a 2026-10-24T20:05Z update is day 2026-10-24 even if fetched the next
  morning. This assumes the old archive file `prices-D` held the update
  published on UTC day D. **That equivalence is a convention, not a verified
  fact**: no day exists in both sources to check it against. If it is ever
  shown to be off by one, change `live.update_day` and nothing else.
- **An abandoned live day is not in the warehouse on purpose.** A
  superseded day under `LIVE_FLOOR` (or with zero rows) gets an `abandoned`
  record in its `data/raw/live/<day>/manifest.json` and is skipped by
  `pending_days` from then on. Because it is not stored, `ingest_range`
  would fill it from a restored archive. Deleting the record makes the next
  run write the partial day, and after that no archive backfill can replace
  it, because a live day, once written, is final.
- ntfy phone alerts read `[ntfy] topic` (and optional `server`) from
  gitignored `data/config/notify.toml`. Absent or unreadable config means
  banners only. Never commit the topic: anyone who knows a public ntfy topic
  can read and post to it. A notification failure never fails a run.
- The launchd job only changes when it is reinstalled: editing
  `scripts/com.pkmn-quant.daily.plist` does nothing to the installed
  `~/Library/LaunchAgents/com.pkmn-quant.daily.plist`. Reinstall it (sed
  REPO_PATH, `launchctl bootout`, `launchctl bootstrap`) for a change to
  apply. Since the merge (2026-10-26) the installed copy runs the main
  checkout (`/Users/ml3787/pkmn-stock`), so it runs whatever branch that
  checkout has checked out.
- The stopgap `capture_raw.py` (outside the repo) is superseded by `pkmn
  live-ingest`/`pkmn daily`, which is now installed; do not run it again. Its
  `data/raw/live/` directories are read as already held, not re-fetched.
- `data/results/daily-state.json` (alert state) sits next to the
  `daily-<date>/` directories, so a bare `glob("daily-*")` matches it too;
  glob `daily-*/daily.json` (as the dashboard does) or `daily-2*`.
- `refresh_products` merges into `products.parquet` and never replaces it: it
  runs on every ingest, including the unattended `pkmn daily` job that
  swallows ingest errors, so a replace lets one transient connection reset
  delete a whole group's products silently (it did, on 2026-07-30). Keep the
  merge, the `CATALOG_FLOOR` refusal, and the wide `(httpx.HTTPError,
  ValueError, KeyError)` filter — a fetch failure must degrade to "stale
  rows kept, failure counted", never to "rows gone, run reports success".
- **`pkmn evaluate`'s auto-discovered benchmark went stale across the
  universe swap, and published a wrong statistic.** With no `--benchmark`,
  `evaluate` globs `data/results/buy-and-hold-sealed-*` and takes the
  longest curve; only if that matches nothing does it fall back to the run
  registry. The 23-group directory written 2026-07-14 survived the swap, so
  the first post-swap run (`20260804T184401Z-cf292c`) scored post-swap
  strategies against a **pre-swap** benchmark (+203.03% over the 660 aligned
  days instead of +10.74%) and published Reality Check p = 1.0000. The
  corrected re-run (`20260804T204825Z-fa07b8`) gives **p = 0.8038**. Both
  legacy directories are renamed, under `data/results/`, so the glob no
  longer matches and plain `pkmn evaluate` falls through to the registry
  (newest recorded buy-and-hold-sealed `backtest` run):
  `STALE-preswap-23group-buy-and-hold-sealed-2024-03-01-2026-06-30/` and
  `STALE-preswap-23group-buy-and-hold-sealed-2024-08-28-2026-06-30/`.
  Nothing was deleted. **Do not re-create a `buy-and-hold-sealed-*`
  directory name**; `pkmn backtest` has written to `data/results/<run_id>/`
  since Plan 2b, so only a hand-made directory can resurrect this. Prefer
  passing `--benchmark data/results/<run_id>` explicitly when it matters.
  The rename left **exactly one** dangling `artifact_path` in the registry
  (audited across all 37 records): `20260714T045104Z-8d084f`, the `backtest`
  behind the pre-swap +183.67% headline, still records
  `data/results/buy-and-hold-sealed-2024-03-01-2026-06-30`, which now lives
  under the `STALE-preswap-23group-` prefix. Nothing breaks — `pkmn runs
  show` prints it from the self-contained JSONL, the API 404s by design
  (`api/data.py`'s `art.exists()` guard → `NotFound` → 404), and the
  dashboard's `.exists()` filter silently drops it from the overlay
  selector. Read the artifact at its `STALE-preswap-23group-` path if you
  want it; do not rename the directory back and do not rewrite the
  append-only registry line. No record points at the second renamed
  directory; the four earlier `evaluate` records name the old path in
  `config.benchmark`, not `artifact_path`. Details in the findings doc's
  Full set coverage section.
  Related: `evaluate` names its artifact directory by **date**, not run id,
  so two runs on one day overwrite each other — the superseded artifact here
  is preserved as `data/results/evaluate-2026-08-04-stale-benchmark/`, and a
  registry record's own `config.benchmark`/`results.reality_check_p` are the
  authoritative per-run values, not whatever is in the directory today.
  **The glob is now deleted (2026-08-09)**: `pkmn evaluate` resolves its
  benchmark through `_benchmark_candidates(load_runs(root))`, the same
  registry-only rule `pkmn horizon`, `/api/runs` and the dashboard apply —
  which also drops the paired sidecar benchmarks tagged `benchmark_for` —
  and it now refuses a benchmark whose window does not span the strategies'
  (a real 61-day buy-and-hold on disk used to clear the >= 60-day overlap
  check and would have produced a Reality Check over 61 days of 660-day
  curves). Until that fix, the 2026-08-08 run picked the correct benchmark
  only because those renames were in place, so nothing matched.
- **Current bar (2026-10-24, formula): +126.84%, run
  `20261024T170908Z-99ee95`**, 86.1% notional / 99.95% all-in deployed,
  warmup 0; see the Formula baseline status bullet. The paragraph below is
  the older, superseded figure, kept for provenance.
- **Buy-and-hold benchmark reference figure (pre-fix-wave), SUPERSEDED by
  the formula baseline above.** The run below allocated half the account to one
  non-printing product through the unbounded deficit sweep, and predates the
  screen and liquidity-tier fixes as well; do not quote it as the bar until
  the re-run lands. Kept here with its provenance so the replacement can be
  compared against it. 2024-03-01→2026-06-30,
  impact-on, 217-group universe: +24.93% (run `20260807T135424Z-8362bc`,
  $10,000, `n_assets=100`). CAGR +10.02%, max drawdown −25.73%, Sharpe
  0.6336, equity $10,000.00 → $12,492.97, 38 fills. **That drawdown is entry
  friction, not market risk**: recomputed from the run's `equity.parquet` it
  lands on the FIRST fill day against a running peak equal to the all-cash
  starting balance, and nothing near it recurs across the remaining 28
  months. It prices what it costs to convert the account into sealed
  inventory (fees plus the floor premium, since positions mark at `market`
  while buys pay `max(market, low)`) — a real and useful number, but never
  quote it as "how far a long sealed position fell" or set it beside a
  strategy's drawdown. **This is the bar; use
  it, and state the deployment fraction with it: 74.2% notional / 99.9%
  all-in** ($7,423.07 notional + $38.00 fees + $2,530.36 impact = $9,991.43;
  impact alone is 34.1% of notional under the buy floor, so a quarter of the
  account is friction, not inventory). **Quote deployment on one basis or the
  other, never one from each** — notional runs 60.1 → 4.7 → 74.2 across the
  three benchmark eras and all-in runs 63.0 → 5.5 → 99.9; "4.7% → 99.9%"
  mixes them and overstates the repair. Every earlier benchmark
  figure is **dead as a comparison point**: +186.0% flat-cost and +183.67%
  impact-on were the 23-group universe, the impact-on one at 63.0% all-in
  deployment (the flat-cost run charges no impact at all, so its all-in
  necessarily differs and 63.0% does not describe it), and
  +10.53% (`20260804T061600Z-37a23f`) was the 217-group universe at 5.5%
  all-in — a 95%-cash account whose Sharpe 6.57 and −0.90% drawdown were
  money-market artifacts. **+24.93% is not "the market fell then recovered"
  relative to those numbers: the three are different allocation rules over
  two different universes (23 groups, then 217) under two different cost
  models (the buy floor arrived with the third)** — not one market measured
  three times, so never chart them as a series. Two caveats survive the repair.
  (a) 38 of the 100 screened assets actually hold units (the pre-floor
  day-one measurement predicted 73), so the bar is less diversified than
  designed. (b) **The figure carries an implicit `warmup_days=0`** — the
  screen ranks on `ctx.history`, so the bar moves with how much history
  exists at the first bar: measured on the native engine, same window/cash/
  impact, warmup 0 gives 38 fills / +24.93% (reproducing the run exactly)
  and warmup 120 gives 42 fills / +16.07%. `pkmn backtest` exposes no
  `--warmup-days` flag and defaults to 0, while `pkmn walkforward` defaults
  to 120, so the bar and the walk-forward rows compared against it are not
  on the same warmup. **Direction and scope, so nobody has to re-derive
  them:** the published bar is the *harder* of the two (+24.93% vs +16.07%),
  so the mismatch is conservative against the strategies and no conclusion
  flips; and **the horizon results are unaffected** — all six horizon source
  runs and the index are `pkmn backtest` at warmup 0, so they are internally
  consistent. See the findings doc's Instrument repair section.
- **The benchmark is deliberately NOT walk-forward-searchable.**
  `buy-and-hold` stays out of `REGISTRY`; its one knob lives in
  `BUY_AND_HOLD_PARAMS` (`research/backtest_run.py`, `n_assets`, default
  100, bounds [1, 10000]) so it is CLI-settable and declarative but has no
  optuna search space. This is a deliberate deviation from the
  instrument-repair design spec, which asked for it to be searchable.
  Reason: buy-and-hold is the bar, not a candidate — tuning the bar to
  maximise its own return makes every comparison against it self-serving,
  and tuning it to anything else makes it arbitrary. If you change
  `n_assets`, you have changed the bar: re-run the benchmark, say so, and
  do not compare across the change.
- **Buys price at `max(market, low)`; sells are deliberately asymmetric.**
  tcgcsv's `market` is derived from completed **sales** while `low`/`mid`/
  `high` describe current **listings**, so `market < low` is ordinary (2.19M
  in-window rows), not corrupt — a buy simply cannot execute below the
  cheapest live offer. `CostModel.buy_impact` floors the base at
  `max(market, low)` and charges the premium through the **`impact` column**,
  leaving a fill's `price` as the day's `market` print, then walks toward
  `mid` from the floored base. A null `low` means no floor, never a guessed
  one. **No sell-side floor exists and none should be invented**: tcgcsv
  publishes no bid series (`low`/`mid`/`high` are all ask-side), so a
  symmetric fix would be fabricating a price. **Marking is also unchanged** —
  positions mark at `market`, so buying at the ask and marking at the last
  sale books an immediate unrealised loss; that is correct, not a bug.
  One consequence to know: the floor is **gated on `impact_enabled`**
  (`engine/execution.py:86-102`), because the premium has no column to live
  in with impact off and sizing off a floored base without it cut real
  quantities up to 10x. So `--no-impact` runs again size and price buys at a
  sales print that may sit below any live offer — they are a comparability
  tool, not a model of executable prices.
- Sharpe/Sortino on this data are inflated by mark smoothing (thin markets,
  carry-forward marks), and the full-set-coverage swap made this **worse,
  not better**: the same carry-forward marks now apply to 31,713 priced
  products instead of 6,671, most of them thin vintage singles. Sparsity on
  the current warehouse: 22,677 products print on >=90% of days, 4,232 on
  50-90%, 3,828 on 10-50%, 975 on <10%. The top bucket is not evidence of
  liquidity — tcgcsv publishes a daily `market` price whether or not
  anything sold, so "prints every day" and "trades every day" are different
  claims. Caveat every Sharpe/Sortino in any report, and treat the wide
  universe's risk metrics as more optimistic than the old ones, not less.
- `tcgcsv.Group.published_on` is a **mutating "last touched" timestamp, not a
  release date** — for 19 promo/catch-all groups (POP Series, Nintendo
  Promos, EX Trainer Kits, Miscellaneous Cards & Products, …) it reads as
  today's date and moves. Never filter groups on it in either direction.
  `tracked_groups` used to bound it `MIN_SET_RELEASE <= published_on <=
  today`; the upper bound alone silently dropped 20 groups from a whole
  907-day backfill (2026-08-03). Both bounds are gone; keep them gone.
- **Long runs: `caffeinate -i` is not enough — use `caffeinate -dimsu`.**
  `-i` blocks idle sleep only. Two multi-hour walk-forwards have died
  silently under `-i` with a zero-byte log and no traceback (2026-07-30 and
  2026-08-04). `pkmn walkforward` also prints nothing until it completes, so
  a redirected run gives no progress signal regardless of `PYTHONUNBUFFERED`
  — check liveness with `ps`, not the log.
- **Run ml-ranker and ml-ranker-v2 walk-forwards with `--workers 1`.** They
  go through the per-bar Python callback bridge and hold the GIL for the
  entire loop, so fold parallelism buys nothing while costing N copies of
  the per-fold market window — and since the full-set swap that window is
  11x bigger. The CLI default (`--workers 0` = auto = one thread per core)
  put this machine into a 7.4-hour thrash (load average 89 on 8 cores,
  24.6 GB of 25.6 GB swap) before being killed; `--workers 1` finished the
  same run in 75 minutes. Fold-serial and fold-parallel are bit-for-bit
  identical (Plan 11), so this costs nothing in comparability. Budget ~4.5
  min per native-strategy walk-forward and 75-140 min per ML one.
- Never share one `PreparedMarket` across fold worker threads in
  `run_walkforward`: each fold builds its own IS/OOS `PreparedMarket`
  windows from the shared read-only `frame_full`/`products_full` frames —
  the windows themselves are per-fold and must stay that way for the
  bit-for-bit serial/parallel equivalence to hold.
- Workflow: feature branch per plan; two-stage review per task; STOP after each
  completed task and explain what/why at intern level; wait for the user's
  explicit green light before the next task.
- After editing anything under `cpp/`, `uv sync --reinstall-package
  pkmn-quant` — `uv sync` alone will not rebuild the extension module from a
  source change, and a stale `.so` silently keeps running the old C++ code.
  Never enable fast-math or fp-contract (`-ffast-math`, `-ffp-contract=fast`,
  MSVC `/fp:fast`) in `cpp/CMakeLists.txt` or anywhere in the build —
  bit-for-bit parity with the Python engine depends on IEEE-754-exact,
  non-reassociated floating point; either flag will pass the C++ unit tests
  and silently break parity on real data.

- **IEX/Plan A build gotchas.** A non-SKBUILD cmake configure of `cpp/` now
  requires zlib (`find_package(ZLIB REQUIRED)`); the Python wheel build does
  not (the market-data targets are excluded from it). On macOS FindZLIB
  returns the SDK `usr/include`, which `cpp/CMakeLists.txt` clears from
  `ZLIB::ZLIB`'s interface includes to avoid shadowing the toolchain headers.
  A plain `uv sync` drops the `api` and `dashboard` groups, after which
  `uv run mypy` fails on the api code; use
  `uv sync --group api --group dashboard` before the gates. The bench SHA
  stamp is dirty-aware and counts untracked files (a build directory inside
  the tree makes a result read `-dirty`; that is why the Task 6 A/B rows
  say `1c14e20e25d7-dirty`). Benchmark comparisons in this VM need
  interleaved A/B runs; a single before/after pair is inside the noise.

- **Plan B book execution gotchas.** Book and formula results are never
  compared: `pkmn evaluate` and `pkmn horizon` refuse to mix modes.
  `pkmn evaluate` defaults to `--execution book` and filters to one mode;
  pass `--execution formula` for the legacy artifacts. Book walk-forwards
  write `data/results/wf-{strategy}-{start}-{end}-book`; formula keeps the
  legacy name, and `walkforward.json` and registry records carry
  `"execution"` (missing means formula). Live paths are still formula:
  `pkmn signals` prices with the formula, but it picks the lexicographically
  last `wf-{strategy}-*` directory, which is the `-book` one when both
  exist, so its params can come from a book walk-forward while its prices
  stay formula. Impact-off runs are recorded as formula. The ladder prices in
  whole cents, rounded to nearest with halves away from zero (`std::llround`);
  the exact 1e9-ticks ladder is test-only. Each side has its own full depth
  per asset-day (the formula shares one counter across both sides), two buys
  walk the same ask ladder in arrival order, and the book is rebuilt fresh
  every day. On the five native strategies at default params zero fills fell
  on two-sided asset-days, so the one designed divergence never fired there.
  Crossed or missing quotes build flat books at the base price. The wire
  encoder raises `ValueError` on a price that is not a whole number of
  cents or is at or above the u32 null sentinel. Wire files live in
  gitignored `data/wire/`. `pkmn evaluate` writes book runs to
  `evaluate-<date>-book/` and formula to `evaluate-<date>/`, so one of each
  per day coexist (two of the same mode on one day still overwrite).
- Formula records now carry `"execution": "formula"` in their config, so the
  config hash of an otherwise identical formula run differs before vs after
  Plan B (the hash is display-only today; compare configs, not hashes).
- **The catalog is unversioned and mutates daily.** `products.parquet` is rewritten by every `pkmn daily` or `pkmn live-ingest` run that writes a day (runs that write nothing leave it alone), and it is an input a price-only fingerprint cannot see. Compare `catalog_sha256` in two runs' `data_fingerprint` before calling two ML runs comparable. Records written before the key existed lack it.
