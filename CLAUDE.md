# pkmn_quant — Pokemon card backtesting system

Algo-trading research system for Pokemon card prices (TCGplayer data via
tcgcsv.com). Design spec: `docs/superpowers/specs/2026-06-09-pkmn-quant-design.md`.

## Status (2026-07-03)

- Plan 1 (data layer) and Plan 2 (backtest engine) are merged to main. 85 tests.
- Plan 3 (research layer) is written, NOT yet executed:
  `docs/superpowers/plans/2026-07-03-research-layer.md` — complete code in every
  task step; execute with subagent-driven development on a `feat/research-layer`
  branch.
- Plan 4 (live signals + Streamlit dashboard + README) comes after Plan 3.

## Commands

```bash
uv sync                      # install (uv manages everything; never pip)
uv run pytest                # test suite
uv run ruff check . && uv run ruff format --check . && uv run mypy
                             # lint + format + strict typecheck (src/ only)
uv run pkmn ingest --start 2026-07-01 --end 2026-07-31   # extend price history
uv run pkmn backtest --start 2024-03-01 --end 2026-06-30 # benchmark backtest
```

All four gates must pass before every commit. CI runs them with
`uv sync --frozen` (stale uv.lock fails the build — commit pyproject.toml and
uv.lock together).

## Layout

- `src/pkmn_quant/data/` — tcgcsv client, transforms, quality gates, Parquet/
  DuckDB warehouse (`Warehouse.query()` gives SQL over `prices`/`products`).
- `src/pkmn_quant/engine/` — event-driven backtester: costs, portfolio, data
  view, execution, strategy ABC, metrics, backtest loop. T+1 fills, long-only.
- `src/pkmn_quant/strategies/` — Strategy implementations (buy_and_hold so far).
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
