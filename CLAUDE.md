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
  `--engine cpp` on `pkmn backtest`/`walkforward`; anything else (a raw
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
  price rows; warehouse-wide, past the window, the gap is far larger —
  1,845 of 6,493 as of this run, mostly explained by a second, distinct
  cause: `ingest.py`'s documented one-time catalog fetch never picking up
  new sets) — `NativeBacktest.run()` crashed on it (`KeyError`)
  where the Python engine silently excludes uncataloged assets from
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

## Commands

```bash
uv sync                      # install (uv manages everything; never pip)
uv run pytest                # test suite
uv run ruff check . && uv run ruff format --check . && uv run mypy
                             # lint + format + strict typecheck (src/ only)
uv run pkmn ingest --start 2026-07-01 --end 2026-07-31   # extend price history
uv run pkmn backtest --start 2024-03-01 --end 2026-06-30 # benchmark backtest
uv run pkmn walkforward --strategy sealed-accumulation \
    --start 2024-03-01 --end 2026-06-30 --trials 15      # research run (cpp, fold-parallel auto)
uv run pkmn walkforward --strategy sealed-accumulation \
    --start 2024-03-01 --end 2026-06-30 --trials 15 --workers 1  # serial reference run
uv run pkmn signals --strategy sealed-accumulation       # live recommendations
uv run --group dashboard streamlit run app/dashboard.py  # results explorer
uv run pkmn portfolio show                               # real positions + P&L
uv run pkmn daily --skip-ingest                          # the loop, offline
uv run pkmn daily --skip-ingest --paper                  # paper mode dry-run
uv run pkmn runs list                                     # experiment registry: recorded runs
uv run pkmn runs show <run-id>                             # full record for one run
uv run pkmn evaluate                                     # cross-strategy rigor: CIs, DSR, Reality Check
uv run pkmn backtest --start ... --end ... --no-impact    # flat-cost, skip market-impact model
uv run pkmn backtest --start ... --end ... --engine cpp   # same result, native C++ engine
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
  ml-ranker-v2's in-loop selection. `runs.py`:
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
  Python engine returns, so callers can't tell engines apart. A
  `NativeStrategySpec` names one of the five native strategies (or falls
  back to a per-bar Python callback bridge for anything else, e.g.
  ml-ranker). Parity with the Python engine is bit-for-bit by design
  (`tests/test_native_parity.py`, `scripts/parity_full.py`) — see the
  Plan 10 status bullet above for the one real gap the full-warehouse run
  found and how it was closed.
- `data/` — gitignored. Contains 874 ingested days (2024-02-08..2026-06-30,
  ~2.9M price rows) plus raw archives. Do not delete; re-ingest is ~40 min.
  `data/portfolio/` holds the gitignored real and paper ledgers. `data/runs/`
  holds the gitignored experiment registry (`registry.jsonl`).
- `src/pkmn_quant/api/` — FastAPI read-only research explorer API (dependency
  group `api`): `models.py` has the Pydantic response models (also the
  cross-language contract checked by `tests/api/test_contract.py`); 5
  endpoints over the experiment registry and walkforward/evaluate artifacts.
  Run standalone with `uv run --group api uvicorn pkmn_quant.api:app --port
  8000`, or via `make web` alongside the frontend dev server.
- `web/` — React/TypeScript SPA (Vite), its own toolchain (`web/package.json`,
  `web/package-lock.json` committed) independent of the `uv`/Python gates:
  3 screens (runs browser, walk-forward detail, cross-strategy rigor
  compare) consuming the API above. `npm run check` (tsc --noEmit + vitest,
  8 tests) and `npm run build` (production build) gate it; CI runs both in
  a dedicated `web` job alongside the Python `checks` job.

## Conventions and gotchas

- Frozen dataclasses for value objects; mutable state in the smallest scope.
  Copy mutable containers at trust boundaries (see Context construction).
- Strategies must be reset-safe: `Backtest.run()` calls `strategy.reset()`.
- Golden regression test (`tests/test_cli_backtest.py`) pins exact engine
  numbers — if a deliberate change shifts results, update the goldens in the
  same commit with a hand-derivation in the docstring.
- tcgcsv.com 401s httpx's default User-Agent — always use `tcgcsv.make_client()`.
- Sharpe/Sortino on this data are inflated by mark smoothing (thin markets,
  carry-forward marks). Caveat them in any report; compare strategies against
  the buy-and-hold benchmark (+186.0% flat-cost / +183.7% impact-on for
  sealed, 2024-03→2026-06), not equities.
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
