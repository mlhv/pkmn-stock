# Walk-forward findings — 2026-07-04

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
negative. All five deflated Sharpe ratios are far below the conventional
0.5 "not distinguishable from a lucky monkey" threshold — the highest,
ml-ranker at 0.010, means only about 1% of a matched population of random
strategies with the same trial count and return variance would be expected
to produce a Sharpe this high by chance alone; cost-aware-reversion and
xs-momentum are effectively zero (0.001 and a value on the order of
2x10^-7). The Reality Check p-value of 1.0000 is the headline number: it
says that across the joint, cross-correlation-preserving bootstrap of all
five strategies at once, the best-performing strategy in every single
resample beat (or tied) the buy-and-hold benchmark's actual observed
return zero times out of 10,000 — i.e. buy-and-hold's real result was never
exceeded by the max of the five candidates under any resampled path. That is
about as unambiguous a "nothing here beats buy-and-hold, and this isn't a
multiple-testing artifact of screening five strategies at once" result as
this framework can produce. It is consistent with, and sharpens, every
walk-forward re-run since Plan 9: once market impact is priced in, this
data and this strategy zoo have not found anything that survives contact
with buy-and-hold sealed.

Standing caveat, repeated because it applies to every figure in the table
above: Sharpe (and anything derived from it — the CIs are on total return,
not Sharpe, but the deflated Sharpe column is) inherits the mark-smoothing
inflation documented throughout this file (thin markets, carry-forward
marks). Treat the deflated Sharpe numbers as optimistic upper bounds, not
calibrated probabilities.

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
