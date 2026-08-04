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
- Full set coverage complete on feat/full-set-coverage (520 passed +
  1 skipped Python via a bare `uv run pytest` after `uv sync --group api`;
  the skip is `tests/test_dashboard.py`, which needs `streamlit` — run
  `uv run --group dashboard pytest tests/test_dashboard.py` separately for
  it, 5 passed, i.e. 525 with the dashboard group installed; 26 web tests
  via `cd web && npm run check` plus a clean `npm run build`): **prices now
  cover all 217 tcgcsv groups, not 23.** `MIN_SET_RELEASE` is deleted and
  `tracked_groups` has no date bound in either direction;
  `Paths(root, warehouse_name=)` supports a shadow warehouse;
  `scripts/rebuild_warehouse.py` rebuilds the whole history from the cached
  raw archives (17m08s for 907 days, resumable) and `scripts/
  verify_rebuild.py` gates the swap on every pre-existing product
  reproducing exactly. Measured at swap: 3,197,600 → 36,337,465 price rows
  (**11.36x**) and 6,671 → 31,712 priced products (**4.75x**) over the same
  907 days; verification PASSED first try, so **the change is purely
  additive** — no pre-existing row moved. Priced universe by kind: 28,175
  `single`, 2,439 `sealed`, 986 `excluded`, 112 `null` (delisted upstream,
  no catalog row; engine tags them kind `other`). Full re-run of all six
  strategies plus the benchmark, impact-on both sides, same window
  (2024-03-01..2026-06-30): buy-and-hold sealed **+183.67% → +10.53%**;
  sealed-accumulation −7.36% → −8.59%; xs-momentum −9.30% → −38.72%;
  dip-buyer −21.15% → −99.80%; cost-aware-reversion −22.45% → −98.70%;
  ml-ranker −5.05% → −81.64%; ml-ranker-v2 −23.15% → −99.81%. `pkmn
  evaluate` (`20260804T184401Z-cf292c`): joint White's Reality Check
  **p = 1.0000, unchanged**; every deflated Sharpe still far below the 0.5
  coin-flip point (highest 0.254, ml-ranker, whose 95% CI is
  [−99.58%, +331.57%] — wide, not good). **The benchmark's movement and two
  of the four wipeouts were traced; the two ML wipeouts were not.** The
  benchmark's fall is a capital-deployment artifact: `BuyAndHold`
  equal-weights with integer quantities, day-1 sealed assets went 45 →
  1,315, per-asset budget went $222.22 → $7.60, and deployed capital went
  $6,012.55 (60.1%) → $466.78 (4.7%) — the run is 95% cash. **The old bar
  was not fully deployed either** (37% cash): on the slice actually at risk
  the two runs returned +291.7% and +191.1%, so ~90% of the 17x headline
  collapse is the sizing rule, not the assets. The dilution reading is
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

## Commands

```bash
uv sync                      # install (uv manages everything; never pip)
uv run pytest                # test suite
uv run ruff check . && uv run ruff format --check . && uv run mypy
                             # lint + format + strict typecheck (src/ only)
uv run pkmn ingest --start 2026-07-01 --end 2026-07-31   # extend price history
uv run pkmn refresh-products                             # repair the catalog from all 217 groups, no price ingest
uv run python scripts/rebuild_warehouse.py --root .      # rebuild the warehouse into data/warehouse-full/ from cached archives (~17 min, resumable)
uv run python scripts/verify_rebuild.py --root .         # gate the swap: every pre-existing product must reproduce exactly (exit 0 = safe)
uv run pkmn backtest --start 2024-03-01 --end 2026-06-30 # buy-and-hold benchmark (default strategy)
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
uv run pkmn evaluate                                     # cross-strategy rigor: CIs, DSR, Reality Check
uv run pkmn backtest --start ... --end ... --no-impact    # flat-cost, skip market-impact model
uv run pkmn walkforward --strategy sealed-accumulation --start ... --end ... \
    --trials 15 --engine python                          # reference (slow) Python engine, walkforward only
cmake -S cpp -B cpp/build -DPKMN_BUILD_TESTS=ON && cmake --build cpp/build -j && ctest --test-dir cpp/build
                             # C++ unit tests (Catch2), independent of pytest
uv run python scripts/parity_full.py                      # full-data bit-for-bit acceptance, both engines
uv run --group viz python scripts/render_readme_chart.py   # regenerate README hero chart from local artifacts
make web                                                  # run the API + web dev server together
```

All four gates must pass before every commit. CI runs them with
`uv sync --frozen` (stale uv.lock fails the build — commit pyproject.toml and
uv.lock together).

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
  (`/evaluate/:runId`). `npm run check` (tsc --noEmit + vitest, 25 tests)
  and `npm run build` (production build) gate it; CI runs both in a
  dedicated `web` job alongside the Python `checks` job.

## Conventions and gotchas

- Frozen dataclasses for value objects; mutable state in the smallest scope.
  Copy mutable containers at trust boundaries (see Context construction).
- Strategies must be reset-safe: `Backtest.run()` calls `strategy.reset()`.
- Golden regression test (`tests/test_cli_backtest.py`) pins exact engine
  numbers — if a deliberate change shifts results, update the goldens in the
  same commit with a hand-derivation in the docstring.
- tcgcsv.com 401s httpx's default User-Agent — always use `tcgcsv.make_client()`.
- `refresh_products` merges into `products.parquet` and never replaces it: it
  runs on every ingest, including the unattended `pkmn daily` job that
  swallows ingest errors, so a replace lets one transient connection reset
  delete a whole group's products silently (it did, on 2026-07-30). Keep the
  merge, the `CATALOG_FLOOR` refusal, and the wide `(httpx.HTTPError,
  ValueError, KeyError)` filter — a fetch failure must degrade to "stale
  rows kept, failure counted", never to "rows gone, run reports success".
- **Buy-and-hold benchmark reference figure, 2024-03-01→2026-06-30,
  impact-on: +10.53%** (run `20260804T061600Z-37a23f`) on the current
  217-group universe. The older +186.0% flat-cost / +183.7% impact-on
  figures are **dead** — they were measured on the 23-group universe and
  cannot be compared to anything run today. **Do not use +10.53% as a bar
  either without reading the caveat**: `BuyAndHold` equal-weights the sealed
  universe with integer quantities, and on 1,315 day-1 sealed assets a
  $10,000 account gives a $7.60 per-asset budget, so the run deploys only
  $466.78 (4.7%) and sits 95% in cash for 22 months. Its Sharpe of 6.57 and
  max drawdown of −0.90% are money-market artifacts, not risk-adjusted
  quality. The benchmark's sizing rule needs fixing before any wide-universe
  comparison means anything (see the findings doc's Full set coverage
  section); until then, state the deployment fraction alongside the return.
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
