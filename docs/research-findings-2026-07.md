# Walk-forward findings — 2026-07-04

> **SUPERSEDED TWICE. Read the last two sections of this file first, newest
> last: "Full set coverage" (2026-08-04) and "Instrument repair and
> horizon-matched evaluation" (2026-08-09).**
>
> *Universe (2026-08-04).* Every number in this document, including in the
> catalog-completeness note immediately below and the section it points at,
> was computed against a price warehouse covering **23 of tcgcsv's 217
> groups**. Prices now cover all 217: 11.36x the rows, 4.75x the products.
> The universe is derived from what is priced, so every strategy's candidate
> set changed and every result moved — the benchmark from +183.67% to
> +10.53%, and all six active strategies to between −8.59% and −99.81%. The
> old rows are intact (the rebuild was verified bit-for-bit and is purely
> additive); they are simply a measurement of a much narrower market. Cite
> the numbers below only as "on the 23-group universe".
>
> *Instruments (2026-08-09).* Both of the benchmark figures above are
> returns on a barely-invested account (60.1% and 4.7% of cash deployed),
> and every number in this file above the last section priced buys from
> `market`, a sales-derived print that often sits below the cheapest
> listing. Both were repaired. **The current benchmark reference figure is
> +24.93% (run `20260807T135424Z-8362bc`, 99.9% deployed, impact-on,
> 2024-03-01..2026-06-30)** and it is not comparable to +183.67% or
> +10.53%, because the allocation rule changed rather than the market. The
> last section also answers, per round trip over matched 1-3 month windows,
> whether any strategy beats holding sealed: **no**.

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
wrong or need to be distrusted *on the catalog axis*** — they were computed
correctly against the catalog that existed at the time, and for every
strategy except the two ML ones the correction turns out to be immaterial on
this window; on that axis they remain the numbers to cite for
2024-03-01..2026-06-30. **This note says nothing about the cost axis**, which
supersedes some of them independently: dip-buyer (−9.02%), xs-momentum
(−25.08%) and cost-aware-reversion (−10.18%) below are **flat-cost** numbers,
and the current shared-regime (impact-on) figures for those three are
−21.15%, −9.30% and −22.45% — see the Catalog completeness section's
mixed-regime note. Cite the impact-on figures for cross-strategy comparison.
The ml-ranker and
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

**These catalog counts are point-in-time.** The catalog is now re-fetched on
every ingest, so unattended runs move them: on 2026-07-30 a single
`[Errno 54] Connection reset by peer` on group 2534 during the 09:00
`pkmn daily` job left the live file at 32,218 rows / 213 groups, and — under
the replace-the-file behaviour this branch originally shipped — the run
reported success with no trace. The next morning it recurred: on 2026-07-31,
still pre-fix, a reset on group 1404 dropped that group while a successful
re-fetch restored 2534, leaving 32,410 rows / 213 groups. Two silent losses
in 48 hours, both harmless only by luck — neither group holds priced
products, and a direct re-measurement after the second one puts warehouse-wide
`UNCATALOGED` at 8 of 6,666, unchanged. The final-review fix makes
that class of drift non-destructive: `refresh_products` now merges the fetch
into the stored catalog (fresh rows win per `product_id`), refuses to write a
fetch holding under 90% of the rows on disk, catches malformed-JSON and
missing-`results` payloads alongside HTTP errors, and reports the failed
group count into `pkmn refresh-products`' output and `daily.json`
(`catalog_failures`). None of the numbers in this section change: they were
measured before that drift, from a complete refresh.

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

---

## Full set coverage (2026-08-04): prices for all 217 groups, and what a 4.75x wider universe does to every number in this file

**Everything above this section was computed on a 23-group price universe.
This section supersedes all of it.** Not in the qualified way the
catalog-completeness note at the top of this file supersedes things — that
correction changed metadata for 32 in-window products and turned out to be
immaterial for most strategies. This one changes the population being
traded. Every prior number in this file is a measurement of a different,
much narrower market.

### What changed

`pkmn ingest` previously fetched price archives for 23 tcgcsv groups: the
`tracked_groups` filter kept only sets released on or after
`MIN_SET_RELEASE` (2024-01-01). The product catalog had already been widened
to all 217 groups by the previous plan; prices had not. That asymmetry was
documented as deliberate. It no longer exists — `MIN_SET_RELEASE` is gone
and `tracked_groups` returns every group tcgcsv publishes.

The warehouse was rebuilt into a shadow directory from the cached raw
archives (`scripts/rebuild_warehouse.py`, 17m08s for 907 days), every
pre-existing product was verified to reproduce exactly
(`scripts/verify_rebuild.py`, PASSED first try, exit 0), and only then
swapped in. The pre-swap warehouse is retained at `data/warehouse-old/`.

The gate compares prices and quarantine decisions; the final review added a
third comparison, **catalog membership**, because the two catalogs are built
asymmetrically. The live one is merged into on every ingest (with a
`CATALOG_FLOOR` refusal), so a transient per-group fetch failure keeps the
previous rows; a shadow's is one fresh fetch with nothing to merge against
and no floor that can bite, so a single reset connection could ship a
catalog *smaller* than the live one and the gate would have passed it —
silently re-creating the uncataloged-product bug the previous plan exists to
fix, which the price comparison cannot see (a product that loses its catalog
row still reproduces its prices exactly). This did not bite on the actual
swap: 0 catalog product_ids were lost. It is a latent hole in a script now
documented as reusable operator tooling, and it is closed.

Measured at swap time (2024-02-08 .. 2026-08-02, 907 days both sides):

| | pre-swap (23 groups) | post-swap (217 groups) | growth |
|---|---:|---:|---:|
| price rows | 3,197,600 | 36,337,465 | **11.36x** |
| distinct priced products | 6,671 | 31,712 | **4.75x** |
| quarantined rows | — | 1,010,597 | — |
| on-disk | 38 MB | 416 MB | 11x |

**The universe is not configured anywhere — it is derived from what is
priced.** `products.kind` classifies each priced product, and the strategies
filter on that, so widening ingestion widened every strategy's candidate set
without a line of strategy code changing:

| kind | distinct priced products |
|---|---:|
| `single` | 28,175 |
| `sealed` | 2,439 |
| `excluded` | 986 |
| `null` (no catalog row) | 112 |

**The change is additive in two ways, not one.** The obvious way is 25,041
products that had no price history at all. The less obvious way: products
that already existed gained history. In-window (2024-03-01..2026-06-30),
products that existed pre-swap went from 2,918,425 rows across 4,687
products to 4,211,405 rows across 6,513 products. Nothing was overwritten —
verification proves the old rows reproduce bit-for-bit. The extra rows are
days on which a product happened to be filed under an untracked group, which
is the same TCGplayer re-filing behavior the previous plan documented on the
catalog side, showing up on the price side.

### Sparsity: how much of the new universe is meaningfully tradeable

Fraction of the 907 warehouse days on which each product has a price row:

| sparsity bucket | n products |
|---|---:|
| prices on >=90% of days | 22,677 |
| 50-90% | 4,232 |
| 10-50% | 3,828 |
| <10% of days | 975 |

The reassuring reading is that 71.5% of the new universe prints on almost
every day, so this is not mostly a pile of one-print curiosities. The
correct reading is less reassuring: **"has a price row" is not "is
liquid"**, and on this data source it is barely evidence of a trade at all.
tcgcsv publishes a `market` price daily whether or not anything sold; the
carry-forward mark smoothing that has been caveated in every report in this
file is now applied to 4.75x more products, most of them thin vintage singles
(the plan predicted "roughly 7x" here; the measurement came in at 4.75x
products and 11.36x rows — cite the measurement, not the prediction).
The 4,803 products in the bottom two buckets are the visibly thin tail; the
22,677 in the top bucket are the invisibly thin body. Every Sharpe and
Sortino below is inflated by this, worse than before.

### Before/after — stitched OOS total return, full zoo plus the benchmark

All post-swap runs: `--start 2024-03-01 --end 2026-06-30 --trials 15`,
11 folds, market impact **on** (CLI default). The "pre-swap" column is taken
from this file's Catalog completeness section, which is the most recent
impact-on set, so **both columns share the same cost regime** and the only
difference is the universe. The benchmark row is a single full-window
`pkmn backtest`, not a walk-forward, on both sides.

