# Walk-forward findings — 2026-07-04

**Catalog-completeness note (2026-07-29/30), read before anything below.**
Every section in this file above the "Catalog completeness" section near the
end was computed against a product catalog that only covered 23 of tcgcsv's
217 groups (see that section for the full story and the real numbers). The
short version: the fix corrected 2,009 products warehouse-wide, but only 32
of those sit inside the standard 2024-03-01..2026-06-30 backtest window used
throughout this file (all 32 `single`, zero `sealed`) — the other ~1,978 only
started pricing in July 2026, after this window ends. On this window the
fix is immaterial for every strategy with a hard `kind` filter (buy-and-hold
sealed and sealed-accumulation are bit-for-bit unchanged to the full float;
dip-buyer and xs-momentum move about 1 CAGR-point) and provably a no-op for
cost-aware-reversion (it never reads `kind` at all). It is **not**
immaterial for the two ML strategies: ml-ranker and ml-ranker-v2 have no
hard `kind` filter, so those 32 products were always in their feature
frames — the fix only changed two feature values (`is_sealed`,
`days_since_release`) from missing to real for those rows, and that alone
moved ml-ranker by +2.47 stitched-OOS points and ml-ranker-v2 by −8.43
points, in opposite directions, both confirmed deterministic (bit-identical
across independent re-runs, not noise). **None of the numbers below are
wrong or need to be distrusted** — they were computed correctly against the
catalog that existed at the time, and for every strategy except the two ML
ones the correction turns out to be immaterial on this window; they remain
the numbers to cite for 2024-03-01..2026-06-30. The ml-ranker and
ml-ranker-v2 sections below, and their appearances in the Rigor pack /
evaluate tables, are the ones genuinely superseded by the new section at the
end. The catalog fix matters more for any future window extending past
2026-06-30, where the bulk of the 1,978 newly-priced products actually live.

Runs: `pkmn walkforward --start 2024-03-01 --end 2026-06-30 --is-days 180
--oos-days 60 --trials 15 --seed 42` (CLI default `--warmup-days 120`),
11 folds per strategy, OOS period 2024-08-28 .. 2026-06-18. Benchmark:
`pkmn backtest --start 2024-08-28 --end 2026-06-30 --cash 10000 --kind sealed`.
Artifacts (gitignored): `data/results/wf-*-2024-03-01-2026-06-30/`.

## Headline

**No active strategy came close to buy-and-hold sealed over this period.**
Sealed product rose almost monotonically 2024-08 .. 2026-06; any strategy
that holds cash between entries or takes profit early pays dearly for it.

| Strategy            | Stitched OOS total | OOS CAGR (mean) | IS CAGR (mean) | Overfitting gap |
|---------------------|-------------------:|----------------:|---------------:|----------------:|
| buy-and-hold sealed | **+151.1%**        | —               | —              | —               |
| sealed-accumulation | +13.6%             | +8.7%           | +13.4%         | +4.8 pts        |
| xs-momentum         | −11.0%             | −4.1%           | +0.6%          | +4.7 pts        |
| dip-buyer           | −9.3%              | −5.0%           | −4.7%          | +0.3 pts        |

## Per-strategy notes

- **sealed-accumulation** — the only profitable active strategy OOS
  (+13.6% stitched, positive in 8/11 folds, max drawdown −2.2%). Optuna
  repeatedly pushed `min_drawdown` to the bottom of its range (~0.10) and
  `take_profit` to the top (~2.5) in mid-2025 folds: in a trending market
  the optimizer tries to turn the strategy into buy-and-hold, but the 10%
  per-name budget and profit-taking cap the upside far below the benchmark.
- **xs-momentum** — negative OOS (−11.0% stitched). Trailing-return
  winners in singles did not persist over 2024-2026 at 60d/30d timescales,
  and the ~12-15% round-trip cost of TCGplayer fees + shipping eats any
  residual signal. Worst fold: −10.9% (2026-04 .. 2026-06).
- **dip-buyer** — consistently unprofitable in-sample AND out-of-sample
  (−9.3% stitched; IS mean CAGR −4.7%). Sharp one-week dips in singles do
  not mean-revert enough to cover transaction costs; the near-zero
  overfitting gap just means there was no edge to overfit.

## Plan 6 re-runs — 2026-07-10

All three active strategies re-run after the `opened_on` retrofit (Plan 6) and
the first run of cost-aware-reversion.  Old 2026-07-04 numbers remain in the
Headline table above as the historical record; new numbers are here.

### Why the numbers changed for dip-buyer and xs-momentum

The `opened_on` field on `Position` fixed two classes of bugs that affected
both strategies — these are **documented bug fixes, not tuning changes**; old
numbers measured buggy strategies:

1. **Hold/rebalance clocks** previously started at order *emission* (T+0), not
   at the actual T+1 fill.  Both strategies are now stateless on `opened_on`
   (a field on the position itself) rather than derived from an internal clock
   seeded at emission.  In the backtester the difference is one day per
   position; in live mode the old code could drift arbitrarily if a daily loop
   was paused.
2. **Dip-buyer orphan bug** — when a buy order partially filled (or filled on
   the last bar before the hold window expired), the code dumped the position
   immediately every subsequent bar.  The fill-date reanchoring on `opened_on`
   eliminates this.  An emitted-but-unfilled buy no longer blocks re-entry on
   the next bar.
3. **xs-momentum flat-period idle** — when the strategy had no open positions
   it waited out `rebalance_days` doing nothing.  Fixed: when flat, the
   strategy evaluates every bar and can enter immediately.

### Updated numbers

| Strategy                | Stitched OOS total | OOS CAGR (mean) | IS CAGR (mean) | Overfitting gap |
|-------------------------|-------------------:|----------------:|---------------:|----------------:|
| buy-and-hold sealed     | **+151.1%**        | —               | —              | —               |
| sealed-accumulation     | +13.6%             | +8.7%           | +13.4%         | +4.8 pts        |
| xs-momentum (2026-07-10)| −25.1%             | −10.1%          | +2.8%          | +12.9 pts       |
| dip-buyer (2026-07-10)  | −9.0%              | −4.8%           | −5.2%          | −0.4 pts        |
| cost-aware-reversion    | −10.2%             | −5.3%           | −3.5%          | +1.7 pts        |

### Delta analysis

**dip-buyer** is essentially unchanged (−9.3% → −9.0% stitched; gap now ~0).
The bug fixes did not materially alter its trading pattern; the conclusion is
unchanged: no edge to overfit, consistently unprofitable IS and OOS.

**xs-momentum** is markedly worse (−11.0% → −25.1% stitched; gap +4.7 →
+12.9 pts).  The always-evaluate-when-flat and emission-clock fixes cause it
to trade significantly more: previously it idled through dead periods (when
flat it waited out `rebalance_days` doing nothing), suppressing the round-trip
toll.  Every extra rebalance pays the ~12-15% round-trip cost.  The larger
IS/OOS gap (12.9 pts) means the extra optimizer freedom is fitting noise rather
than signal.  The honest conclusion: the old, better-looking number was an
artifact of bugs that suppressed trading.  xs-momentum is more decisively
refuted after the fixes.

