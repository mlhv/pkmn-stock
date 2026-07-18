# pkmn_quant — an event-driven backtester for Pokemon card markets

A quant research system for TCGplayer card prices: custom event-driven
backtest engine (Python reference + a bit-for-bit-parity C++ engine) with
realistic card-market execution costs (including a walk-the-spread
market-impact model), five parameterized strategies, optuna walk-forward
validation, live signal generation, reinvest loop with portfolio ledger and
daily scheduling, an experiment registry, and a Streamlit results explorer.
Python 3.12, polars, scikit-learn, C++20/nanobind, strict mypy, 328 tests
(plus 3 dashboard tests behind an opt-in dependency group) + 23 Catch2
tests, CI.

**The honest headline:** across 2024-08 → 2026-06, none of the active
strategies beat buy-and-hold sealed product (+151% out-of-sample,
flat-cost). With the market-impact cost model on (the current CLI default),
it gets worse for active strategies: both previously-positive results
(sealed-accumulation +13.6%, ml-ranker +6.0%) flip negative OOS
(−7.4%, −7.5%) once trades are priced against the book, while buy-and-hold
sealed barely moves (+186.0% → +183.7% over the full backtest window). The
system's value is that it can prove results honestly: walk-forward
out-of-sample testing, transaction-cost and market-impact realism, an
explicit overfitting measurement, and an experiment registry that makes
every number reproducible from its config hash.

## Results (walk-forward, out-of-sample only)

Two regimes: **flat-cost** (2026-07-11 runs, no market-impact model) and
**impact-on** (2026-07-14 runs, the walk-the-spread model, now the CLI
default). Prior numbers and full detail remain in the findings doc; run IDs
for the impact-on row are in `data/runs/registry.jsonl` and cited in the
findings doc's Plan 9 section.

| Strategy             | Stitched OOS, flat-cost | Stitched OOS, impact-on |
|----------------------|-------------------------:|--------------------------:|
| buy-and-hold sealed  | **+151.1%**              | *(not re-run walk-forward; full-period backtest +186.0% → +183.7%)* |
| sealed-accumulation  | +13.6%                   | **−7.4%**                  |
| ml-ranker            | +6.0%                    | **−7.5%**                  |
| dip-buyer            | −9.0%                    | *(not re-run under impact)* |
| cost-aware-reversion | −10.2%                   | *(not re-run under impact)* |
| xs-momentum          | −25.1%                   | *(not re-run under impact)* |

11 folds each: optimize 180 days in-sample, freeze params, test 60 days
out-of-sample, roll, stitch the OOS segments. The overfitting gap
(mean IS CAGR − mean OOS CAGR) is reported on every run. Impact costs walk
the fill price from `market` toward the day's `mid` (buys) or `low` (sells),
scaled by order size against the daily liquidity cap; opt out per-command
with `--no-impact`. Full findings and caveats:
[docs/research-findings-2026-07.md](docs/research-findings-2026-07.md).

## Why the numbers are believable

- **No look-ahead by construction:** strategies receive a `Context` (history
  up to today, positions, cash) and cannot tell backtest from live mode.
- **Card-market execution realism:** T+1 fills, ~12.75% sell fees + shipping,
  integer quantities, per-day liquidity caps tiered by price, no shorting,
  and (on by default for `backtest`/`walkforward`/`daily`, opt out with
  `--no-impact`) a walk-the-spread market-impact model: buys walk from
  `market` toward `mid`, sells from `market` toward `low`, scaled by order
  size against the liquidity cap. Round-trip friction is ~15% before impact
  — most naive strategies lose to it, and the
  [findings](docs/research-findings-2026-07.md) say so; with impact on, both
  previously-positive active strategies flip negative OOS.
- **Reproducible by construction:** every `backtest`/`walkforward` run
  appends a record (config hash, git SHA+dirty, data fingerprint, results)
  to the experiment registry (`data/runs/registry.jsonl`), inspectable via
  `pkmn runs list`/`pkmn runs show <run-id>`.
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
        │                runs.py: experiment registry (data/runs/registry.jsonl)
        └── live/        pkmn signals: same Strategy, latest data,
                         recommendations with the strategy's OOS record

    engine/quotes.py: per-day Quote (mid/low) feeding the walk-the-spread
    market-impact cost model (engine default off, CLI default on)

## Engines