| strategy | pre-swap, 23 groups (impact-on) | post-swap, 217 groups (impact-on) | delta |
|---|---:|---:|---:|
| buy-and-hold sealed (backtest) | +183.67% (`4aeaa9`) | **+10.53%** (`37a23f`) | −173.1 pts |
| sealed-accumulation | −7.36% (`827311`) | **−8.59%** (`f7ec57`) | −1.2 pts |
| xs-momentum | −9.30% (`45ba49`) | **−38.72%** (`dd024b`) | −29.4 pts |
| dip-buyer | −21.15% (`356d78`) | **−99.80%** (`c3055a`) | −78.7 pts |
| cost-aware-reversion | −22.45% (`c581f7`) | **−98.70%** (`fc1642`) | −76.3 pts |
| ml-ranker | −5.05% (`db7880`) | **−81.64%** (`2862d3`) | −76.6 pts |
| ml-ranker-v2 | −23.15% (`37eeeb`) | **−99.81%** (`452b23`) | −76.7 pts |

**Superseded 2026-08-09** by the Instrument repair section at the end of this
file: the benchmark row was measured at 4.7% deployment (now 99.9%, +24.93%)
and every row was measured without the executable buy-price floor. Those
figures are the "before" column of that section's before/after table.

Two of these numbers demand an explanation before anything else in this
section can be read: the benchmark falling by an order of magnitude, and
four strategies losing essentially everything. Both were investigated;
neither is what it looks like at first glance.

### The benchmark's fall is a capital-deployment artifact, not a market fact

`BuyAndHold.on_bar` splits cash equally across the `kind` universe on the
first bar and buys `floor(budget_per_asset / price)` of each. That rule has
an integer floor, and the floor bites hard when the universe grows while the
account does not:

| | pre-swap | post-swap |
|---|---:|---:|
| sealed assets with a print on 2024-03-02 | 45 | 1,315 |
| budget per asset ($10,000 account) | $222.22 | $7.60 |
| assets actually filled | 39 | 79 |
| **capital deployed** | **$6,012.55 (60.1%)** | **$466.78 (4.7%)** |
| all-in capital consumed (notional + fees + impact) | $6,296.27 (63.0%) | $550.77 (5.5%) |
| median fill price | $25.30 | $4.35 |
| max fill price | $149.99 | $7.54 |
| final equity | $28,367.25 | $11,052.73 |
| **headline total return** | **+183.67%** | **+10.53%** |
| **return on the invested slice** (gain / all-in) | **+291.7%** | **+191.1%** |

*Provenance:* rows 3-8 are read directly off the two runs' artifacts —
`data/results/20260729T200904Z-4aeaa9/{fills,equity}.parquet` (pre-swap) and
`data/results/20260804T061600Z-37a23f/{fills,equity}.parquet` (post-swap).
Deployed = `sum(price * quantity)` over fills; all-in adds `sum(fees)` and
`sum(impact)`; gain = last minus first `equity` value; invested-slice return
= gain / all-in. Row 1 is `select count(*) from (select distinct product_id,
sub_type from prices join products using(product_id) where kind='sealed' and
date='2024-03-02')`, run against `data/warehouse-old/` and the live
warehouse respectively. Row 2 is `10000 / row 1` — the actual `BuyAndHold`
budget is over `ctx.marks`, which carries forward, so it can exceed the
count of same-day prints; the $7.54 max fill price sitting just under
$10,000/1,315 = $7.605 confirms the two agree here.

**Both benchmarks are cash-dragged; they differ in degree, not in kind.** The
pre-swap run left 37% of the account in cash and the post-swap run left 95%,
so the like-for-like comparison is the last row: the invested slice returned
**+291.7% pre-swap versus +191.1% post-swap** ($18,367.25 gain on $6,296.27,
against $1,052.73 on $550.77). That is a real decline and it is not nothing —
but it is a factor of 1.5 on the money actually at risk, against a factor of
17 on the headline. **About 85% of the headline collapse is the sizing rule
running out of budget, not the assets performing worse** — see the
decomposition immediately below for the basis and the range.

*Deriving that 85%.* The headline return factors exactly into two terms,
`R = f * r`, where `f` = all-in capital consumed / starting cash
(the deployment fraction) and `r` = gain / all-in (the invested-slice
return). The identity is exact on both runs to full float precision:
`0.6296271250 * 2.9171628764 = 1.8367248750` and
`0.0550772375 * 1.9113660612 = 0.1052727625`. The headline fell
`1.836725 → 0.105273`, a ratio of 17.45x and a level change of −1.731452.
How much of that to attribute to `f` depends on the decomposition, so here
are four standard ones rather than one round number:

| basis | share attributed to deployment (`f`) |
|---|---:|
| **log (Törnqvist)** — exactly additive in logs, order-independent | **85.2%** |
| Shapley — order-independent average over both orderings | 80.1% |
| sequential, `f` moved first | 96.8% |
| sequential, `r` moved first | 63.4% |

The quoted 85% is the **log basis**, chosen because the claim being made is
about a *multiplicative* collapse (a 17x fall in a return factor), and the
log decomposition is the one that is exactly additive for a product of two
terms and independent of the order you move them in:
`ln(f_post/f_pre) = −2.43639`, `ln(r_post/r_pre) = −0.42279`, and
`−2.43639 / (−2.43639 + −0.42279) = 0.8521`. The Shapley figure (80.1%) is
the other order-independent choice and is the natural one if you insist on
decomposing the *level* change; the two sequential figures bracket the
answer at 63.4-96.8% and are order-dependent, which is exactly why neither
is quoted. **Every basis puts deployment as the dominant term**, which is
the claim; only the second digit is basis-sensitive. The earlier "roughly
90%" in this document sat above three of the four and is corrected to 85%.
All inputs are read off the same two `fills.parquet`/`equity.parquet` pairs
cited in the provenance block above.

The post-swap run's Sharpe of 6.57 and max drawdown of −0.90% should be read
the same way: those are the statistics of a portfolio that was 95% a
money-market account, not a risk-adjusted triumph. The pre-swap run's
statistics carry a weaker version of the same distortion.

**The dilution hypothesis — that the newly-added sealed products are old,
thin, non-appreciating junk that dragged the average down — is falsified by
the data.** Over the identical window, for sealed assets priced at both ends
of it:

| sealed assets | n | median total return | mean | % that rose |
|---|---:|---:|---:|---:|
| already in the pre-swap warehouse | 298 | +170.8% | +191.7% | 97.0% |
| newly added by this plan | 835 | **+212.3%** | +240.9% | 97.6% |

*Provenance, and read the caveat — this table's basis is thinner than its
n=1,133 suggests.* Source: the live warehouse's `prices` joined to
`products` on `product_id`, filtered `kind='sealed'` and `date` between
2024-03-01 and 2026-06-30, grouped by `(product_id, sub_type)`, taking
`p0 = arg_min(market, date)` and `p1 = arg_max(market, date)` plus
`d0 = min(date)`, `d1 = max(date)`; return is `p1/p0 - 1`. The bucket split
is a left join against the set of `distinct product_id` in
`data/warehouse-old/`'s `prices` table (null = newly added). **The row filter
is `d0 <= 2024-03-08 and d1 >= 2026-06-23 and p0 > 0`** — a one-week
tolerance at each end, which is what "priced at both ends" means here. That
filter is doing a lot of work: **it keeps 1,133 of the 2,389 sealed assets
priced anywhere in the window** (each sealed product has exactly one
sub_type here, so assets and products coincide), because anything that
started or stopped pricing mid-window is excluded. So this is a
**survivorship-conditioned sample**, and the honest reading is narrower than
"the new sealed products did better": it is "among sealed products that
priced across the whole window, the newly added ones did better." That is
still enough to falsify the dilution hypothesis as stated — which was about
the added products dragging the *benchmark* down, and the benchmark only
ever bought day-1-priced assets and held them, so full-window survivors are
exactly the right population for that question. It is **not** a claim about
the 1,256 sealed assets the filter drops, and none is made.

Among full-window survivors, the newly-added sealed products appreciated
*more* than the ones that were already there — that conditioning is not
optional, it is the sample the table above is computed on. On that
population the wider sealed universe is not worse. The benchmark's
headline number fell because the benchmark's sizing rule could no longer
deploy the account, and because what it *could* still afford was restricted
to the sub-$7.60 tail.

**This means the +10.53% is not a usable benchmark**, and it is not a
statement about the sealed market. It is a statement about equal-weighting
integer quantities of 1,315 assets with $10,000. Any future comparison
against "buy-and-hold sealed" on the wide universe needs the benchmark's
sizing fixed (proportional sizing, a capped universe, or a larger notional)
before the comparison means anything. That is a real defect this plan
surfaced, and it is not fixed here.

### The near-total losses: adversarial selection into inverted quotes

−99.8% deserved scrutiny rather than acceptance, so the mechanism was traced
directly. Diagnostic single-window runs (2024-03-01..2026-06-30, $10,000,
impact on, each strategy's fold-1 walk-forward parameters held fixed — these
are *probes*, not the reported walk-forward results, and they reproduce the
ordering, not the exact numbers):

