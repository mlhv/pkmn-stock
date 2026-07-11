# pkmn_quant — Pokemon card backtesting system

Algo-trading research system for Pokemon card prices (TCGplayer data via
tcgcsv.com). Design spec: `docs/superpowers/specs/2026-06-09-pkmn-quant-design.md`.

## Status (2026-07-10)

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

## Commands

```bash
uv sync                      # install (uv manages everything; never pip)
uv run pytest                # test suite
uv run ruff check . && uv run ruff format --check . && uv run mypy
                             # lint + format + strict typecheck (src/ only)
uv run pkmn ingest --start 2026-07-01 --end 2026-07-31   # extend price history
uv run pkmn backtest --start 2024-03-01 --end 2026-06-30 # benchmark backtest
uv run pkmn walkforward --strategy sealed-accumulation \
    --start 2024-03-01 --end 2026-06-30 --trials 15      # research run
uv run pkmn signals --strategy sealed-accumulation       # live recommendations
uv run --group dashboard streamlit run app/dashboard.py  # results explorer
uv run pkmn portfolio show                               # real positions + P&L
uv run pkmn daily --skip-ingest                          # the loop, offline
uv run pkmn daily --skip-ingest --paper                  # paper mode dry-run
```

All four gates must pass before every commit. CI runs them with
`uv sync --frozen` (stale uv.lock fails the build — commit pyproject.toml and
uv.lock together).

## Layout

- `src/pkmn_quant/data/` — tcgcsv client, transforms, quality gates, Parquet/
  DuckDB warehouse (`Warehouse.query()` gives SQL over `prices`/`products`).
- `src/pkmn_quant/engine/` — event-driven backtester: costs, portfolio, data
  view, execution, strategy ABC, metrics, backtest loop. T+1 fills, long-only.
- `src/pkmn_quant/strategies/` — Strategy implementations: buy_and_hold,
  sealed_accumulation, dip_buyer, momentum, cost_aware_reversion. All four
  active strategies are in PORTFOLIO_SAFE_STRATEGIES (support `--portfolio`
  exit signals against a real ledger and the paper daily loop).
- `src/pkmn_quant/research/` — walk-forward layer: folds, seeded optuna search,
  runner/stitcher, strategy registry, markdown report, `walkforward.json`
  artifacts (the research → live bridge).
- `src/pkmn_quant/live/` — `pkmn signals`: one on_bar at the latest warehouse
  date using the last fold's params from the latest walk-forward artifact;
  markdown + JSON reports that carry the strategy's OOS record.
  `ledger.py`: append-only JSONL trade ledger replayed through the engine
  Portfolio; single source of truth, marks never stored. `notify.py`:
  osascript banners, argv-passing.
- `app/dashboard.py` — Streamlit results explorer (dependency group
  `dashboard`; not mypy'd, not imported by src/ or tests — demo tool only).
- `data/` — gitignored. Contains 874 ingested days (2024-02-08..2026-06-30,
  ~2.9M price rows) plus raw archives. Do not delete; re-ingest is ~40 min.
  `data/portfolio/` holds the gitignored real and paper ledgers.

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
  the buy-and-hold benchmark (+186% for sealed, 2024-03→2026-06), not equities.
- Workflow: feature branch per plan; two-stage review per task; STOP after each
  completed task and explain what/why at intern level; wait for the user's
  explicit green light before the next task.
