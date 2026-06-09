# Pokemon Card Algo Trading System — Design

**Date:** 2026-06-09
**Status:** Approved
**Working name:** `pkmn_quant`

## Goal

A backtesting and signal-generation system for Pokemon card prices (singles and
sealed products), built with the rigor of an equities algo-trading project but
modeling the real economics of the TCG market. Two purposes, in priority order:

1. **Resume value** for SWE roles at both FAANG/big tech and fintech/trading
   firms: a custom event-driven backtest engine with realistic execution
   modeling and walk-forward validation (the fintech story), built as a
   cleanly layered, strictly typed, heavily tested package with CI (big tech, strong engineering hygiene). Both audiences are primary.
2. **Personal investing signals**: the same strategies run in live mode against
   fresh daily data and emit buy/sell/hold recommendations the user may act on
   manually.

## Data

- **Source:** tcgcsv.com daily archives (TCGplayer data), available back to
  February 2024. Pokemon is category 3. Each daily archive yields per-product
  rows with `lowPrice`, `midPrice`, `highPrice`, `marketPrice`, `subTypeName`.
  The existing `data/MEAscendedHeroesProductsAndPrices-2.csv` is one such
  single-day snapshot for one set.
- **Universe:** singles AND sealed products across recent sets (Feb 2024
  onward). Sealed vs. single classified via heuristics (`extRarity` is null
  for sealed; names containing "Booster", "Collection", "Elite Trainer Box",
  etc.) and stored as a `kind` column.
- **Storage:** Parquet dataset partitioned by date, queried via DuckDB.
  Two tables:
  - `products(product_id, set_id, name, rarity, kind)` — dimension table
  - `prices(date, product_id, sub_type, low, mid, high, market)` — fact table
- **Ingestion:** `pkmn ingest` downloads archives for missing dates, extracts
  tracked sets, appends to Parquet.
- **Quality gates at ingest:** missing dates, vanished products, null
  `marketPrice`, >10x day-over-day price jumps. Bad rows are quarantined with
  a reason code, never silently dropped.

## Architecture

Single installable Python package, layered so each layer depends only on the
one below it:

```
pkmn-stock/
├── pyproject.toml          # uv-managed
├── src/pkmn_quant/
│   ├── data/               # tcgcsv ingestion → Parquet/DuckDB warehouse
│   ├── engine/             # event-driven backtester
│   │   ├── clock.py        #   daily bar iterator
│   │   ├── strategy.py     #   Strategy ABC: on_bar(ctx) → orders
│   │   ├── execution.py    #   fill simulator: spread, fees, liquidity caps
│   │   ├── portfolio.py    #   positions, cash, P&L accounting
│   │   └── backtest.py     #   wires it together, produces Result
│   ├── strategies/         # concrete strategies
│   ├── research/           # walk-forward harness, optuna search, quantstats
│   ├── live/               # signal runner → recommendation report
│   └── cli.py              # Typer CLI: ingest / backtest / walkforward / signals
├── app/dashboard.py        # Streamlit results explorer
├── tests/
└── .github/workflows/ci.yml
```

**Core invariant:** the `Strategy` interface is identical in backtest and live
mode. A strategy receives a `Context` (price history up to "today", current
positions, cash) and emits orders/signals; it cannot tell which mode it runs
in. This makes look-ahead bias structurally impossible and is the project's
central design talking point (dependency inversion).

## Engine

**Event loop (daily bars):** for each trading day — (1) mark portfolio to
market, (2) build `Context` with history up to today only, (3) call
`strategy.on_bar(ctx)`, (4) route orders to the execution simulator,
(5) record equity snapshot. Single-threaded and readable; performance comes
from Polars-backed history views.

**Execution simulator — card-market realism:**

| Aspect | Model |
|---|---|
| Buy fill | `marketPrice` + flat shipping per order line |
| Sell proceeds | `marketPrice × (1 − fee_rate)` − shipping; fee_rate ≈ 12.75% (TCGplayer 10.25% + ~2.5% processing), configurable |
| Quantities | Integers only |
| Liquidity cap | Max copies per product per day, tiered by price |
| Fill timing | Orders placed day T fill at day T+1 prices (no same-bar fills) |
| Shorting | Rejected by the simulator |

All parameters live in one `CostModel` dataclass serialized into every result,
so each backtest report states its own assumptions. Round-trip friction is
~15%; the design embraces that most naive strategies lose to it — proving that
honestly is more credible than inflated returns.

**Portfolio accounting:** average-cost basis, realized/unrealized P&L, full
trade ledger. Heaviest-tested module (see Testing).

## Strategies

Each ~50–100 lines; the engine does the heavy lifting.

1. **Sealed accumulation** — buy sealed after the post-release trough
   (drawdown-from-release + age entry rule); exit on target multiple or
   holding period.
2. **Cross-sectional momentum (singles)** — rank chase-rarity singles by
   trailing N-week return, hold top decile, rebalance monthly.
3. **Mean reversion on hype spikes** — fade (or follow) cards up >X% in a
   week; doubles as a momentum-continuation test.
4. **Buy-and-hold benchmark** — equal-weight the set at release. Every
   strategy is judged against this; "beat just-buying-boxes" is the bar.

## Walk-forward analysis

Rolling windows: optimize parameters with **optuna** on a 6-month in-sample
window, freeze, run 2 months out-of-sample, roll, stitch out-of-sample
segments into one equity curve. ~28 months of data → 8–10 folds. Reports show
in-sample vs. out-of-sample side by side; the gap is the overfitting
measurement. **quantstats** generates industry-standard tearsheets (Sharpe,
Sortino, max drawdown, win rate).

**Stated limitation:** ~2.5 years across a few dozen sets is a small sample.
The project claims rigorous methodology on limited data, not statistical
significance — and says so in the README.

## Live signals

`pkmn signals`: ingest latest data, run enabled strategies in live mode,
output a ranked recommendation report (Markdown to stdout + JSON artifact):
product, action, strategy reasoning, current market price, hypothetical
position size. Each recommendation carries its strategy's out-of-sample
walk-forward record. Scheduling (cron/GitHub Actions) is future work.

## Dashboard

Thin Streamlit app (~200 lines): equity curves vs. benchmark, walk-forward
fold table, trade ledger browser, per-product price charts, current signals.
Exists for demos and README screenshots, not as a product.

## Testing & engineering hygiene

- **Tooling:** `uv`, `ruff` (lint+format), `mypy --strict` on the engine,
  `pytest` with coverage gate.
- **Property tests (hypothesis)** on portfolio invariants, e.g.
  `cash + position value == initial capital + realized P&L − costs`.
- **Golden-file regression tests:** tiny fixture dataset in-repo with full
  backtest results snapshotted; any engine change that alters numbers fails
  CI loudly.
- **CI:** GitHub Actions — lint, typecheck, test on every push; coverage
  badge.
- **README:** written for a 90-second hiring-manager skim — architecture
  diagram, tearsheet screenshot, honest friction narrative, quickstart.

## Out of scope (v1)

Docker, scheduled cloud runs, ML-based strategies, multi-marketplace data
(eBay, PSA-graded), automated order placement. Listed as future work in the
README.

## Decisions log

- Custom event-driven engine over vectorbt/backtrader: off-the-shelf engines
  assume shortable, liquid, penny-spread equities; building the engine is the
  resume centerpiece for SWE roles.
- Realistic long-only simulation (~90% of focus) + live recommendation mode
  (~10%) sharing one strategy interface.
- DuckDB + Parquet over a database server: zero infra, industry-relevant.
- Data engineering depth (orchestrators, dbt) intentionally out of scope.