| strategy (probe) | probe total return | buy notional | buy-side impact | impact / notional | share of buy impact from inverted quotes |
|---|---:|---:|---:|---:|---:|
| cost-aware-reversion | −92.0% | $8,292 | $18,624 | **225%** | 97% |
| dip-buyer | −87.8% | $13,002 | $25,243 | **194%** | 92% |
| xs-momentum | −78.3% | $10,505 | $7,944 | 76% | 93% |
| sealed-accumulation | −2.6% | $6,419 | $779 | 12% | 38% |

*Provenance — these are not walk-forward artifacts and no run id exists for
them.* The walk-forward artifact directories contain only
`stitched_equity.parquet` and `walkforward.json`, no fills, so each row was
produced by calling `NativeBacktest` directly (no registry record written,
nothing added under `data/results/`). Configuration, identical across the
four rows: `PreparedMarket.prepare(warehouse, 2024-03-01, 2026-06-30,
warmup_days=0)` on the live warehouse; `CostModel(impact_enabled=True)`
(all other cost parameters at their defaults); `initial_cash=10000.0`; no
seeded holdings; `NativeStrategySpec(name, params)` with `params` set to
that strategy's **fold-1** parameters copied from
`data/results/wf-<strategy>-2024-03-01-2026-06-30/walkforward.json`:

- cost-aware-reversion: `dip_window_days=47`, `dip_threshold=0.15299249746816473`,
  `min_edge=0.14132806164993655`, `take_profit=1.5974985995449138`, `max_hold_days=180`
- dip-buyer: `dip_threshold=0.21225580020730261`, `hold_days=89`, `take_profit=1.4799436244482758`
- xs-momentum: `lookback_days=120`, `top_n=5`, `rebalance_days=51`
- sealed-accumulation: `min_drawdown=0.39817723481147554`, `take_profit=1.2079283160822953`, `min_age_days=178`

Parameters not listed fall to the C++ factory's defaults
(`cpp/src/pkmn_engine/strategies/factory.cpp`), which are the same defaults
the walk-forward uses — optuna only searches each strategy's declared
`ParamSpec`s, and those are exactly the parameters listed above, so the
probes differ from the reported runs only in holding one fold's parameters
fixed across the whole window instead of rolling them. The columns are
computed from the returned `Result.fills`: buy notional =
`sum(price * quantity)` over `quantity > 0`; buy-side impact = `sum(impact)`
over the same rows; the last column is that impact sum restricted to fills
whose `(day, product_id, sub_type)` row in `prices` has `market < low`,
divided by the total. **Because one fold's parameters are held fixed for
22 months, these returns are not the reported results and must not be quoted
as them** — they exist to establish the mechanism and the ordering.

For dip-buyer, total frictions were **$35,533 on a $10,000 account** — 355%
of starting capital — against $13,002 of gross buy notional. The market
prices it sold at were, in aggregate, far above the market prices it bought
at ($39,523 of sell notional against $13,002 of buy notional, 1,244 units
sold against 1,285 bought), so the account was not destroyed by marks moving
against it. The frictions consumed it.

The frictions are not spread evenly. 92-97% of buy-side impact comes from
fills where **`market < low`** — the day's market print sits below the
lowest listed price, which is an internally inconsistent quote. A worked
example, product 107010 (Venusaur, Base Set 1st Edition Holofoil, group
1663 — a set this plan added). The contiguous warehouse series around the
fill, with no rows elided — there is genuinely no 2025-08-14 row in the
warehouse, and the quotes hold flat on either side of the range shown:

```
date        market    low      mid      high
2025-08-13  793.17   799.99   1300.0   1800.0
              (no 2025-08-14 row in the warehouse)
2025-08-15   71.88   799.99   1300.0   1800.0   <- "91% dip"
2025-08-16   71.88   799.99   1300.0   1800.0
2025-08-17   71.88   799.99   1300.0   1800.0
2025-08-18   71.88  1800.0    1800.0   1800.0   <- listings collapse to one $1,800 print
2025-08-19   71.88  1800.0    1800.0   1800.0   <- dip-buyer's fill day
```

`market` fell 91% while every listing stayed at $800-1800. dip-buyer saw a
91% dip and bought **3 units on 2025-08-19** at the $71.88 "market" price.
Note the fill day, because the quote regime changes mid-series: by 2025-08-19
`low`, `mid` and `high` have all collapsed to a single $1,800 print, so the
impact model's spread is `mid − market` = `1800 − 71.88`, and it charged
`spread * qty * qty / (2 * cap)` = `(1800 − 71.88) * 3 * 3 / 6` = $2,592.18
— 12x the notional. The recorded fill is
`day=2025-08-19 qty=3 price=71.88 fees=1.00 impact=2592.18`, so the
effective all-in cost is `(71.88*3 + 1.00 + 2592.18) / 3` = **$936.27/unit**.
The position was then marked at $71.88, so it was written down to a
thirteenth of its cost the moment it filled.

**The impact charge is defensible — in fact it undercharges; the entry price
is the problem.** You cannot buy at $71.88 when, on the fill day, the only
listed price at all is $1,800 (`low` = `mid` = `high` = 1800.0 on
2025-08-19). Charging the walk from `market` toward `mid` produced an
effective $936.27/unit, which is **about half the cheapest price you could
actually have transacted at that day**. So the cost model is not
over-penalising this fill; it is doing roughly the right thing — and erring
on the generous side — with a price that does not exist. The engine is not
miscounting anything: `impact` is computed exactly as specified in Plan 9,
and every dollar is auditable in the fills.

*Provenance:* the quote rows are `select date, market, low, mid, high from
prices where product_id = 107010 and sub_type = '1st Edition Holofoil'`
against the live warehouse. The fill is from a re-run of the dip-buyer probe
described above (fold-1 parameters, 2024-03-01..2026-06-30, $10,000, impact
on), reproducing `day=2025-08-19 qty=3 price=71.88 fees=1.0 impact=2592.18`
exactly. `cap = 3` is `CostModel.max_daily_qty(71.88)`, which is why the
denominator is 6.

What makes this catastrophic rather than merely costly is **adversarial
selection**: dip-buyer maximizes over "market fell most versus its own
history" and cost-aware-reversion over "market cheapest versus fair value".
Both are, by construction, maximizers over exactly the quantity that an
inverted quote corrupts. They do not stumble into these rows; they seek them.
The loss ordering across the zoo tracks how directly each selector
maximizes over that quantity — dip-buyer and cost-aware-reversion (direct
maximizers) worst, xs-momentum (maximizes the opposite sign) intermediate,
sealed-accumulation (drawdown-gated, sealed-only) nearly flat, buy-and-hold
(no selection at all) unaffected. That is a consistency check, not a proof.

The wider universe supplies far more of this fuel, and a fatter tail of it.
In-window, on rows where `market < low`:

| | inverted rows | products | median mid/market | p99 mid/market | rows with mid > 10x market |
|---|---:|---:|---:|---:|---:|
| pre-swap products | 312,914 | 2,987 | 1.43 | 17.3 | 7,004 |
| newly added products | 1,878,145 | 12,199 | 1.66 | **39.0** | **85,628** |

*Provenance:* the live warehouse's `prices` table only (no `products` join —
this is not restricted by `kind`), `date` between 2024-03-01 and 2026-06-30,
filtered `market < low and market > 0`, grouped by bucket. The bucket split
is the same left join used for the sealed-returns table above: against the
set of `distinct product_id` in `data/warehouse-old/`'s `prices` table, null
meaning newly added. Columns are `count(*)`,
`count(distinct product_id)`, `median(mid/market)`,
`quantile_cont(mid/market, 0.99)`, and `sum(mid > 10*market)`. The two rate
figures in the next paragraph come from the same join and window **without**
the `market < low` filter: `100.0 * sum(market < low) / count(*)` per bucket,
over 4,211,405 pre-swap rows and 29,880,919 new rows.

Note what this does and does not say. The *rate* of inverted quotes is
essentially unchanged (7.43% of pre-swap rows, 6.29% of new rows — the new
data is very slightly cleaner per row). What changed is the absolute supply,
6x more inverted rows, and the extremity of the tail, 12x more rows where
`mid` exceeds 10x `market`. An extreme-value selector cares only about the
tail, so a constant defect rate over a 11x larger sample is a much worse
environment for it.

**What remains unexplained.** Three things, stated as open:

1. **The two ML strategies were not traced.** ml-ranker (−81.64%) and
   ml-ranker-v2 (−99.81%) run through the per-bar Python callback bridge and
   cost 75 and 140 minutes per walk-forward, so no diagnostic probe was run
   for them. They have no hard `kind` filter, so they see all 31,712
   products, and their features include price-ratio terms that inverted
   quotes would corrupt — but that is a hypothesis, not a measurement. Do
   not cite a mechanism for those two numbers.
2. ~~**Whether `market < low` is a tcgcsv data defect or a real (thin)
   print**~~ **CLOSED 2026-08-09** — see the Instrument repair section at the
   end of this file. It was neither, because the question had a false
   premise: `market` is sales-derived and `low` is the cheapest current
   listing, so they are different series and `market < low` is an ordinary,
   well-formed observation. The defect was in the consumer — the impact
   model's "market is the front of the book" premise. Buys now price at
   `max(market, low)`. (As originally recorded: "The two readings imply
   opposite fixes — quarantine the row, or trust it and fix the impact
   model's premise — and this plan produced no evidence for either.")
3. **What a liquidity floor would do.** The obvious next experiment
   (restrict the universe to products above some print-density or spread-
   sanity threshold) was out of scope for this plan and has not been run, so
   there is no measured statement about whether these strategies have any
   edge on a cleaned wide universe.

### Rigor comparison — DSR and joint Reality Check, before vs after

`pkmn evaluate`, same seeded stationary block bootstrap both times
(n_boot=10000, mean block 10 days, seed 42), 660 aligned days
(2024-08-28..2026-06-18):

| strategy | DSR pre-swap (`20260730T181818Z-75d38b`) | DSR post-swap (`20260804T204825Z-fa07b8`) |
|---|---:|---:|
| cost-aware-reversion | 0.000 | 0.119 |
| dip-buyer | 0.000 | 0.009 |
| ml-ranker | 0.004 | 0.254 |
| ml-ranker-v2 | 0.000 | 0.069 |
| sealed-accumulation | 0.000 | 0.003 |
| xs-momentum | 0.000 | 0.000 |

Each column uses its own era's benchmark. **Two different windows are in
play, so both are stated rather than mixed**: the artifact's full-window
equity (2024-03-01..2026-06-30, 852 rows) and the 660-day aligned slice
(2024-08-28..2026-06-18) the statistics actually run on.

| benchmark | full-window final equity | aligned slice, first → last day | aligned-slice return |
|---|---:|---:|---:|
| pre-swap `buy-and-hold-sealed-2024-03-01-2026-06-30` | $28,367.25 | $9,187.06 → $27,839.87 | +203.03% |
| post-swap `20260804T061600Z-37a23f` | $11,052.73 | $9,946.86 → $11,015.43 | +10.74% |

The aligned-slice percentages are exactly `last / first - 1` on the two
middle columns and reconstruct from them. Do not instead divide a
full-window final equity by the $10,000 starting cash and expect the same
number: for the pre-swap benchmark that gives +183.67%, which is the
full-window headline quoted elsewhere in this document — a real figure, but
not the one these statistics use.

The deflated Sharpe column is **identical across the two runs to the full
float** (verified against both `evaluate.json` artifacts, all six
strategies), and `deflated_sharpe` takes no benchmark argument — only the
strategy's own aligned returns plus the zoo of strategy Sharpes (`cli.py`).
Do not over-generalize that into "DSR cannot depend on the benchmark",
though: the aligned date set is **seeded from the benchmark curve** and then
intersected with every strategy curve (`cli.py`), so a benchmark with
different date coverage would change which days feed DSR and move it. Here
the two benchmarks span the same 852 days and yield the same 660-day
intersection, which is *why* the column is unchanged. The Reality Check
consumes the benchmark's returns directly and does move.

**White's Reality Check, jointly over all six strategies: p = 1.0000
pre-swap and p = 0.8038 post-swap.** Pre-swap, the observed best excess
return sat at or below every one of 10,000 luck-only recentered resample
maxima; post-swap it sat at or below 8,038 of them. Both are nowhere near
significance — the conventional bar is p <= 0.05 and the loosest reading of
0.8038 is still "the best of the six underperformed roughly four out of five
pure-luck best-of-six draws" — so the conclusion is unchanged. What changed
is that the post-swap statement is no longer the maximally extreme one, and
saying "unchanged" was overstating it.

**Correction, and how the wrong number got published.** The first post-swap
evaluate run (`20260804T184401Z-cf292c`) recorded p = 1.0000, and that figure
was wrong. `pkmn evaluate` auto-discovers its benchmark by globbing
`data/results/buy-and-hold-sealed-*` and taking the longest curve
(`cli.py`), and that legacy directory — written 2026-07-14, on the 23-group
universe — still existed after the swap. So cf292c measured six post-swap
strategies against a **pre-swap** benchmark that returned +203.03% over the
660-day aligned slice instead of the post-swap +10.74%. The registry fallback that finds
the current run only fires when the glob matches nothing, so it never ran.
Fixed by renaming both legacy directories out of the glob's path, nothing
deleted; they are now, under `data/results/`:

- `STALE-preswap-23group-buy-and-hold-sealed-2024-03-01-2026-06-30/`
- `STALE-preswap-23group-buy-and-hold-sealed-2024-08-28-2026-06-30/`

(The second one had to move too: it also matched the glob, so renaming only
the first would have handed auto-discovery a *different* pre-swap
benchmark.) Plain `pkmn
evaluate` now falls through to the registry and picks
`20260804T061600Z-37a23f`, and the re-run `20260804T204825Z-fa07b8` is the
corrected record. Every earlier `evaluate` record in the registry — the
Rigor pack, ml-ranker-v2, and catalog-completeness runs — has a
`config.benchmark` naming the pre-rename path; those runs were correct for
their era, and their artifact now lives under the `STALE-preswap-23group-`
name.

**The rename also left one dangling `artifact_path`, and this is the
complete list.** Auditing all 37 registry records against the filesystem,
exactly one record's `artifact_path` points at a directory that no longer
exists under that name:

| record | command | recorded | `artifact_path` (stale) | now lives at |
|---|---|---|---|---|
| `20260714T045104Z-8d084f` | `backtest` buy-and-hold-sealed | 2026-07-14 | `data/results/buy-and-hold-sealed-2024-03-01-2026-06-30` | `data/results/STALE-preswap-23group-buy-and-hold-sealed-2024-03-01-2026-06-30` |

That is the run behind the pre-swap +183.67% headline. No registry record
points at the second renamed directory
(`...-2024-08-28-2026-06-30`) at all, so the table above is exhaustive. The
four `evaluate` records that name the old path — the three earlier-era runs
in the preceding paragraph (`20260719T145313Z-dd9f28`,
`20260720T034508Z-241dd9`, `20260730T181818Z-75d38b`) plus the stale
post-swap `20260804T184401Z-cf292c` — carry it in `config.benchmark`, not in
`artifact_path`, and their own artifact directories all still exist.

**Nothing breaks, and the three consumers were checked rather than
assumed:** `pkmn runs show 20260714T045104Z-8d084f` prints the full record
including its results (the registry is a self-contained JSONL; it never
reads the directory). The API returns **404 by design** — `_artifact_dir`
in `api/data.py` guards with `art.exists()` and raises `NotFound`, which
`api/app.py` maps to `HTTPException(404)`, so `GET
/api/backtest/20260714T045104Z-8d084f` is a clean not-found rather than a
crash. The Streamlit dashboard's `benchmark_runs()` (`app/dashboard.py`)
filters on `(art / "equity.parquet").exists()`, so the record silently
drops out of the overlay selector; its legacy directory scan matches names
starting with `buy-and-hold`, which the `STALE-preswap-23group-` prefix no
longer does, so the renamed artifact does not reappear there either.

**If you hit it:** the data is not lost, only moved. Read the artifact
directly from the `STALE-preswap-23group-` path, or pass it explicitly
(`--benchmark data/results/STALE-preswap-23group-buy-and-hold-sealed-2024-03-01-2026-06-30`)
if you deliberately want the pre-swap 23-group bar. Do **not** repair this
by renaming the directory back — that resurrects the glob match this whole
correction removed. Rewriting the registry line is also not recommended:
`registry.jsonl` is append-only history, and the record is accurate about
where its artifact was written at the time.

The error made the test **strictly harder**, not easier: a benchmark that
returned +203% is a higher bar than one that returned +10.7%, so every
strategy's excess return was understated and nothing in the published result
was flattered by it. That is why the research conclusion survives intact
rather than needing revisiting. It is still a wrong published statistic,
recorded here as such.

One artifact-path wart from the correction, stated so nobody trips on it:
`pkmn evaluate` names its output directory by **date**, not by run id, so
the corrected run overwrote `data/results/evaluate-2026-08-04/`. The stale
run's output is preserved beside it as
`data/results/evaluate-2026-08-04-stale-benchmark/` (nothing under `data/`
was deleted), but the superseded registry record cf292c's `artifact_path`
still reads `data/results/evaluate-2026-08-04`, which now holds fa07b8's
corrected output. Each record's own `config.benchmark` and
`results.reality_check_p` fields are the authoritative per-run values; the
directory is not.

The deflated Sharpe values went *up* for four strategies, which is worth one
sentence of caution so nobody misreads it as improvement: DSR is a
probability that the true Sharpe exceeds zero after correcting for
selection, and it rises when the return distribution gets wider and more
skewed, not only when performance improves. ml-ranker's 0.254 sits beside a
95% CI of [−99.58%, +331.57%] on a −81.64% point estimate — the number is
large because the estimate is uninformative, not because the strategy got
better. Every value remains far below the 0.5 coin-flip point, let alone
the conventional 0.95 bar.

### The honest conclusion

**Nothing beats buy-and-hold sealed. The standing result across every prior
plan in this repo holds — but it now means something weaker than it used
to.**

On the wide universe the benchmark returned +10.53% and every one of the six
active strategies was negative, so the ordering is unchanged and the joint
Reality Check is p = 0.8038 — down from the pre-swap 1.0000, and still
nowhere near the p <= 0.05 that would indicate anything. What changed is
that the benchmark is no
longer a meaningful bar: it is 95% cash because of an integer-sizing
artifact, so "nothing beats it" is now a much easier statement to satisfy
than it was when the bar was +183.67%. Be precise about that comparison
rather than flattering the old number: **the old bar was not fully deployed
either** — it left 37% of the account in cash (63.0% all-in deployed) and
its +183.67% was +291.7% on the slice actually at risk. The new bar is the
same defect an order of magnitude worse (5.5% deployed, +191.1% invested-
slice). Neither figure is a clean market return; the old one was merely
close enough to one that the defect never announced itself. **A larger
universe is not an improvement by itself**, and this one is not an
improvement yet.
What it delivered is:

- **A correct, verified, purely additive data foundation** — 11.36x the
  price rows, 4.75x the products, every pre-existing row proven identical.
  That is real and it is the durable output of this plan.
- **Two defects surfaced that the narrow universe was hiding**: the
  benchmark's sizing rule does not survive a wide universe, and the impact
  model's "market is the front of the book" premise does not survive
  products whose market print sits below every listing. Both were invisible
  on 23 recently-released sets and are unavoidable on 217.
- **No new strategy result.** The six strategies' post-swap numbers measure
  what happens when selectors tuned on a narrow, clean, monotonically-rising
  slice are pointed at a wide, thin, quote-inconsistent one without any
  liquidity filter. That is a statement about the strategies' robustness and
  about the missing data hygiene layer, not a measurement of edge. Do not
  cite −99.80% as "dip-buyer's performance"; cite it as "dip-buyer with no
  liquidity floor on a universe containing 2.19M inverted quotes" (the
  in-window total: 312,914 on pre-swap products plus 1,878,145 on newly
  added ones, across 15,186 products — 1.88M is the newly-added subset
  alone, not the universe the strategy actually traded).