**cost-aware-reversion** (first run): −10.2% stitched OOS vs buy-and-hold
sealed +151.1% and sealed-accumulation +13.6%.  Negative.  The cost hurdle
(`fee_rate + 2*shipping/price + margin`) correctly excludes untradeable dips,
but what remains still did not revert enough to overcome costs in this period.
Notes from the parameter posteriors:

- Tuned `take_profit=1.51` sits above the round-trip break-even (~1.15-1.4
  depending on price), so the optimizer found the sane region.
- `take_profit` values below ~1.2 would be loss-cutting rather than
  profit-taking; the tuner avoided this.
- At high `min_edge` the `dip_threshold` knob is partially inert (the cost
  hurdle binds first), so the posterior on `dip_threshold` should be
  interpreted cautiously.

Last-fold tuned params (2026-07-09 warehouse date): `dip_window_days=80`,
`dip_threshold=0.1526`, `min_edge=0.04755`, `take_profit=1.51`,
`max_hold_days=101`.  Live smoke (`pkmn signals --strategy cost-aware-reversion
--portfolio`) produced a clean report (no recommendations; real ledger empty).
Dip-buyer live smoke likewise clean with tuned params `dip_threshold=0.1031`,
`hold_days=90`, `take_profit=1.453`.

### Sharpe/Sortino caveat

Applies here as in the original runs: Sharpe/Sortino numbers are inflated by
mark smoothing (thin markets, carry-forward marks).  Compare strategies to
each other and to buy-and-hold only.

### Framing

Report negative results as negative.  The success criterion for Plan 6 is a
usable short-horizon tool plus an honest record, not beating buy-and-hold.
The deliverable is that hold-day exits now run identically in backtests and
against the real ledger, and that all four strategies are portfolio-safe (usable
with `pkmn signals --portfolio` and `pkmn daily --paper`).

## Overfitting gap

Positive as expected for the two strategies with tunable edge
(sealed +4.8 CAGR pts, momentum +4.7 pts): parameters look better on the
window they were fit on. The gap is computed on CAGR (annualized) so the
180d IS vs 60d OOS length mismatch does not fabricate a gap; CAGR over 60d
windows is noisy, so treat the gap as an order-of-magnitude signal.

## Plan 8: ml-ranker — 2026-07-11

Run: `uv run pkmn walkforward --strategy ml-ranker --start 2024-03-01
--end 2026-06-30 --trials 15`, 11 folds, ~22 min wall clock.
OOS span: 2024-08-28 .. 2026-06-18.

### Results

| Metric                  | Value         |
|-------------------------|---------------|
| Stitched OOS total      | +6.0%         |
| Stitched CAGR           | +3.3%         |
| Stitched max drawdown   | −8.4%         |
| Stitched Sharpe         | 0.95          |
| Stitched Sortino        | 1.33          |
| Stitched Calmar         | 0.39          |
| IS total return (mean)  | +6.0%         |
| OOS total return (mean) | +0.6%         |
| IS CAGR (mean)          | +13.0%        |
| OOS CAGR (mean)         | +5.2%         |
| Overfitting gap         | +7.75 CAGR pts|

Summary table alongside all strategies:

| Strategy             | Stitched OOS total | Mean OOS CAGR | IS CAGR (mean) | Overfitting gap |
|----------------------|-------------------:|--------------:|---------------:|----------------:|
| buy-and-hold sealed  | **+151.1%**        | —             | —              | —               |
| sealed-accumulation  | +13.6%             | +8.7%         | +13.4%         | +4.8 pts        |
| **ml-ranker**        | **+6.0%**          | **+5.2%**     | **+13.0%**     | **+7.8 pts**    |
| dip-buyer            | −9.0%              | −4.8%         | −5.2%          | −0.4 pts        |
| cost-aware-reversion | −10.2%             | −5.3%         | −3.5%          | +1.7 pts        |
| xs-momentum          | −25.1%             | −10.1%        | +2.8%          | +12.9 pts       |

### Gap first

