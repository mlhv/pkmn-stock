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

## Overfitting gap

Positive as expected for the two strategies with tunable edge
(sealed +4.8 CAGR pts, momentum +4.7 pts): parameters look better on the
window they were fit on. The gap is computed on CAGR (annualized) so the
180d IS vs 60d OOS length mismatch does not fabricate a gap; CAGR over 60d
windows is noisy, so treat the gap as an order-of-magnitude signal.

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