The honest summary is that this plan moved the project from a small correct
answer to a large one it is not yet equipped to ask questions of. The next
work is a liquidity/quote-sanity layer and a benchmark sizing fix, in that
order, and until both land, no cross-strategy comparison on the wide
universe should be treated as a research result.

**Update 2026-08-09:** the benchmark sizing fix landed (screen + deficit
sweep, 4.7% → 99.9% deployed) along with an executable buy-price floor, and
the zoo was re-run against both; see the Instrument repair section at the end
of this file. The liquidity/quote-sanity *experiment* — restricting the
traded universe by a print-density or spread threshold — is still not run.
The screen built for the benchmark is the machinery it needs.

### Plan amendment: `published_on` is a mutating timestamp, and the gate caught it

The plan as written failed at the verification step, and it is worth
recording why. `tracked_groups` filtered groups on `MIN_SET_RELEASE <=
published_on <= today`. Removing the lower bound was the point of the plan;
the upper bound looked harmless. It is not — `tcgcsv.Group.published_on` is
a *last-touched* timestamp, not a release date, and for 19 promo and
catch-all groups (POP Series, Nintendo Promos, EX Trainer Kits,
Miscellaneous Cards & Products, and others) it reads as today's date and
moves. Those 19 groups plus one more were dropped from the whole 907-day
backfill: the rebuild logged "over 197 sets" instead of 217, and the shadow
warehouse was missing ~1,842 rows/day across 2026-07-01..2026-08-02.

**The verification gate caught it** — 64 problems reported, exit non-zero,
no swap performed. The whole reason the plan built a shadow-rebuild-then-
verify-then-swap pipeline instead of rebuilding in place was to have
something fail here rather than silently in a research result three weeks
later, and that is exactly what it bought. The fix (commit `6eb5903`)
removes the date bound entirely; the re-run logged 217 sets, added 1,434,948
clean rows over the failed attempt, and verification passed on the first
try. Full diagnosis in the plan's `task-4-diagnosis.md`.

### Known residual: 112 products with prices and no catalog row

The coverage table above has a `null`-kind bucket of 112 products — priced
product_ids with no matching row in `products.parquet` at all. These are
distinct from the 986 `excluded` (a real catalog `kind` value).

They are not a repeat of the pre-catalog-fix gap. All 112 stopped pricing
before the warehouse's last day: the latest `max(date)` among them is
2026-07-19 and most stopped much earlier (2025-01, 2025-07, 2026-02). The
supported reading is that these are products **delisted upstream** — the
catalog is a live snapshot of what tcgcsv publishes today, the prices are
archival, so anything TCGplayer removed keeps its history and loses its
catalog row. That reading was not proven, only checked for consistency, and
no fix is proposed: the engine already tags catalog-missing assets kind
`other` (Plan 10, commit `091b663`), which excludes them from every
`kind`-filtered strategy and keeps them tradeable for cost-aware-reversion.
0.35% of the priced universe, no action taken, recorded so it is not
rediscovered as a surprise.

### Operational notes for the next full re-run

- **The ML walk-forwards must run `--workers 1` on this universe.** The
  first ml-ranker attempt used the CLI default (`--workers 0` = auto = 8
  threads here) and was killed after 7.4 hours with the machine thrashing:
  load average 89 on 8 cores, 97 threads, 24.6 GB of 25.6 GB swap consumed.
  The same run with `--workers 1` finished in 75 minutes. ml-ranker and
  ml-ranker-v2 go through the per-bar Python callback bridge and hold the
  GIL for the entire loop, so fold parallelism buys almost nothing while
  costing N copies of the per-fold market window — and the window is now 11x
  larger. Fold-serial and fold-parallel are bit-for-bit identical (the Plan
  11 acceptance property), so this costs nothing in comparability.
- **`caffeinate -i` is not enough for runs this long.** A second attempt
  died silently at 64 minutes with a zero-byte log and no traceback. `-i`
  blocks *idle* sleep only; use **`caffeinate -dimsu`**. This also
  retroactively explains the unexplained zero-byte kill recorded in the
  Catalog completeness section's operational notes, which was wrapped in
  `caffeinate -i`.
- **`pkmn walkforward` prints nothing until it completes**, so a redirected
  run gives no progress signal regardless of `PYTHONUNBUFFERED`. Budget
  ~4.5 minutes per native-strategy walk-forward and 75-140 minutes per ML
  walk-forward on this universe, and check liveness with `ps`, not the log.

## Instrument repair and horizon-matched evaluation (2026-08-09): a benchmark worth losing to, buys priced at a listing that existed, and the 1-3 month answer

**This section supersedes the Full set coverage section above on two axes:
the benchmark figure, and the price a buy executes at.** Every number in that
section was measured against a benchmark that deployed 4.7% of the account,
and with buys priced from `market` — a sales-derived print that on 2.19M
in-window rows sits *below* the cheapest listing. Both were repaired here.
The universe is unchanged (all 217 groups, same 908-day warehouse), the
window is unchanged (2024-03-01..2026-06-30), and market impact is on
throughout, so this section and that one differ only by the two repairs.

### The question this section exists to answer

The owner's goal, stated plainly: **enter and exit within 1-3 months and beat
simply holding sealed over that same window.** Every prior result in this
file compares a strategy's stitched 28-month return against a single
28-month buy-and-hold, which measures exposure time as much as skill — a
strategy that is in the market a third of the days is being scored against
one that is in it all of them. Nothing in this repo could answer the owner's
question before this branch.

### What changed