Two backtest engines produce the identical `Result` (equity curve + fills):
the Python reference engine (`engine/backtest.py`, always available) and a
C++ engine (`cpp/`, nanobind-bound as `pkmn_quant._engine`) with native ports
of all five strategies. Select it per-run with `--engine cpp` on
`backtest`/`walkforward`; a `NativeStrategySpec` not among the five native
names (or any raw Python `Strategy` instance) still runs correctly on the
C++ event loop via a per-bar callback bridge, just without the native
strategy speedup.

**The parity guarantee is bit-for-bit, not "close enough":** every fill's
day/asset/quantity/price/fees/impact and every equity-curve value must match
exactly (`==`), not within a tolerance. This is enforced three ways —
Catch2 unit tests in `cpp/tests/` for the C++ core in isolation, differential
tests in `tests/test_native_parity.py` (synthetic fixtures, both engines,
exact comparison) for every strategy, and `scripts/parity_full.py` for the
acceptance bar: all five strategies plus the ml-ranker bridge, bit-for-bit,
over the real 874-day warehouse. Run it yourself after any C++ or strategy
change:

    uv run python scripts/parity_full.py        # five rule strategies, ~1 min
    uv run python scripts/parity_full.py --ml    # + ml-ranker bridge, ~2-3 min (sklearn trains in-loop, twice)

Build prerequisites: `uv sync` builds and installs the extension
automatically (scikit-build-core + nanobind, CMake ≥3.26 wired through
`pyproject.toml`). For local C++ iteration — editing `cpp/` and running the
Catch2 suite directly — you need Xcode Command Line Tools (or any C++20
compiler) and CMake on `PATH`:

    cmake -S cpp -B cpp/build -DPKMN_BUILD_TESTS=ON && cmake --build cpp/build -j
    ctest --test-dir cpp/build --output-on-failure

**Measured speedup** (best of 3, full 2024-03..2026-06 range, impact model
on; both numbers are total wall-clock including the one-time polars
load/flatten the C++ path pays crossing the boundary, not engine-loop-only):

| strategy | python (s) | cpp (s) | speedup |
|---|---|---|---|
| buy-and-hold | 10.34 | 4.37 | 2.4x |
| sealed-accumulation | 11.91 | 3.52 | 3.4x |
| dip-buyer | 27.44 | 3.60 | 7.6x |

Full acceptance results, the discovery that some priced product_ids have no
`products.parquet` catalog row (40 within the backtest window; 1,845
warehouse-wide as of this run — see the findings doc for the two distinct
causes) and how the C++ engine now handles that, and what the speedup
unlocks: `docs/research-findings-2026-07.md` (Plan 10 section).

**Fold-level parallelism for `walkforward`:** `--workers` controls it (`0`
= auto, `min(folds, cores)`; `1` = serial; `N` = `N` threads); both CLI
defaults are `--engine cpp --workers 0`, so a bare `pkmn walkforward` run is
already the fast, parallel path. Results are bit-identical at any worker
count — each fold's optuna study is independent and seeded, and the native
engine now genuinely releases the GIL during its per-fold run. Measured on
the real warehouse (`scripts/bench_walkforward.py`, sealed-accumulation,
same range as above): python-serial 359.5s vs cpp-serial 20.0s vs
cpp-workers=auto 20.5s — the 18x win is almost entirely the native engine,
not threads; fold-level parallelism itself measured no gain on this
workload (see the Plan 11 section of the findings doc for why). Use
`--engine python --workers 1` for the pre-Plan-11 reference behavior.

## Quickstart

    uv sync
    uv run pytest                # 328 tests (3 dashboard tests skip without --group dashboard)
    uv run pkmn ingest --start 2024-02-08 --end 2026-06-30   # ~40 min, ~2.9M rows
    uv run pkmn backtest --start 2024-03-01 --end 2026-06-30 # benchmark (impact model on by default)
    uv run pkmn backtest --start 2024-03-01 --end 2026-06-30 --no-impact  # flat-cost, no market impact
    uv run pkmn backtest --start 2024-03-01 --end 2026-06-30 --engine cpp # same result, native C++ engine
    uv run pkmn walkforward --strategy sealed-accumulation \
        --start 2024-03-01 --end 2026-06-30 --trials 15      # cpp engine, fold-parallel auto (both defaults)
    uv run pkmn walkforward --strategy sealed-accumulation \
        --start 2024-03-01 --end 2026-06-30 --trials 15 \
        --engine python --workers 1                          # reference behavior: serial, Python engine
    uv run pkmn signals --strategy sealed-accumulation       # today's entries
    uv run pkmn runs list                                     # experiment registry: recorded runs
    uv run pkmn runs show <run-id>                            # full record for one run
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
