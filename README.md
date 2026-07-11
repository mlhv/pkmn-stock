# pkmn_quant — an event-driven backtester for Pokemon card markets

A quant research system for TCGplayer card prices: custom event-driven
backtest engine with realistic card-market execution costs, five
parameterized strategies, optuna walk-forward validation, live signal
generation, reinvest loop with portfolio ledger and daily scheduling, and a
Streamlit results explorer. Python 3.12, polars, scikit-learn, strict mypy,
267 tests (plus 3 dashboard tests behind an opt-in dependency group), CI.

**The honest headline:** across 2024-08 → 2026-06, none of the five active
strategies beat buy-and-hold sealed product (+151% out-of-sample). ml-ranker
is the first active strategy besides sealed-accumulation with a positive
stitched OOS return (+6.0%), but +6.0% vs +151.1% is not close. The system's
value is that it can prove results honestly: walk-forward out-of-sample
testing, transaction-cost realism, and an explicit overfitting measurement.

## Results (walk-forward, out-of-sample only)

Numbers from 2026-07-11 runs. Prior numbers remain in the findings doc.

| Strategy             | Stitched OOS total | Mean OOS CAGR | Overfitting gap |
|----------------------|-------------------:|--------------:|----------------:|
| buy-and-hold sealed  | **+151.1%**        | —             | —               |
| sealed-accumulation  | +13.6%             | +8.7%         | +4.8 pts        |
| ml-ranker            | +6.0%              | +5.2%         | +7.8 pts        |
| dip-buyer            | −9.0%              | −4.8%         | −0.4 pts        |
| cost-aware-reversion | −10.2%             | −5.3%         | +1.7 pts        |
| xs-momentum          | −25.1%             | −10.1%        | +12.9 pts       |

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
        ├── strategies/  sealed_accumulation, dip_buyer, momentum,
        │                cost_aware_reversion, ml_ranker (learned
        │                gradient-boosted ranking, trained in-loop behind
        │                the anti-look-ahead wall), buy_and_hold
        ├── research/    folds -> seeded optuna search -> walk-forward
        │                runner/stitcher -> registry -> reports + artifacts
        │                features.py: 8 leakage-bounded features (scikit-learn)
        └── live/        pkmn signals: same Strategy, latest data,
                         recommendations with the strategy's OOS record

## Quickstart

    uv sync
    uv run pytest                # 267 tests (3 dashboard tests skip without --group dashboard)
    uv run pkmn ingest --start 2024-02-08 --end 2026-06-30   # ~40 min, ~2.9M rows
    uv run pkmn backtest --start 2024-03-01 --end 2026-06-30 # benchmark
    uv run pkmn walkforward --strategy sealed-accumulation \
        --start 2024-03-01 --end 2026-06-30 --trials 15      # minutes
    uv run pkmn signals --strategy sealed-accumulation       # today's entries
    uv run pkmn portfolio deposit --amount 1000              # seed the ledger
    uv run pkmn portfolio buy --product-id ... --qty ... --price ...  # record a buy
    uv run pkmn portfolio show                               # positions + P&L
    uv run pkmn signals --strategy sealed-accumulation --portfolio  # entries + exits
    uv run pkmn signals --strategy cost-aware-reversion --portfolio  # cost-hurdle strategy
    uv run pkmn daily --skip-ingest                          # full loop, offline
    uv run --group dashboard streamlit run app/dashboard.py  # explorer (Portfolio tab: Real/Paper toggle)

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
titles, and the `daily-{date}-paper/` artifact directory.  Fill counts in
`daily.json` reflect recorded fills only (after liquidity and affordability
clipping, not raw recommendations), so a paper day where every order clips to
zero sends no notification.  Use it to watch the strategy trade fake money
through the identical pipeline before you act on any real recommendation.
All five strategies (sealed-accumulation, dip-buyer, cost-aware-reversion,
xs-momentum, ml-ranker) are portfolio-safe: each supports `--portfolio` for
exit signals against a real ledger and works with the paper daily loop.

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
- Docker