Three things: two repairs and one new measurement
(`docs/superpowers/specs/2026-08-05-instrument-repair-and-horizon-design.md`).

1. **`buy_and_hold` gained a liquidity screen and a deficit sweep**
   (`research/screen.py`, `strategies/buy_and_hold.py`, and the C++ port in
   `cpp/src/pkmn_engine/strategies/buy_and_hold.cpp`). It had been splitting
   cash evenly across all 1,315 day-one sealed assets and flooring to integer
   units, so at a $7.60 per-asset budget most sealed products were simply
   unbuyable. It now ranks by print density, then relative spread
   `(mid - low) / mid`, then `product_id`/`sub_type`; keeps the top
   `n_assets` (default 100); allocates equal-dollar; then sweeps the
   remaining cash into whichever screened asset has the largest dollar
   deficit until nothing is affordable. Deployment went **4.7% → 99.9%**.
2. **Buys price at `max(market, low)`** (`engine/costs.py` `buy_impact`,
   `engine/execution.py` `_fill_buy`, mirrored in C++). The premium over
   `market` is charged through the `impact` column rather than the `price`
   column, so a fill's `price` field is still the day's `market` print and
   the floor stays auditable as a separate number. Sells and marking are
   deliberately unchanged; see the asymmetry note below.
3. **`research/horizon.py` and `pkmn horizon <run-id>`** pair a backtest's
   fills into FIFO round trips and score each trip against holding the sealed
   index over **that trip's own entry and exit dates**. A six-week trade is
   compared with six weeks of the index.

Both repairs are bit-for-bit identical between the Python and C++ engines —
`scripts/parity_full.py` on the real warehouse, after both changes:

```
[buy-and-hold]          python  72.72s / cpp 31.83s   PASS  (42 fills)
[sealed-accumulation]   python  65.19s / cpp 30.19s   PASS  (56 fills)
[dip-buyer]             python 816.67s / cpp 34.42s   PASS  (334 fills)
[xs-momentum]           python 427.76s / cpp 29.51s   PASS  (183 fills)
[cost-aware-reversion]  python  95.23s / cpp 30.53s   PASS  (253 fills)
```

That run's output covered the five native strategies. CLAUDE.md describes
`parity_full.py` as also covering the ml-ranker callback bridge (`--ml`);
**that case did not appear in this run's output and is not claimed as
re-verified here.**

(Its buy-and-hold line shows 42 fills where the benchmark run below shows 38.
Not a discrepancy, and not a parity issue — parity is between the two
*engines* on identical inputs, which is what the PASS asserts. The two runs
have different inputs: `parity_full.py` uses `warmup_days=120` and `pkmn
backtest` uses 0. See the warmup caveat under the benchmark below, which was
measured rather than assumed.)

### Open question 2, closed: `market < low` is a category error in the consumer, not a bad row

The Full set coverage section records as open (above, "What remains
unexplained", item 2) whether `market < low` is a tcgcsv data defect or a
real thin print, noting that the two readings imply opposite fixes —
quarantine the row, or trust it and fix the impact model's premise.

**The answer is neither, because the question had a false premise.** `market`
and `low` are two different series, not two versions of one.
`data/transforms.py` maps them straight through from tcgcsv: `market` ←
`marketPrice`, `low` ← `lowPrice`, `mid` ← `midPrice`. TCGplayer derives
`marketPrice` from recent completed **sales**; `lowPrice`/`midPrice`/
`highPrice` describe **current listings**. `market < low` is therefore the
ordinary, well-formed observation that recent sales cleared below today's
cheapest offer — 2,191,059 in-window rows across 15,186 products are in this
state, and none of them is corrupt.

The defect was in the consumer. The impact model treated `market` as the
front of the book and walked outward from it, which prices a buy at a number
nobody was offering. Fixing the premise rather than the data is what
Component 2 of the design does: a buy's base price is `max(market, low)`,
because you cannot lift an offer that does not exist. When `low` is null the
base stays `market` — a missing `low` means no floor, never a guessed one.

**Open questions 1 (the two ML losses were never traced) and 3 (what a
liquidity floor would do) remain open.** The screen built here is the
machinery question 3 needs, but running it is a separate full-zoo re-run and
was deliberately out of scope.

### The repaired benchmark

Run `20260807T135424Z-8362bc`, `pkmn backtest --start 2024-03-01 --end
2026-06-30` (impact on, `n_assets=100`, $10,000):

| metric | value |
|---|---:|
| total return | **+24.93%** |
| CAGR | +10.02% |
| max drawdown | −25.73% |
| Sharpe / Sortino / Calmar | 0.6336 / 0.6824 / 0.3894 |
| equity | $10,000.00 → $12,492.97 |
| fills | 38 (all buys; it buys once and holds) |

Deployment, computed from that run's `fills.parquet` (`sum(price*quantity)`
for notional, plus `sum(fees)` and `sum(impact)` for all-in):

| measure | value |
|---|---:|
| buy fills / units / distinct assets | 38 / 74 / 38 |
| notional | $7,423.07 (74.2% of $10,000) |
| fees | $38.00 |
| impact | $2,530.36 |
| **all-in capital consumed** | **$9,991.43 (99.9%)** |

The plan gated on ≥90% all-in deployment before any strategy was allowed to
run against this bar. 99.9% clears it.

**Two things in that table deserve to be drawn out rather than buried.**

*Impact is 34.1% of notional* ($2,530.36 / $7,423.07). The buy floor makes
entering the sealed universe far more expensive than the old model priced it,
and that cost is now visible instead of silently absent.

*Only 38 of the 100 screened assets ended up holding units*, where the design
spec's day-one measurement (equal-dollar plus sweep, no buy floor yet)
predicted 73 at `n_assets=100`. The floor raises the all-in cost per unit, so
the same $10,000 reaches fewer assets. **The repaired benchmark is therefore
less diversified than the design table suggested** — 38 assets, not 73.
Deployment is what the gate measured and it holds; breadth is not, and it
fell.

**A third fact, found while checking the two above, and it is a caveat on the
bar itself: the benchmark is warmup-sensitive.** The screen reads
`ctx.history` for print density and for the quote feeding its spread, and
`warmup_days` is exactly how much history exists at the first bar. Measured
on the native engine, same window, cash, kind and impact setting, varying
only `warmup_days`:

| warmup_days | fills | final equity | total return |
|---:|---:|---:|---:|
| 0 (`pkmn backtest`'s only setting) | 38 | $12,492.97 | **+24.93%** |
| 120 (`pkmn walkforward`'s default, and `parity_full.py`'s) | 42 | $11,607.09 | +16.07% |

The warmup-0 row reproduces run `20260807T135424Z-8362bc` exactly, which is
how the pair was checked. **The published bar is the warmup-0 number, because
`pkmn backtest` exposes no `--warmup-days` flag and `run_single_backtest`
defaults to 0.** Note the asymmetry this creates in the comparison table
below: the six strategy rows are walk-forward runs at `warmup_days=120`,
while the bar is a backtest at 0. That asymmetry is not new — every prior
benchmark figure in this file came from `pkmn backtest` the same way — but
the screen makes buy-and-hold newly sensitive to it, so it is worth stating
that "+24.93%" carries an implicit `warmup_days=0` and a re-run under a
different warmup will not reproduce it.

### +24.93% is not comparable to +183.67% or +10.53%

These three numbers are the same command over the same window, and it is a
mistake to read them as a time series of market performance. They differ
primarily in **how much of the account was invested**, which is an allocation
rule, not a market fact:

| benchmark run | universe | allocation | buy floor | notional deployed | all-in deployed | headline |
|---|---|---|---|---:|---:|---:|
| `20260729T200904Z-4aeaa9` | 23 groups | equal-weight, all | no | $6,012.55 (60.1%) | $6,296.27 (63.0%) | **+183.67%** |
| `20260804T061600Z-37a23f` | 217 groups | equal-weight, all | no | $466.78 (4.7%) | $550.77 (5.5%) | **+10.53%** |
| `20260807T135424Z-8362bc` | 217 groups | screened top-100 + sweep | yes | $7,423.07 (74.2%) | $9,991.43 (99.9%) | **+24.93%** |

All three are impact-on. The first two rows are taken from this file's Full
set coverage section (its capital-deployment table), which is where they were
derived; only the third is new here.

**+24.93% is the first of the three that is a return on a substantially
invested account**, so it is the first that can be read as "what holding
sealed did". The other two are returns on 63% and 5.5% of the money, with the
rest in a zero-yield cash sleeve. Do not construct a narrative out of
183.67 → 10.53 → 24.93: the movement is an allocation rule changing under a
fixed market, and only the last figure is a bar. Every published number from
the previous plan was measured against the 4.7%-deployed benchmark and is
superseded as a comparison point.

Its risk metrics change character for the same reason. The 4.7% benchmark
reported a Sharpe of 6.57 and a max drawdown of −0.90%, which were
money-market artifacts of a 95%-cash account, not risk-adjusted quality. The
repaired benchmark reports Sharpe 0.63 and max drawdown −25.73% — the
drawdown of an actual long sealed position. That looks worse and is more
honest. (The standing mark-smoothing caveat applies to both: thin markets and
carry-forward marks inflate every Sharpe in this file.)

### Before/after: stitched OOS total return, all six strategies plus the benchmark

All runs `--start 2024-03-01 --end 2026-06-30 --trials 15`, 11 folds, market
impact **on**, 217-group universe on both sides. The "before" column is taken
from the Full set coverage section's post-swap column, which is the most
recent impact-on set and shares this section's universe and window. **For the
six strategy rows the only instrument difference is the buy floor** — a
strategy's stitched OOS return never reads the benchmark (nothing in
`research/walkforward.py` or `research/search.py` touches buy-and-hold), and
every other change on the branch is off the backtest path: `buy_and_hold`
itself, the same floor mirrored into the live paper-fill planner
(`live/paper.py`), the new `research/horizon.py` and its CLI command, and an
`(asset, day)` uniqueness guard in the C++ market view that throws on
violation rather than altering behaviour (no run hit it — every run
completed). The benchmark row is a single full-window `pkmn
backtest`, not a walk-forward, on both sides, and that row *does* move on
both axes at once.