The +7.75 CAGR-point IS/OOS gap is the **second-largest gap in the project**
(xs-momentum's +12.9 pts is worst). IS mean CAGR +13.0% vs OOS mean CAGR
+5.2%: a large share of in-sample performance is noise-fitting.

OOS fold returns are noisy: range −3.2% .. +8.4% across 11 folds; 6 of 11
positive. One fold (2024-12 .. 2025-02, +8.4%) contributes most of the
stitched gain — the positive stitched OOS number is not uniformly distributed
across the backtest period.

### What is positive

ml-ranker is the **first active strategy besides sealed-accumulation with a
positive stitched OOS return** (+6.0%). Tuned rebalance cadences ran 51-87
days across folds: the Optuna tuner consistently avoided fast-churn parameter
regions, consistent with the ~15% round-trip toll that punishes frequent
trading. The strategy's positive result is conditional on using slow cadences.

### vs the stated goal

The goal was to approach buy-and-hold sealed (+151.1%). ml-ranker does not
come close: +6.0% vs +151.1%. It is the second-best active strategy overall
(behind sealed-accumulation +13.6%), but no active strategy approaches holding
sealed in this regime. The honest verdict: this is a failure against the
original benchmark target, and a relative success against the other active
strategies.

### Did it learn "hold sealed"?

Rebuilding the last fold's target on 2026-06-18 with the fold's tuned params
produced 9 buys: 5 singles, 4 sealed. The strategy uses both card kinds; it
did not simply rediscover buy-and-hold-sealed, nor did it ignore sealed.
Allocation breadth is genuine, not a degenerate single-asset collapse.

### sklearn all-NaN bug (research infrastructure finding)

During the initial research run, sklearn 1.9's histogram binner crashed with
"window shape cannot be larger than input array shape" on all-NaN feature
columns. Early folds legitimately produce these: `ret_90d` is null everywhere
when the warehouse is younger than 90 days at the training date — a correct
consequence of the leakage-safe label design, not a data error. Fixed
in-strategy by fitting and predicting on the not-all-null feature subset per
call (commit ca77f47). The leakage-safe architecture surfaced the bug precisely
because it does not silently fill or forward-fill early history.

### Caveats

- Sharpe/Sortino inflated by mark smoothing (thin markets, carry-forward
  marks) — same caveat as all other strategies.
- ~2.4 years, one bull regime for sealed; these results generalize to this
  regime only.
- 15 Optuna trials is a small search (TPE uses 10 random startup trials before
  Bayesian steps). The gap could worsen with a wider search that finds higher
  IS peaks.

### Live smokes (2026-07-11)

- `pkmn signals --strategy ml-ranker --portfolio`: clean, no recommendations;
  real ledger empty, $0 cash.
- `pkmn daily --skip-ingest --paper --strategy ml-ranker`: clean, status ok,
  n_buys 0 — paper portfolio's newest position opened 2026-07-10, tuned
  rebalance 65d, not yet due. Honest-count machinery from Plan 7 worked
  correctly.

### Engine performance (Plan 8 profiling)

180-day backtest wall time: 3.84s → ~2.0s (~1.9x) after adding a marks cursor
and date partition. The bottleneck was per-day dict building, not parquet
re-read (parquet read: 0.15s).

## Plan 9: walk-the-spread impact — 2026-07-14

Runs: `pkmn backtest --start 2024-03-01 --end 2026-06-30` (buy-and-hold
sealed) and `pkmn walkforward --strategy {sealed-accumulation,ml-ranker}
--start 2024-03-01 --end 2026-06-30 --trials 15`, all with the impact model
ON — the new CLI default as of this branch. Buys walk the quoted price from
`market` toward `mid` (the day's median listing) and sells walk from `market`
toward `low`, scaled by `qty / (2 * daily_liquidity_cap)`; `--no-impact`
restores the old flat-cost behavior. Every run recorded to
`data/runs/registry.jsonl`; run IDs below are the first provenance citations
from that registry in this document.

### Results: without impact (prior sections) vs with impact

| Strategy             | OOS return, flat-cost (prior) | OOS return, impact ON | Run ID (impact-on)         |
|-----------------------|------------------------------:|-----------------------:|-----------------------------|
| buy-and-hold sealed  | +186.0% (backtest total return, 2024-03→2026-06)\* | +183.7% (backtest total return) | `20260714T045104Z-8d084f` |
| sealed-accumulation  | +13.6% (stitched OOS)         | −7.4% (stitched OOS)   | `20260714T045549Z-e0e52c`  |
| ml-ranker            | +6.0% (stitched OOS)          | −7.5% (stitched OOS)   | `20260714T051127Z-0bc0fc`  |

\* The +186.0% flat-cost number is the full-period `pkmn backtest` total
return (2024-03-01 .. 2026-06-30), the same benchmark cited in CLAUDE.md's
conventions section — not the walk-forward stitched-OOS +151.1% figure from
the Headline table above (different window, different metric; both are
buy-and-hold sealed). The impact-on run above is the directly comparable
apples-to-apples figure: same command, same window, impact toggled.

### Hypothesis verdict: confirmed, strongly

The Plan 9 hypothesis was that impact costs would hurt high-turnover
strategies more than buy-and-hold, widening buy-and-hold's lead. The data
confirms this more sharply than expected:

- **Buy-and-hold sealed** trades ~39 times total over the full period (one
  accumulation trickle, essentially buy-and-hold). Impact cost is a rounding
  error against a +186% multi-year move: +186.0% → +183.7%, a 2.3-point
  haircut.
- **sealed-accumulation** (11 folds, previously +13.6% stitched OOS) flips
  to **−7.4%**. This strategy's edge was never free of round-trip friction
  by much margin (+13.6% vs +151.1% benchmark already showed most of the
  benchmark's edge was being given up to cost); the added walk-the-spread
  impact on every rebalance was enough to erase what remained and go
  negative.
- **ml-ranker** (previously +6.0% stitched OOS, the only other positive
  active strategy) also flips to **−7.5%**. Both previously-positive active
  strategies are now negative under impact.

The apparent edge that sealed-accumulation and ml-ranker showed in the
flat-cost regime was, at least in significant part, an artifact of
under-costing trades that walk price against the book. Once that friction is
priced in, **every active strategy in this project is now OOS-negative**;
buy-and-hold sealed is undefeated and its lead widens rather than narrows.

### The overfitting-gap number needs a second look for ml-ranker

ml-ranker's impact-on overfitting gap is 0.33 CAGR-pts — small (about
1/20th of the flat-cost run's 7.75 pts), which would normally read as
"little overfitting, trustworthy result." Do not read it that way here. The
gap is small because **in-sample mean CAGR also went negative** (IS mean
CAGR −3.39%, OOS mean CAGR −3.72%, vs the flat-cost run's IS +13.0% /
OOS +5.2%): the strategy stopped finding a fittable edge in-sample at all
under impact costs, so there was nothing left to overfit.
A shrinking gap is only good news when it comes from OOS catching up to a
positive IS; here it comes from IS collapsing to match a negative OOS (IS
mean total return −1.7%, OOS mean total return −0.7%). Same arithmetic,
opposite story — flag this pattern whenever an overfitting gap looks
unusually good.

sealed-accumulation's gap more than doubled under impact: IS mean CAGR
+9.07% vs OOS mean CAGR −3.16% (gap 12.22 pts, vs +4.8 pts flat-cost) — the
more familiar "in-sample optimism, out-of-sample disappointment" shape,
markedly worse than before because impact now bites every rebalance the
optimizer chose thinking it was free.

### Standing caveats (repeated)

- Sharpe/Sortino/Calmar are inflated by mark smoothing (thin markets,
  carry-forward marks); compare strategies to each other and to buy-and-hold
  only, not to equities benchmarks.
- ~2.4 years of data, one bull regime for sealed; these results say nothing
  about a flat or falling market.
- 15 Optuna trials/fold is closer to random search than full Bayesian
  optimization; a wider search could shift these numbers in either
  direction.
- **New this plan:** the impact model itself is an assumption, not a
  calibration. Linear walk from `market` toward `mid` (buys) or `low`
  (sells), scaled by `qty / (2 * daily_liquidity_cap)`, is a modeling
  choice — it has not been validated against real observed fill prices.
  Treat the impact-on numbers as "directionally more realistic than
  flat-cost," not as a validated forecast of real trading costs.

## Plan 10 (2026-07-14): C++ engine — full-data parity + measured speedup

Task 11 (final task of the C++ engine port, spec `2026-06-09` addendum
`2026-07-14`) ran the acceptance test the whole plan was building toward:
every strategy, both engines, the real 874-day warehouse
(2024-02-08..2026-06-30), bit-for-bit.

### Parity acceptance: all six PASS

`uv run python scripts/parity_full.py [--ml]`, full range
2024-03-01..2026-06-30, 120-day warmup, impact model on, $10,000 initial
cash — every comparison exact (`==` on the full equity curve and every
fill's day/asset/quantity/price/fees/impact):

```
[buy-and-hold] python 9.93s / cpp 3.53s
PASS  buy-and-hold  (39 fills)
[sealed-accumulation] python 10.09s / cpp 3.60s
PASS  sealed-accumulation  (68 fills)
[dip-buyer] python 24.53s / cpp 3.64s
PASS  dip-buyer  (460 fills)
[xs-momentum] python 10.66s / cpp 3.40s
PASS  xs-momentum  (532 fills)
[cost-aware-reversion] python 12.11s / cpp 3.69s
PASS  cost-aware-reversion  (183 fills)
[ml-ranker (bridge)] python 94.59s / cpp 57.01s
PASS  ml-ranker (bridge)  (430 fills)
```

All five native strategies plus the ml-ranker callback bridge (sklearn
trains in-loop, unmodified Python `Strategy`, per-bar callback into the C++
engine) match bit-for-bit on real market data, not just the synthetic
fixtures Tasks 1-10 exercised.

### A real bug the full-data run found (fixed before this acceptance passed)

The first `parity_full.py` run did not produce a PASS/FAIL line at all — it
crashed with `KeyError: 542095` inside `NativeBacktest.run()` on the very
first strategy. Root cause: `products.parquet` is missing rows for 40 of
4,687 priced `product_id`s within the backtest window
(2024-03-01..2026-06-30) — 7,565 price rows, first appearing 2024-03-03.
This is upstream tcgcsv catalog drift, not stale local data — verified two
ways: a live re-fetch of every currently-tracked group's `/products`
endpoint still omits the missing ids, and tracing the raw archive path
confirms at least one (`542095`, `Holofoil`) is priced under a group we
already track (`23353`) whose live catalog no longer lists it.
`NativeBacktest.run()` required a `products.parquet` row for every priced
asset; the Python reference engine never did (strategies build their
universe by filtering/joining against `ctx.products`, so an uncataloged
asset is simply invisible to kind-filtered strategies — buy-and-hold,
sealed-accumulation, dip-buyer, xs-momentum — but still a candidate for
cost-aware-reversion, which has no kind filter at all). Fixed by tagging a
missing catalog row kind "other" (-1), the C++ `ProductTable`'s existing
sentinel for exactly this case (commit `091b663`, plus a differential
regression test that seeds one extra uncataloged asset and proves both
engines exclude it from buy-and-hold and include it, bit-for-bit, in
cost-aware-reversion).

The gap is much larger warehouse-wide than inside this backtest window: as
of this run the warehouse has grown past the window to 890 ingested days
(2024-02-08..2026-07-16), and 1,845 of 6,493 all-time priced `product_id`s
(28%) have no catalog row — 29,443 of the 37,008 orphan rows (80%) are
dated 2026-07 alone. That concentration points to a second, distinct cause
from the 40 in-window drift cases above: the documented Plan 1 limitation
in `ingest.py` (`refresh_products` only runs once, when `products.parquet`
is missing — new sets picked up by daily price ingestion afterward are
never added to the catalog). Worth recording as a standing warehouse fact,
independent of the C++ work: any future code that assumes catalog
completeness needs to handle both failure modes.

### Measured speedup (best of 3, full 874-day range, impact on)

`uv run python scripts/bench_engines.py`:

| strategy | python (s) | cpp (s) | speedup |
|---|---|---|---|
| buy-and-hold | 10.34 | 4.37 | 2.4x |
| sealed-accumulation | 11.91 | 3.52 | 3.4x |
| dip-buyer | 27.44 | 3.60 | 7.6x |

Both timings are total wall-clock (`Backtest.run()` / `NativeBacktest.run()`
end to end), not engine-loop-only — `NativeBacktest` still pays Python/polars
cost to load and flatten the warehouse into numpy arrays once per run before
the C++ loop starts, so the measured speedup understates how much faster the
event loop itself is. That flatten cost is roughly constant per run
(~3-4s here) while the Python reference engine's per-day polars filtering
scales with strategy complexity — dip-buyer's daily dip-window scan is the
most expensive Python path here (27.4s) and shows the largest end-to-end
speedup (7.6x) for exactly that reason; buy-and-hold's cheap per-day Python
work (2.4x) is the closest this table gets to isolating the flatten
overhead.

### What this does and does not change

Research conclusions are **unchanged by construction**: bit-for-bit parity
means every number in every prior section of this document is identical
whichever engine produced it — the C++ engine is not a new result, it is
the same result computed faster. What changed is the cost of producing
those numbers: a full-range backtest that took the Python engine 10-30s per
strategy now completes in 3-4s, and — more importantly for what comes
next — the C++ core has no Python dependency, so a future GIL release for
the native-strategy path (nb::call_guard / gil_scoped_release around the
event loop, not yet added) would let walk-forward folds and optuna trials
run across threads instead of only across processes; the callback bridge,
which calls back into Python per bar, would still need to hold it. Today
nothing in cpp/ releases the GIL — the event loop holds it throughout, same
as the Python engine. That capability is the unlock Plan 11 (parallel
walk-forward search) depends on; this plan didn't need the speed for
anything the numbers above required, but the next one does.

## Plan 11 (2026-07-17): fold-parallel walk-forward

`uv run python scripts/bench_walkforward.py` — sealed-accumulation,
2024-03-01..2026-06-30, is-days=180/oos-days=60/trials=15/seed=42, impact
model on, one run per config (a walkforward is minutes long; best-of-N
would triple an already-long benchmark):

| config | wall-clock (s) | speedup vs python |
|---|---|---|
| python, serial | 359.5 | 1.0x |
| cpp, serial | 20.0 | 18.0x |
| cpp, workers=auto | 20.5 | 17.5x |

serial == parallel (bit-for-bit): PASS

Results are **unchanged by construction**: each fold's optuna study is
independent and seeded, so routing folds through a `ThreadPoolExecutor`
changes nothing about what gets computed, only the concurrency of computing
it. That equivalence isn't just asserted — it's the built-in acceptance
check above (cpp serial vs cpp workers=auto: identical stitched equity
curve to the float, identical summary dict, identical per-fold params) plus
the Plan 11 equivalence test suite that runs it continuously in CI.

What changed is wall-clock — but read the table carefully before crediting
threads for it. The 18.0x win over the pre-Plan-11 status quo (python,
serial) comes almost entirely from the native C++ engine and the
once-per-run load / per-fold `PreparedMarket` hoist (Tasks 1-2), not from
parallelism: fold-level threading itself (cpp serial 20.0s vs cpp
workers=auto 20.5s, 8 cores available, 11 folds) added **no measured
gain** on this workload — if anything workers=auto is very slightly slower,
which is noise from a single run each rather than a real regression (see
the script docstring on why this bench doesn't do best-of-N). The likely
mechanism, offered as an explanation rather than a measured profile: each
fold's Python-side prep (`PreparedMarket.prepare` — polars filtering,
`iter_rows` interning into flat arrays) runs with the GIL held, and at 15
trials/fold the GIL-released C++ event-loop region is a small slice of a
fold's total wall-clock, so Amdahl's law caps the achievable threaded
speedup near zero for this trial count. The theoretical ceiling is
`min(folds, cores)`; this run had headroom on both axes (11 folds, 8
cores) and still didn't realize it, because headroom is necessary but not
sufficient — the region that actually releases the GIL has to be a
meaningful fraction of the work being parallelized, and here it isn't.

The substantive result of this plan is the acceptance property, not a
wall-clock win: fold workers are provably correct under real concurrency
(bit-identical output, genuinely concurrent optuna studies, no shared
mutable state — each worker builds its own `PreparedMarket` windows off
shared read-only frames) and free to enable (no measured regression beyond
noise). It's worth shipping because it costs nothing and because a
configuration with a larger GIL-released fraction (more trials/fold, or a
strategy whose per-bar work dominates prep) would realize more of the
ceiling this plan only proves is *reachable*, not *exceeded* here.
Strategies that run via the per-bar Python callback bridge (e.g. ml-ranker)
hold the GIL for the strategy's entire per-bar loop, not just a short
engine region, so they gain effectively nothing from `--workers` — for
those, wall-clock stays bounded by process-level parallelism, same as
before this plan. Plan 10 (above) described GIL release on the native path
as a capability a future plan "can" add; it is no longer future — the
native event loop now genuinely releases the GIL during every fold's C++
run, which is what makes the bit-for-bit concurrent-optuna result above
possible at all, even though the wall-clock payoff on this particular
workload is close to zero.

## Rigor pack (2026-07-19): CIs, deflated Sharpe, Reality Check

`uv run pkmn evaluate` (registry run `20260719T145313Z-dd9f28`) discovered
all five `wf-*` strategy artifacts in `data/results/` (skipping the shorter
of the two ml-ranker artifacts, `wf-ml-ranker-2024-03-01-2024-09-01`, with a
stderr note — the longer `wf-ml-ranker-2024-03-01-2026-06-30` was used),
auto-located the `buy-and-hold-sealed-2024-03-01-2026-06-30` benchmark, and
ran a seeded stationary block bootstrap (n_boot=10000, mean block 10 days,
seed 42) over the 660 days common to all curves (2024-08-28..2026-06-18):

| strategy | OOS total return | 95% CI | Sharpe (ann.) | deflated Sharpe |
|---|---|---|---|---|
| cost-aware-reversion | -10.18% | [-18.81%, -1.11%] | -1.68 | 0.001 |
| dip-buyer | -9.02% | [-16.29%, -1.23%] | -1.26 | 0.005 |
| ml-ranker | -7.52% | [-20.21%, 5.73%] | -0.73 | 0.010 |
| sealed-accumulation | -7.36% | [-21.27%, 8.16%] | -0.80 | 0.008 |
| xs-momentum | -25.08% | [-44.62%, -5.20%] | -2.22 | 0.000 |

**White's Reality Check** (best strategy vs benchmark, jointly over all 5
strategies): p = 1.0000.

Caveat before reading anything else into this table: the five artifacts mix
cost regimes. sealed-accumulation and ml-ranker are impact-on re-runs
(`cost_model.impact_enabled: true` in their registry records, per Plan 9);
dip-buyer, cost-aware-reversion, and xs-momentum are the earlier flat-cost
artifacts. The three flat-cost strategies' OOS returns are directly
comparable to each other but not, strictly, to the two impact-on ones or to
the impact-on buy-and-hold benchmark this run auto-located — the
comparison is the best available from what's on disk, not an apples-to-
apples cost-model match. Re-running the flat-cost three under
`--engine cpp` with impact on (as Plan 9/10 did for the other two) would
remove this asterisk; that re-run is out of scope here.

With that caveat stated: every one of the five strategies has a negative
point-estimate OOS total return, and every 95% CI is wide — three
(cost-aware-reversion, dip-buyer, xs-momentum) sit entirely below zero,
while the two impact-on strategies (ml-ranker, sealed-accumulation) have
CIs that straddle zero, meaning the bootstrap can't rule out a small positive
return for either at the 95% level even though the point estimate is
negative. Deflated Sharpe is the probability the candidate's *true* Sharpe
exceeds zero after correcting for having selected it out of the trial pool;
all five are far below even the 0.5 coin-flip point, let alone the
conventional 0.95 confidence bar — the highest, ml-ranker at 0.010, means
only about a 1% probability that its edge is genuine (equivalently, about
99% of a matched population of random strategies with the same trial count
and return variance would be expected to produce a best Sharpe at least
this high by chance alone); cost-aware-reversion and xs-momentum are
effectively zero (0.001 and a value on the order of 2x10^-7). The Reality
Check p-value of 1.0000 is the headline number: the statistic is
sqrt(n) * max_k mean(excess_k), and the bootstrap distribution against
which it's compared is recentered to be luck-only (zero-mean) per
strategy. p = 1.0000 means the observed best excess return sat at or
below every one of 10,000 luck-only (recentered) resample maxima — the
best candidate underperforms even a typical best-of-five luck draw. That
is about as unambiguous a "nothing here beats buy-and-hold, and this isn't
a multiple-testing artifact of screening five strategies at once" result
as this framework can produce. It is consistent with, and sharpens, every
walk-forward re-run since Plan 9: once market impact is priced in, this
data and this strategy zoo have not found anything that survives contact
with buy-and-hold sealed.

Standing caveat, repeated because it applies to every figure in the table
above: Sharpe (and anything derived from it — the CIs are on total return,
not Sharpe, but the deflated Sharpe column is) inherits the mark-smoothing
inflation documented throughout this file (thin markets, carry-forward
marks). Treat the deflated Sharpe numbers as optimistic upper bounds, not
calibrated probabilities.

## ml-ranker-v2 (2026-07-20): friction features, net-of-cost labels, purged validation

**Trial accounting:** one declared walkforward, no re-rolls — `uv run pkmn
walkforward --strategy ml-ranker-v2 --start 2024-03-01 --end 2026-06-30
--trials 15` (registry run `20260720T033422Z-858699`, artifact
`data/results/wf-ml-ranker-v2-2024-03-01-2026-06-30/`). The evaluate step
below is a separate, non-search run over the resulting artifact (registry
run `20260720T034508Z-241dd9`).

### Walk-forward result

11 folds, same schedule as every other artifact in this file (IS 180d / OOS
60d / warmup 120d, impact model on):

| metric | value |
|---|---|
| stitched_total_return | -14.72% |
| stitched_cagr | -8.44% |
| stitched_max_drawdown | -25.87% |
| stitched_sharpe | -0.58 |
| stitched_sortino | -0.64 |
| stitched_calmar | -0.33 |
| is_total_return_mean | -3.95% |
| oos_total_return_mean | -1.39% |
| is_cagr_mean | -7.61% |
| oos_cagr_mean | -7.02% |
| overfitting_gap (is_cagr_mean - oos_cagr_mean) | -0.58 pts |

Full per-fold IS/OOS windows and the selected `(horizon_days,
rebalance_days, top_n, train_days, min_price, min_samples_leaf)` for each of
the 11 folds are in
`data/results/wf-ml-ranker-v2-2024-03-01-2026-06-30/report.md`; not
reproduced here beyond the summary table above.

### Full-zoo evaluate (all six strategies + benchmark)

`uv run pkmn evaluate` (registry run `20260720T034508Z-241dd9`), the same
seeded stationary block bootstrap as the Rigor pack section above
(n_boot=10000, mean block 10 days, seed 42), 660 aligned days
(2024-08-28..2026-06-18), benchmark `buy-and-hold-sealed-2024-03-01-
2026-06-30`:

| strategy | OOS total return | 95% CI | Sharpe (ann.) | deflated Sharpe |
|---|---|---|---|---|
| cost-aware-reversion | -10.18% | [-18.81%, -1.11%] | -1.68 | 0.000 |
| dip-buyer | -9.02% | [-16.29%, -1.23%] | -1.26 | 0.004 |
| ml-ranker | -7.52% | [-20.21%, 5.73%] | -0.73 | 0.007 |
| ml-ranker-v2 | -14.72% | [-43.83%, 25.59%] | -0.58 | 0.017 |
| sealed-accumulation | -7.36% | [-21.27%, 8.16%] | -0.80 | 0.005 |
| xs-momentum | -25.08% | [-44.62%, -5.20%] | -2.22 | 0.000 |

**White's Reality Check** (best strategy vs benchmark, jointly over all 6
strategies): p = 1.0000 — unchanged in substance from the 5-strategy Rigor
pack run (also 1.0000); adding ml-ranker-v2 to the zoo does not move the
joint verdict, because its point estimate is the worst of the six impact-
priced/mixed-regime active strategies, not the best.

Small deflated-Sharpe point-estimate changes versus the Rigor pack table
above (e.g. ml-ranker 0.010 → 0.007) come from re-running the same seeded
bootstrap over a 6-strategy panel instead of 5 (the joint resample draws a
different max-of-K path even with the same seed and same per-strategy
returns); they are not evidence of anything strategy-specific and are well
within the width implied by the CIs above.

### Ablation: v2 vs v1, from the actual numbers

Both are impact-on artifacts, so this is an apples-to-apples comparison
(unlike the mixed-regime caveat that applies to the flat-cost three below).

| | v1 (`ml-ranker`, impact-on) | v2 (`ml-ranker-v2`) |
|---|---|---|
| stitched OOS total return | -7.52% | -14.72% |
| is_cagr_mean | -3.39% | -7.61% |
| oos_cagr_mean | -3.72% | -7.02% |
| overfitting_gap (IS - OOS, CAGR pts) | +0.33 pts | -0.58 pts |
| deflated Sharpe | 0.007-0.010\* | 0.017 |

\* 0.010 in the 5-strategy Rigor pack run, 0.007 in the 6-strategy evaluate
run above — see the resample-path note directly above; not a real change in
v1's evaluation, just panel-size noise in the same seeded bootstrap.

**v2 is worse, plainly.** Stitched OOS total return nearly doubles in
magnitude on the loss side (-7.52% to -14.72%), and OOS CAGR mean roughly
doubles too (-3.72% to -7.02%). Friction-aware features, net-of-cost
labels, and purged validation were built to remove a specific failure mode
(the model chasing gross moves that don't clear round-trip cost, validated
on a leaky random split) — the labels and the `early_stopping=False` closure
did that (the embargoed grid selection itself stayed inert at the deployed
params — see the mechanism-honesty note below), but the resulting model finds
even less of an exploitable edge than v1 did, not more. This is a negative
result for the v2 hypothesis and should be reported as such, not spun.

**The methodologically interesting finding is the overfitting gap, not the
return.** v1's impact-on gap was already small in absolute terms (+0.33
CAGR-pts) but, as the Plan 9 section above documents, that smallness was
itself misleading: IS mean CAGR was negative (-3.39%) only because the
whole regime had gone negative under impact, not because the model had
stopped fooling itself in-sample relative to OOS. v2's gap is smaller
still and has flipped sign (-0.58 pts, i.e. OOS mean CAGR is very slightly
better than IS mean CAGR, not worse) — and this time in-sample is
unambiguously negative and of comparable magnitude to OOS (-7.61% vs
-7.02%), not artificially depressed by a regime-wide flip. In other words,
the net-of-cost labels and the unconditional `early_stopping=False` (which
closes sklearn's silent random-split leak that auto-activates above 10,000
samples — every v2 training frame clears that line) appear to have done
their intended job of removing in-sample self-deception: the model is no
longer telling itself a rosier story in-sample than it can deliver
out-of-sample. That is a real methodological improvement in honesty of
the validation, cleanly scoped: it says nothing about whether the
underlying features carry a tradeable edge, and on this data they do not —
v2's OOS numbers are worse than v1's on every return metric above. A
validation procedure that stops the model lying to itself is not the same
thing as a validation procedure that finds a strategy that works, and this
run separates those two claims cleanly: honest gap, still no edge.

**What actually operated in this run (mechanism honesty).** The measured
-14.72% is an ablation of {v2 features + net-of-cost labels + unconditional
`early_stopping=False`} at the fixed default config `grid[0] =
ModelConfig(max_iter=100, learning_rate=0.1)` — NOT of the in-loop grid
selection, which was inert here. At the per-fold selected parameters
(every fold chose horizon >= 23 days, with stride = horizon), an OOS-segment
rebalance sees at most ~180 days of history, yielding only 2-7 distinct
strided training dates; the purged split's 15% validation slice then rounds
to a single validation date, below `min_val_dates=2`, so `select_config`
returned its thin-data fallback `grid[0]` at every OOS rebalance of all 11
folds (fallback by design — never a random split). The grid search could
only have engaged inside deep-history in-sample trial evaluations with small
horizons; it never chose a config for a deployed OOS bar. This is a known
limitation for Plan B: exercising the selection at real scale needs a deeper
warmup, smaller horizons, or a relaxed `min_val_dates` / finer stride so
enough validation dates survive the embargo.

### Standing caveats (repeated)

- Sharpe/Sortino/Calmar are inflated by mark smoothing (thin markets,
  carry-forward marks); compare strategies to each other and to
  buy-and-hold only, not to equities benchmarks.
- 15 Optuna trials/fold is closer to random search than full Bayesian
  optimization; a wider search could shift these numbers in either
  direction.
- **Mixed-regime caveat, updated:** ml-ranker-v2 and ml-ranker (v1) are both
  impact-on artifacts now (v1 since Plan 9, v2 by construction — its
  `label_cost` and the engine's cost model both default to impact-on), so
  the v1/v2 ablation above is directly comparable. sealed-accumulation is
  also impact-on (Plan 9). dip-buyer, cost-aware-reversion, and xs-momentum
  in the full-zoo table above remain flat-cost artifacts from before Plan
  9 — those three are not directly comparable to the three impact-on
  strategies or to the impact-on buy-and-hold benchmark; see the Rigor pack
  section above for the full statement of this caveat.
- The deflated Sharpe and Reality Check statistics inherit the mark-
  smoothing Sharpe inflation noted above; treat them as optimistic upper
  bounds, not calibrated probabilities.

## Method notes / caveats (repeat in README)

- The stitched curve is OOS-only: each fold's parameters are frozen before
  the OOS window; segments chained by compounding. Seams assume mark-value
  carryover with no liquidation costs, and every segment restarts from
  initial cash — an upper bound on realized compounding.
- Sharpe/Sortino/Calmar on this data are inflated by mark smoothing
  (thin markets, carry-forward marks). Benchmark "Sharpe 17.0" is an
  artifact; compare strategies to each other and to buy-and-hold only.
- 15 optuna trials/fold is closer to random search than Bayesian
  optimization (TPE uses 10 random startup trials); adequate for 3-param
  spaces but a wider search could change the sealed-accumulation params.
- Fixed during these runs: OOS windows originally started with ZERO price
  history, so lookback strategies were structurally blind (xs-momentum
  traded 0 times OOS in 11/11 folds). Fixed by observe-only warm-up
  history (`4acf020`); engine default `warmup_days=0` preserves goldens,
  CLI default 120.
- Regime caveat: one ~2-year bull market for sealed. These results say
  little about how the strategies behave in a flat or falling market.

## Catalog completeness (2026-07-29/30): universe correction and its effect on this window

### What changed and why

The product catalog only ever asked tcgcsv for the 23 price-tracked groups.
TCGplayer re-files products between groups over time — a product whose
prices were archived under a tracked set can later list under an untracked
promo group (`Miscellaneous Cards & Products`, `Blister Exclusives`,
`Nintendo Promos`, …). Every product that drifted this way lost its catalog
row entirely, and a priced product with no catalog row has no `rarity`, so
it gets tagged `kind: "other"` — invisible to the five of seven strategies
that filter their trading universe on `kind`. The catalog-completeness plan
(Tasks 1-4, this branch) fetches every one of tcgcsv's 217 groups on every
ingest, added `pkmn refresh-products` to repair the catalog on demand
without a price re-ingest, and ran that repair against the real warehouse.

**Real before/after, measured directly against the live warehouse (Task 4,
2026-07-29), priced-universe counts by kind:**

| kind | before | after | delta |
|---|---|---|---|
| UNCATALOGED | 2,017 | 8 | −2,009 |
| `single` | 4,077 | 5,383 | +1,306 |
| `sealed` | 436 | 953 | +517 |
| `excluded` | 135 | 321 | +186 |

Catalog rows 4,673 → 32,506, spanning 214 of tcgcsv's 217 groups (3 are
genuinely empty upstream, confirmed by direct re-fetch). The total priced
universe is invariant at 6,665 before and after — the same products,
reclassified, not a different population. The residual 8 UNCATALOGED
products (`660410, 664093, 646307, 662148, 662168, 557364, 646306, 662234`)
appear in no tcgcsv group at all, verified by direct re-fetch — genuinely
un-catalogable upstream, correctly left `kind: "other"`. Zero products lost,
zero duplicate `product_id`s.

### The reclassification is prospective, not retrospective, for this window

That +1,306/+517/+186 shift reads like a large correction, and warehouse-wide
it is. But the standard research window this whole file uses is
2024-03-01..2026-06-30, and almost none of the shift lands inside it:

- Products priced on or before 2026-06-30: **4,687**.
- Products priced after 2026-06-30 (the warehouse now runs through
  2026-07-28, 902 ingested days): **6,617**.
- Products that price **only** after 2026-06-30 (never appear in-window at
  all): **1,978**.
- Of the 4,687 in-window priced products, exactly **32** changed `kind`
  between the before and after catalogs — **all 32 became `single`, zero
  became `sealed`**. (This reconciles the Plan 10 finding already in this
  file: "40 of 4,687 priced product_ids inside the backtest window" were
  UNCATALOGED before the fix; 8 remain UNCATALOGED after; the other 32
  moved to `single`.)

In other words: the overwhelming majority of the 2,009-product warehouse-wide
correction is for products that only started pricing in the final month of
ingest (2026-07), which sits entirely outside the window every walk-forward
in this file uses. The fix is real and warehouse-wide, but on
2024-03-01..2026-06-30 specifically it touches 0.7% of the in-window priced
universe (32 of 4,687), and touches the `sealed` side of it not at all.

### Re-run results, by mechanism

Every strategy was re-run exactly as before (`pkmn walkforward --strategy
<s> --start 2024-03-01 --end 2026-06-30 --trials 15`, impact model on — the
CLI default) against the corrected catalog. The honest way to read what
moved and why is by *mechanism*, not as one undifferentiated re-run, because
the five kind-aware strategies split cleanly into three groups that behaved
completely differently, and the difference is explained entirely by how
each strategy touches `ctx.products`:

| path | mechanism | in-window universe effect | result |
|---|---|---|---|
| sealed-universe hard filter (buy-and-hold, sealed-accumulation) | `kind == "sealed"` filter | 0 new sealed products | **bit-identical**, to the full float |
| cost-aware-reversion | never reads `ctx.products` at all (code-verified: zero references) | none, by construction | **provably invariant** — analytical, not measured |
| single-universe hard filter (dip-buyer, xs-momentum) | `kind == "single"` filter | +32 new single candidates | small, bounded: **+0.62 pts, +1.24 pts** |
| ML feature paths (ml-ranker, ml-ranker-v2) | no hard filter; `kind`/`released_on` feed features (`is_sealed`, `days_since_release`) that were `NaN` for uncataloged rows | 32 rows go from missing to real metadata; candidate membership unchanged | large, **opposite-signed**: **+2.47 pts, −8.43 pts** |

**Sealed-universe strategies — bit-identical, verified to the full float.**
buy-and-hold-sealed's post-fix `total_return` is
`1.8367248749999998`, identical in every digit to the pre-fix impact-on run
(`20260714T045104Z-8d084f`). sealed-accumulation's post-fix
`stitched_total_return` is `-0.0735732413695348`, identical in every digit
to its pre-fix impact-on run (`20260714T045549Z-e0e52c`). This is exactly
what the mechanism predicts: both strategies buy on (or filter to) the
`sealed` kind, and zero products became `sealed` in-window, so their
candidate sets are byte-for-byte the same before and after the fix.

**cost-aware-reversion — analytically invariant, stated as such rather than
measured.** Its source (`strategies/cost_aware_reversion.py`) references
`ctx.marks` only; it has zero references to `ctx.products` or `kind`
anywhere in the file. Its candidate universe is "every product priced
today," which is invariant to catalog completeness by construction — the
catalog only changes labels, never which `product_id`s are priced. No A/B
was run for this strategy because the code itself rules out an effect; its
post-fix result (stitched −22.45%, impact-on) is simply the first time this
strategy has been run under the impact-on cost model at all (its only prior
number in this file, −10.18%, is a flat-cost artifact — see the mixed-regime
note below).

**Single-universe strategies — small, bounded, confirmed by a clean
same-regime A/B.** dip-buyer and xs-momentum had never been run impact-on
before this plan (their prior numbers in this file, −9.02% and −25.08%, are
flat-cost), so a direct before/after comparison at the CLI's current default
would confound the catalog fix with the well-documented flat-cost →
impact-on regime shift (Plan 9 already showed this shift alone moves results
by double-digit points). To isolate the catalog effect alone, both
strategies were re-run twice under the **same** impact-on regime — once
against the pre-fix catalog (restored from the `/tmp` backup made before
Task 4's refresh) and once against the post-fix catalog — with the live
catalog file verified byte-for-byte back to the corrected state afterward
(`single` 5,383, `sealed` 953, `excluded` 321, `UNCATALOGED` 8, 32,506
catalog rows — exact match to Task 4's after-table):

| strategy | pre-fix catalog, impact-on | post-fix catalog, impact-on | delta |
|---|---:|---:|---:|
| dip-buyer | −21.77% | −21.15% | **+0.62 pts** |
| xs-momentum | −10.54% | −9.30% | **+1.24 pts** |

Small, same-direction, bounded improvements — consistent with adding 32 new
candidate assets to a `single`-only universe of thousands: more choices,
marginally better picks, nowhere near enough to flip the sign.

**ML feature paths — the substantive finding of this plan.** ml-ranker and
ml-ranker-v2 apply no hard `kind` filter (`build_features` takes every
priced product on a given day, joins `ctx.products` for `kind`/
`released_on`, and only *drops* an asset from the trading candidate list if
its `mark < min_price`). Before the fix, the 32 in-window uncataloged
products had **no catalog row to join against**, so `is_sealed` and
`days_since_release` were `NaN` for those specific rows in the feature
frame (sklearn's `HistGradientBoostingRegressor` natively handles missing
features, so training didn't crash — it just saw less information for those
rows). After the fix, those same 32 rows carry real values for both
features. Candidate membership did not change at all for these two
strategies; only the quality of two feature columns, for 32 rows out of
4,687 in-window priced products (0.7%), changed.

That 0.7% metadata correction was **empirically confirmed to move both
strategies' results deterministically**, not as run-to-run noise:

| strategy | pre-fix catalog | post-fix catalog | delta | determinism check |
|---|---:|---:|---:|---|
| ml-ranker | −7.52% (`20260714T051127Z-0bc0fc`) | −5.05% (`20260729T233603Z-db7880`) | **+2.47 pts** | independent re-run `20260730T151358Z-8ab1ea`\* reproduced `-0.05046803533703004` to the full float |
| ml-ranker-v2 | −14.72% (`20260720T033422Z-858699`) | −23.15% (`20260730T151937Z-37eeeb`) | **−8.43 pts** | independent re-run `20260730T181722Z-274fc0` reproduced `-0.2315269080881951` to the full float |

\* both re-runs are separate `uv run` processes, not repeated calls within
one Python process — this is a real cross-process, real-scale determinism
check, not the smaller
synthetic-fixture unit test (`test_deterministic_and_stateless`) that only
proves same-input-same-process reproducibility. Both baselines
(`0bc0fc`, `858699`) were confirmed same-regime as the post-fix runs via
`pkmn runs show` before attributing anything to the catalog (impact-on,
same window, same `trials`/`is_days`/`oos_days`/`seed`/`warmup_days` in
every case) — the deltas above are not confounded by a cost-regime change,
only by the catalog.

**Read this as evidence about the strategies, not about the catalog.**
Determinism closes off the "it's just noisy, ignore it" reading: the swing
is exactly reproducible, so it is a genuine, deterministic sensitivity of
each model's output to the metadata quality of 32 products out of 4,687
in-window (0.7% of the universe). A result that moves by 8.43 points because
32 rows out of thousands gained two previously-missing feature values was
not resting on much to begin with. Do not read ml-ranker's +2.47 as the
catalog "improving" it — the same mechanism, on the same 32 products, moved
ml-ranker-v2 by more than three times as much in the opposite direction.
Contrast this against the hard-filtered strategies directly above: those
move by at most ~1 CAGR-point, in a predictable direction, from a
predictable, countable change in candidate set size. The ML feature paths
have no such bound — the sign itself is not predictable from the size of the
metadata correction. That contrast (hard filters: stable and bounded; ML
feature paths: volatile and unbounded in sign) is the most valuable result
this plan produced, and it says something about the fragility of the ML
strategies' results, not about whether the catalog fix was worthwhile.

### Before/after table — stitched OOS total return, full zoo + benchmark

Regime stated per row; **only rows marked "impact-on, same regime"** isolate
the catalog effect cleanly. Flat-cost prior numbers are shown for reference
only (they predate this plan's impact-on re-runs for the three
never-previously-impact-on strategies) and are not directly comparable to
either the after-fix or the pre-fix-catalog impact-on column.

| strategy | flat-cost (prior, reference only) | impact-on, pre-fix catalog | impact-on, post-fix catalog | catalog-attributable delta |
|---|---:|---:|---:|---:|
| buy-and-hold sealed | +186.0% | +183.7% (`8d084f`) | **+183.67%** (`4aeaa9`) | 0.00 pts (bit-identical) |
| sealed-accumulation | +13.6% | −7.36% (`e0e52c`) | **−7.36%** (`827311`) | 0.00 pts (bit-identical) |
| cost-aware-reversion | −10.18% | n/a (no pre-fix impact-on run; analytically invariant) | **−22.45%** (`c581f7`) | 0 pts (analytical, not measured) |
| dip-buyer | −9.02% | −21.77% (A/B, `7790a6`) | **−21.15%** (`356d78`) | **+0.62 pts** |
| xs-momentum | −25.08% | −10.54% (A/B, `43227a`) | **−9.30%** (`45ba49`) | **+1.24 pts** |
| ml-ranker | +6.0% (flat, Plan 8) | −7.52% (`0bc0fc`) | **−5.05%** (`db7880`) | **+2.47 pts** |
| ml-ranker-v2 | n/a (v2 has no flat-cost baseline; built impact-on) | −14.72% (`858699`) | **−23.15%** (`37eeeb`) | **−8.43 pts** |

### Rigor comparison — DSR and joint Reality Check, before vs after

`pkmn evaluate`, same seeded stationary block bootstrap (n_boot=10000, mean
block 10 days, seed 42) both times, 660 aligned days
(2024-08-28..2026-06-18), benchmark `buy-and-hold-sealed-2024-03-01-
2026-06-30`:

| strategy | DSR before (6-strategy run, `20260720T034508Z-241dd9`) | DSR after (`20260730T181818Z-75d38b`) |
|---|---:|---:|
| cost-aware-reversion | 0.000 | 0.000 |
| dip-buyer | 0.004 | 0.000 |
| ml-ranker | 0.007 | 0.004 |
| ml-ranker-v2 | 0.017 | 0.000 |
| sealed-accumulation | 0.005 | 0.000 |
| xs-momentum | 0.000 | 0.000 |

**White's Reality Check, jointly over all six strategies: p = 1.0000 before
and p = 1.0000 after — unchanged.** Every deflated Sharpe remains far under
even the 0.5 coin-flip point, let alone the conventional 0.95 confidence
bar. One methodological improvement falls out of this re-run almost for
free: dip-buyer, xs-momentum, and cost-aware-reversion are impact-on for the
first time, so **for the first time, all six strategies plus the benchmark
share the same cost regime** — the "mixed-regime" caveat that qualified
every evaluate table in this file since the original Rigor pack section no
longer applies. That is a comparison-hygiene improvement, not a research
result; it does not change the joint verdict, which was already p = 1.0000
under the old mixed-regime panel and stays p = 1.0000 under the new
single-regime one.

### The honest conclusion

**No research conclusion in this file changes.** Nothing beats buy-and-hold
sealed, before or after the catalog fix — the standing result across every
prior plan in this repo holds exactly as before. The corrected catalog does
not surface a strategy edge that was hiding behind bad metadata; it
surfaces that two ML strategies' results were sitting on comparatively
thin ice (a deterministic 8-point swing from a 0.7%-of-universe metadata
correction), while every other strategy's result was either provably or
empirically insensitive to the same correction. Both ml-ranker (−5.05%) and
ml-ranker-v2 (−23.15%) remain deeply negative and are not remotely close to
buy-and-hold sealed's +183.67%. The catalog-completeness plan is capability
and data-quality work — a real fix to a real gap, worth having for any
future window — not a research result, and it should not be read as one.
Its value is **prospective**: the ~1,978 products that began pricing only
in July 2026 are now correctly classified for any walk-forward that extends
past 2026-06-30; on the window every prior finding in this file used, the
fix changes almost nothing that a hard `kind` filter touches and changes a
lot — reproducibly, in unpredictable directions — for the two strategies
that don't filter at all.

### Operational notes, for whoever runs the next ml-ranker-family batch

- The first ml-ranker walk-forward attempt of this batch was killed with
  zero captured output (0 bytes) and no traceback, no exit code, no signal
  name recovered — the process simply stopped producing anything. The
  absence of any traceback is evidence against an in-process Python
  exception (a real code bug would print one to stderr before exiting,
  and stderr was captured in the same stream); it is not proof of a
  specific external cause. Two candidate causes were checked and both
  remain consistent with the evidence: an unexplained ~2h12m gap in
  wall-clock time between the last confirmed-healthy check and the "killed"
  notification (consistent with the machine sleeping), and separately
  measured swap at 88% consumed during a later, unrelated part of this
  batch (consistent with memory pressure triggering an OOM kill). **The
  cause is not definitively established; do not read this as a resolved
  root cause.** The re-run (wrapped in `caffeinate -i`, which prevents idle
  sleep specifically) completed successfully, which is consistent with
  sleep as a contributing factor but does not rule out memory pressure.
- Running the ml-ranker determinism re-run in parallel with the
  ml-ranker-v2 walk-forward was a mistake under this system's memory
  profile: solo ml-ranker takes ~22 minutes (Plan 8), but the parallel pair
  took over 55 minutes and put both runs at risk of the same kill under
  investigation. The subsequent ml-ranker-v2 determinism check was run
  alone, sequentially, and completed in line with the expected solo
  wall-clock. **Run ml-ranker-family walk-forwards sequentially, one at a
  time**, not in parallel, especially on a machine already under memory
  pressure.
