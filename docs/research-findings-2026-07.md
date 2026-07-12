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