| strategy | run id (after) | before (no floor) | after (floor) | IS mean CAGR | OOS mean CAGR | gap |
|---|---|---:|---:|---:|---:|---:|
| sealed-accumulation | `20260808T215853Z-b10d5e` | −8.59% | **−10.31%** | +3.12% | −0.97% | 0.1186 |
| xs-momentum | `20260808T220726Z-4b7346` | −38.72% | **−54.46%** | −5.60% | −6.14% | 0.1098 |
| ml-ranker | `20260808T231711Z-415dc2` | −81.64% | **−84.48%** | −24.94% | −14.18% | 0.0487 |
| ml-ranker-v2 | `20260809T013624Z-98cf5a` | −99.81% | **−99.43%** | −47.61% | −27.37% | −0.0600 |
| cost-aware-reversion | `20260808T221152Z-4766a9` | −98.70% | **−99.98%** | −34.77% | −46.00% | 0.3047 |
| dip-buyer | `20260808T220318Z-15d706` | −99.80% | **−100.00%** | −74.28% | −54.39% | 0.0086 |
| **buy-and-hold sealed** (backtest) | `20260807T135424Z-8362bc` | +10.53% (4.7% deployed) | **+24.93% (99.9% deployed)** | — | — | — |

Timings: the four native walk-forwards took ~4.5 minutes each; ml-ranker 65
minutes and ml-ranker-v2 139 minutes, both `--workers 1` under
`caffeinate -dimsu`.

### Five of six got worse. That is the instrument working, not a regression

The design spec predicted this in advance and committed in writing to not
treating it as failure ("Expected consequence, stated in advance": *"A plan
built on this design must not treat a worse result as failure."*). It
happened, and it is recorded here as predicted rather than discovered.

The mechanism is direct. dip-buyer and cost-aware-reversion select on
`market`, so they still see a $71.88 print as a bargain — and now pay the
$1,800 that was the cheapest listing actually available that day. The
"bargains" the dip-selectors were finding stopped being buyable at the price
that made them look like bargains. Compounding it, positions still **mark**
at `market` (deliberately unchanged), so buying at the ask and marking at the
last sale books an immediate unrealised loss. That combination is correct:
you really did pay the offer, and the position really is worth what
comparable units are selling for.

What changed is not that the strategies became worse traders. It is that the
number is now a real price paid for a real listing instead of an artifact of
walking outward from a price nobody was offering. **The previous numbers were
flattering; the current ones are not punitive.**

ml-ranker-v2 moved the other way, −99.81% → −99.43%. At that magnitude the
difference is noise on a near-total loss, not a signal, and it should not be
cited as the buy floor helping anything.

### Cross-strategy rigor: `pkmn evaluate`

Run `20260809T013934Z-a453f5`, artifacts in
`data/results/evaluate-2026-08-08/`, 660 aligned days (2024-08-28 ..
2026-06-18), benchmark `data/results/20260807T135424Z-8362bc` — the repaired
benchmark, confirmed in the report header rather than assumed.

| strategy | OOS total return | 95% CI | Sharpe (ann.) | deflated Sharpe |
|---|---:|---|---:|---:|
| cost-aware-reversion | −99.98% | [−100.00%, −92.14%] | −0.42 | 0.097 |
| dip-buyer | −100.00% | [−100.00%, −98.58%] | −1.19 | 0.012 |
| ml-ranker | −84.48% | [−99.47%, +177.32%] | −0.11 | 0.155 |
| ml-ranker-v2 | −99.43% | [−100.00%, −36.03%] | −1.14 | 0.001 |
| sealed-accumulation | −10.31% | [−24.22%, +4.57%] | −0.98 | 0.003 |
| xs-momentum | −54.46% | [−81.44%, −6.08%] | −1.34 | 0.000 |

**White's Reality Check, jointly over all six: p = 1.0000** (seeded
stationary block bootstrap, n_boot=10000, mean block 10 days, seed 42). The
observed best excess return sat at or below every one of 10,000 luck-only
recentered resample maxima. Every deflated Sharpe is far below the 0.5
coin-flip point, let alone the conventional 0.95 bar. ml-ranker's 0.155 is
the highest and sits beside a 95% CI spanning [−99.47%, +177.32%] — large
because the estimate is uninformative, not because the strategy is good.

The p-value moved from the previous branch's corrected 0.8038 back to 1.0000.
That is expected and is not itself a finding: the benchmark it is measured
against went from a 5.5%-deployed cash sleeve to a fully invested position
returning +24.93%, so beating it got harder. The two p-values are measured
against two different bars.

**A live trap, recorded because it has already published a wrong number
once.** `pkmn evaluate` still resolves its benchmark by globbing
`data/results/buy-and-hold-sealed-*` before falling back to the run registry
(`cli.py:837-860`) — the exact mechanism that published p = 1.0000 instead of
0.8038 on the previous branch. It picked the right benchmark **this time only
because** the stale directories were renamed `STALE-preswap-23group-*` there,
so the glob matched nothing and the registry fallback fired. **The code was
never fixed.** Anyone who re-creates a `buy-and-hold-sealed-*` directory name
re-arms it. `pkmn horizon` deliberately does not work this way: it resolves
benchmarks by run id from the registry only, and refuses a benchmark whose
window does not span the run's.

### `pkmn horizon`: the measurement this branch exists to produce

**Read the method caveat before the numbers.** The plan intended to run
horizon on the walk-forward artifacts. It cannot: a walk-forward artifact
carries `stitched_equity.parquet` and **no `fills.parquet`**, and horizon
pairs fills. So each strategy was re-run as a **default-parameter full-window
backtest** over the same window, and horizon was run on that. **These are not
the walk-forward parameter sets.** What they gain is that no tuned parameter
can contaminate the per-trade comparison; what they lose is that they are not
the same runs as the walk-forward table above. Read them as a separate
measurement, not as a decomposition of the walk-forward returns.

The source runs, with their own full-window totals for context (impact on,
$10,000, no starting holdings):

| strategy | horizon source run | full-window total return |
|---|---|---:|
| buy-and-hold sealed (the index) | `20260807T135424Z-8362bc` | +24.93% |
| sealed-accumulation | `20260809T014019Z-c45150` | +5.57% |
| cost-aware-reversion | `20260809T014422Z-a9749a` | −98.55% |
| ml-ranker | `20260809T020349Z-903506` | −99.75% |
| xs-momentum | `20260809T014340Z-2abc05` | −99.67% |
| dip-buyer | `20260809T014257Z-e58d69` | −99.74% |
| ml-ranker-v2 | `20260809T100121Z-0f45f1` | −99.82% |

Every trip is scored against benchmark run `20260807T135424Z-8362bc`, whose
git sha is recorded in each horizon report — buy-and-hold's allocation rule
changed on this branch, so the strategy name alone no longer identifies the
bar.

**Per-strategy summary. Sample size is part of every number here and must
travel with it.**

