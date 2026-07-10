# pkmn_quant — an event-driven backtester for Pokemon card markets

A quant research system for TCGplayer card prices: custom event-driven
backtest engine with realistic card-market execution costs, three
parameterized strategies, optuna walk-forward validation, live signal
generation, reinvest loop with portfolio ledger and daily scheduling, and a
Streamlit results explorer. Python 3.12, polars, strict mypy, 213 tests, CI.

**The honest headline:** across 2024-08 → 2026-06, none of the three active
strategies beat buy-and-hold sealed product (+151% out-of-sample). The
system's value is that it can prove that honestly — walk-forward
out-of-sample testing, transaction-cost realism, and an explicit
overfitting measurement.

## Results (walk-forward, out-of-sample only)

| Strategy            | Stitched OOS total | Mean OOS CAGR | Overfitting gap |
|---------------------|-------------------:|--------------:|----------------:|
| buy-and-hold sealed | **+151.1%**        | —             | —               |
| sealed-accumulation | +13.6%             | +8.7%         | +4.8 pts        |
| xs-momentum         | −11.0%             | −4.1%         | +4.7 pts        |
| dip-buyer           | −9.3%              | −5.0%         | +0.3 pts        |

11 folds each: optimize 180 days in-sample, freeze params, test 60 days
out-of-sample, roll, stitch the OOS segments. The overfitting gap
(mean IS CAGR − mean OOS CAGR) is reported on every run. Full findings and
caveats: [docs/research-findings-2026-07.md](docs/research-findings-2026-07.md).

## Why the numbers are believable

- **No look-ahead by construction:** strategies receive a `Context` (history
  up to today, positions, cash) and cannot tell backtest from live mode.
- **Card-market execution realism:** T+1 fills, ~12.75% sell fees + shipping,
  integer quantities, per-day liquidity caps tiered by price, no shorting.
  Round-trip friction is ~15% — most naive strategies lose to it, and the
  [findings](docs/research-findings-2026-07.md) say so.
- **Walk-forward only:** the headline equity curve contains zero in-sample
  days. Parameters are chosen by seeded optuna on each in-sample window and
  frozen before touching out-of-sample data.
- **Stated limitations:** ~2.4 years of data, one bull regime for sealed;
  Sharpe/Sortino inflated by thin-market mark smoothing (documented in every
  report); stitched seams assume mark-value carryover without liquidation
  costs. Methodology over significance.

## Architecture

    tcgcsv.com daily archives
        │  pkmn ingest (quality gates -> quarantine, never silent drops)
        ▼
    Parquet warehouse (DuckDB-queryable)          src/pkmn_quant/data/
        │
        ▼
    Event-driven engine: daily bars -> Context    src/pkmn_quant/engine/
    -> Strategy.on_bar -> orders -> T+1 fill
    simulator -> portfolio -> metrics
        │
        ├── strategies/  sealed_accumulation, dip_buyer, momentum, buy_and_hold
        ├── research/    folds -> seeded optuna search -> walk-forward
        │                runner/stitcher -> registry -> reports + artifacts
        └── live/        pkmn signals: same Strategy, latest data,
                         recommendations with the strategy's OOS record

## Quickstart

    uv sync
    uv run pytest                                        # 213 tests
    uv run pkmn ingest --start 2024-02-08 --end 2026-06-30   # ~40 min, ~2.9M rows
    uv run pkmn backtest --start 2024-03-01 --end 2026-06-30 # benchmark
    uv run pkmn walkforward --strategy sealed-accumulation \
        --start 2024-03-01 --end 2026-06-30 --trials 15      # minutes
    uv run pkmn signals --strategy sealed-accumulation       # today's entries
    uv run pkmn portfolio deposit --amount 1000              # seed the ledger
    uv run pkmn portfolio buy --product-id ... --qty ... --price ...  # record a buy
    uv run pkmn portfolio show                               # positions + P&L
    uv run pkmn signals --strategy sealed-accumulation --portfolio  # entries + exits
    uv run pkmn daily --skip-ingest                          # full loop, offline
    uv run --group dashboard streamlit run app/dashboard.py  # explorer

### Scheduling the daily loop (macOS)

    sed "s|REPO_PATH|$(pwd)|" scripts/com.pkmn-quant.daily.plist \
        > ~/Library/LaunchAgents/com.pkmn-quant.daily.plist
    launchctl load ~/Library/LaunchAgents/com.pkmn-quant.daily.plist

Runs `pkmn daily` at 09:00 (or on next wake, if the Mac was asleep at 09:00):
ingests new prices, runs signals against your ledger (`pkmn portfolio ...`),
and sends a macOS notification when there is something to act on, or when the
run fails.

Before committing real cash, run the loop with `--paper` first
(`uv run pkmn daily --skip-ingest --paper`, or a second launchd job pointing
at the same repo).  Paper mode routes all ledger reads and writes to
`data/portfolio/paper.jsonl`, auto-records fills using the same CostModel
as the backtester (shipping, marketplace fee, per-day liquidity cap), and
labels every output surface PAPER — the dashboard alerts strip, notification
titles, and the `daily-{date}-paper/` artifact directory.  Use it to watch
the strategy trade fake money through the identical pipeline before you act on
any real recommendation.

Troubleshooting:

    launchctl start com.pkmn-quant.daily                        # fire immediately to test
    launchctl list | grep pkmn-quant                            # loaded? last exit code?
    launchctl unload ~/Library/LaunchAgents/com.pkmn-quant.daily.plist   # required before re-loading an edited plist

## Engineering

- `uv` everything; `ruff` lint+format; `mypy --strict` on `src/`; pytest.
- Golden regression test pins exact engine numbers; CI (`uv sync --frozen`)
  fails loudly on any drift.
- Frozen dataclasses for value objects; every backtest `Result` carries its
  cost model, so a run's assumptions travel with its numbers.

## Future work

- Multi-marketplace data (eBay, PSA-graded)
- ML strategies
- Docker
- Short-horizon strategies + entry-date exits (Plan 6, planned)
