# pkmn_quant — Pokemon card backtesting system

Algo-trading research system for Pokemon card prices (TCGplayer data via
tcgcsv.com). Design spec: `docs/superpowers/specs/2026-06-09-pkmn-quant-design.md`.

## Status (2026-07-05)

- Plans 1-4 are merged to main. 152 tests. Plan 4 closed out the v1 spec:
  `walkforward.json` artifacts, `pkmn signals` (live recommendations carrying
  the strategy's OOS record), the Streamlit dashboard, and the README.
- Walk-forward findings for the README live in
  `docs/research-findings-2026-07.md` — headline: nothing beat buy-and-hold
  sealed (+151% over the OOS span); sealed-accumulation +13.6% stitched OOS
  was the only positive strategy.
- Next: future-work backlog (scheduled ingestion/signals, live position
  tracking, more data sources) — no active plan.

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
  sealed_accumulation, dip_buyer, momentum.
- `src/pkmn_quant/research/` — walk-forward layer: folds, seeded optuna search,
  runner/stitcher, strategy registry, markdown report, `walkforward.json`
  artifacts (the research → live bridge).
- `src/pkmn_quant/live/` — `pkmn signals`: one on_bar at the latest warehouse
  date using the last fold's params from the latest walk-forward artifact;
  markdown + JSON reports that carry the strategy's OOS record.
- `app/dashboard.py` — Streamlit results explorer (dependency group
  `dashboard`; not mypy'd, not imported by src/ or tests — demo tool only).
- `data/` — gitignored. Contains 874 ingested days (2024-02-08..2026-06-30,
  ~2.9M price rows) plus raw archives. Do not delete; re-ingest is ~40 min.

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