| strategy | round trips | still open at 2026-06-30 | median edge (all trips) | mean edge | win rate | **1-3m median edge** | **trips in 1-3m** |
|---|---:|---:|---:|---:|---:|---:|---:|
| sealed-accumulation | 33 | 10 | −10.98% | −11.16% | 33.3% | **+13.90%** | **1** |
| cost-aware-reversion | 132 | 10 | −39.25% | −30.79% | 15.9% | **−16.57%** | **41** |
| ml-ranker | 145 | 0 | −33.63% | −23.27% | 20.7% | **−37.96%** | **109** |
| xs-momentum | 106 | 1 | −44.02% | −41.66% | 6.6% | **−45.33%** | **99** |
| dip-buyer | 175 | 0 | −46.12% | −39.60% | 9.7% | **−51.21%** | **116** |
| ml-ranker-v2 | 20 | 0 | −84.32% | −77.76% | **0.0%** | **−84.32%** | **20** |

Median edge per holding-period bucket, trip count in parentheses. Buckets are
`<1m` = under 30 days, `1-3m` = 30-89, `3-6m` = 90-179, `>6m` = 180+
(`research/horizon.py` `_bucket_for`):

| strategy | <1m | 1-3m | 3-6m | >6m |
|---|---|---|---|---|
| sealed-accumulation | −12.40% (5) | **+13.90% (1)** | +6.91% (8) | −22.43% (19) |
| dip-buyer | −28.92% (59) | −51.21% (116) | n/a (0) | n/a (0) |
| xs-momentum | n/a (0) | −45.33% (99) | +55.40% (5) | −83.63% (2) |
| cost-aware-reversion | −13.29% (30) | −16.57% (41) | −65.10% (60) | −37.24% (1) |
| ml-ranker | n/a (0) | −37.96% (109) | −17.28% (20) | +4.75% (16) |
| ml-ranker-v2 | n/a (0) | −84.32% (20) | n/a (0) | n/a (0) |

#### Reading the 1-3 month band honestly

**The only positive 1-3m figure in the table is sealed-accumulation's
+13.90%, and it is one round trip** — against that same strategy's all-trip
median of −10.98%. One trade is an anecdote. It is reported because it is
what the data says, and it must never appear anywhere without its sample size
beside it. The other scattered positives are the same kind of number:
xs-momentum's 3-6m +55.40% rests on 5 trips and sealed-accumulation's 3-6m
+6.91% on 8.

**The five strategies with a real sample in the band — 20 to 116 trips — lose
to simply holding the index over the identical dates by 16.6 to 84.3
percentage points per trade.** Median, mean and win rate all point the same
way, so this is not an artifact of one summary statistic.

Win rate *within* the 1-3m band, counted directly off the `trips` arrays in
each `horizon.json` using the same bucket rule (the counts reproduce the
published bucket sizes exactly):

| strategy | trips in band that beat the index |
|---|---|
| sealed-accumulation | 1 / 1 (the single trip above) |
| cost-aware-reversion | 9 / 41 (22.0%) |
| ml-ranker | 14 / 109 (12.8%) |
| xs-momentum | 2 / 99 (2.0%) |
| dip-buyer | 1 / 116 (0.9%) |
| ml-ranker-v2 | 0 / 20 (0.0%) |

ml-ranker-v2 beat the index on **none** of its 20 trades, every one of which
fell in the 1-3m band.

Two structural notes on the band. dip-buyer and ml-ranker-v2 have *no* trips
past 3 months at all, so their 1-3m number is nearly their whole record
rather than a slice of it. cost-aware-reversion has 4 still-open trips inside
its 41-trip band (closed at the final mark and flagged in the artifact); the
other five strategies have none in-band, so only cost-aware-reversion's band
figure mixes marks with realized exits.

#### What a per-trip edge is and is not

It is one round trip's return, net of the fees and impact recorded on both
fills, minus the sealed index's return over those same two dates. It is
**not** a portfolio return: it ignores how much capital sat idle between
trips, and it weights a one-unit trade the same as a twenty-unit one. A
strategy could in principle post a positive median edge and still lose money,
or the reverse. Here the two agree — every strategy with a negative median
edge also lost at the portfolio level, and sealed-accumulation's +5.57%
full-window return still trails the benchmark's +24.93% — so nothing rests on
the distinction, but it should not be forgotten the next time only one of the
two is positive.

The standing mark-smoothing caveat applies with full force: a still-open
trip's return is a mark, not a realized exit, and marks on this data are
carried forward through thin markets.

### The honest conclusion

**The owner's question now has a measured answer, and the answer is no.**

Over the 1-3 month band, measured per round trip against holding the sealed
index over that trip's own dates, on a benchmark that is 99.9% invested, with
buys priced at the cheapest listing that actually existed, **none of the six
strategies beats holding sealed — and not narrowly.** The strategies with
real samples in the band lose by 16.6 to 84.3 percentage points per trade and
beat the index on 0.9% to 22.0% of their trades. The single positive 1-3m
figure in the whole study is one trade.

The horizon framing was built specifically to remove the excuse that the old
28-month comparison was unfair on exposure time. The excuse is gone and the
strategies still lose.

What this branch delivered:

- **A bar worth measuring against.** 99.9% deployed instead of 4.7%, +24.93%
  over the window, with a −25.73% drawdown that belongs to a real long
  position rather than to a cash sleeve.
- **Fills priced at something you could actually transact at**, and the
  resolution of an open question about `market < low` that turned out to be a
  category error in the consumer rather than a property of the data.
- **A per-trade measurement matched to the holding period the owner cares
  about**, which no prior section in this file could produce.

It delivered **no strategy**. Nothing here is a candidate for real money, and
the correct reading of the five-of-six worsening is that the previous numbers
were measured with a broken instrument, not that anything degraded.

### Deliberate deviations from the design spec

Both are behaviour the spec called for differently; both are intentional and
recorded here rather than left to be discovered.

1. **`n_assets` is not walk-forward-searchable.** The spec put it on the
   registry entry "like every other hyperparameter — CLI-settable,
   walk-forward-searchable, and declarative". It is CLI-settable and
   declarative (`BUY_AND_HOLD_PARAMS` in `research/backtest_run.py`, bounds
   [1, 10000], default 100) but deliberately **not** in `REGISTRY`, so no
   optuna search space exists for it. Reason: buy-and-hold is the bar, not a
   candidate. Tuning the bar to maximise its own return would make every
   comparison against it self-serving, and tuning it to anything else would
   make it arbitrary. A fixed, declared, auditable `n_assets` is the point.
2. **The buy floor is gated on `impact_enabled`**
   (`engine/execution.py:86-102`, `engine/costs.py` `buy_impact`). The floor
   premium is routed through the `impact` column, so with impact off there is
   no column to charge it to; worse, clipping affordability off a floored base
   with no offsetting impact term cut real quantities by up to 10x on a 10x
   floor. Gating keeps `--no-impact` runs bit-identical to the pre-floor
   engine. **The consequence is real and must be stated: a `--no-impact` run
   again sizes and prices buys at a sales print that may sit below any live
   offer.** Flat-cost runs are a comparability tool, not a model of executable
   prices — do not read a `--no-impact` fill as a price you could get.

### Known live traps and follow-ups

- **`pkmn evaluate`'s benchmark glob is still unfixed** (`cli.py:837-860`).
  It resolved correctly here only because no directory matched the pattern.
  Prefer `--benchmark data/results/<run_id>` when the answer matters, and
  never create a `buy-and-hold-sealed-*` directory by hand.
- **Walk-forward artifacts carry no `fills.parquet`**, so `pkmn horizon`
  cannot read a walk-forward run. Every horizon number above therefore comes
  from a default-parameter backtest. Writing per-fold fills into the
  walk-forward artifact would let horizon score the tuned parameter sets; that
  is unbuilt.
- **`pkmn horizon` cannot analyse a run started with `--holdings`.** Seeded
  positions have no matching buy, so FIFO pairing has nothing to pair the exit
  against; it fails cleanly with an error rather than silently dropping the
  trip.
- **The ml-ranker parity bridge was not re-verified on this branch.** Only the
  five native strategies appeared in the `parity_full.py` output quoted above.
- **The benchmark is more concentrated than designed** — 38 assets holding
  units where the pre-floor day-one measurement predicted 73. Whether
  `n_assets` should be raised to restore breadth is unmeasured.
- **The bar is warmup-sensitive and `pkmn backtest` has no `--warmup-days`
  flag.** +24.93% is the warmup-0 figure; warmup 120 gives +16.07% on the
  same window. Whether the bar should be produced at the walk-forward's
  warmup (120) instead, so bar and candidates share it, is a real question
  this branch did not answer.
- **Open questions 1 and 3 from the Full set coverage section remain open:**
  the two ML losses are still untraced, and no liquidity-floor experiment has
  been run. The screen shipped here is the machinery question 3 needs.
