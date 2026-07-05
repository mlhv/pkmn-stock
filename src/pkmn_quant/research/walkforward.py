"""Walk-forward: per fold, optimize in-sample, freeze, run out-of-sample, stitch.

The stitched curve is built ONLY from out-of-sample segments — it is the
closest a backtest gets to 'how this would actually have gone'. The gap
between mean IS and mean OOS CAGR measures overfitting on an annualized basis
so unequal IS/OOS window lengths (e.g. 180d vs 60d) do not fake a gap. Note
that CAGR on short OOS windows (~60d) is noisy, so read the gap as an
order-of-magnitude signal, not a precise number.

Design note: ``Params`` is defined locally (not imported from search.py) so
that this module does not pull in optuna at import time. Callers that use
``optimize_params`` from search.py will have optuna loaded already; callers
that inject a trivial fake optimizer (e.g. tests) pay no import cost.

Warm-up semantics (``warmup_days`` parameter):
  History is loaded from ``window_start - warmup_days`` through ``window_end``
  for every fold (both IS and OOS).  The event loop still iterates only days
  in [window_start, window_end] — no trades occur during the warm-up period.
  This means look-back strategies (momentum, dip windows, peak-to-date) have
  price history on the very first bar of each window rather than starting blind.

  IS and OOS windows both receive the same warm-up so the optimizer's view of
  signal behaviour matches what the OOS run will see.  The library default is
  ``warmup_days=0`` (no warm-up, backwards compatible); the CLI default is 120
  (covers the longest supported momentum lookback).

  If ``warmup_days`` exceeds the available history before a fold, the load
  silently clamps to whatever data exists — no error is raised.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import polars as pl

from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.backtest import Backtest
from pkmn_quant.engine.costs import CostModel
from pkmn_quant.engine.metrics import summarize
from pkmn_quant.engine.strategy import Strategy
from pkmn_quant.research.folds import Fold, make_folds

# Flat mapping of hyperparameter name -> numeric value (float or int).
# Matches the Params alias in search.py; kept local to avoid the optuna import.
Params = dict[str, float | int]

StrategyFactory = Callable[[Params], Strategy]
# optimizer(fold, evaluate) -> best params; evaluate(params) -> IS metric.
Optimizer = Callable[[Fold, Callable[[Params], float]], Params]

# Schema for an empty stitched curve so summarize() always receives typed columns.
_CURVE_SCHEMA = pl.Schema({"date": pl.Date, "equity": pl.Float64})


@dataclass(frozen=True)
class FoldResult:
    fold: Fold
    params: Params
    is_summary: dict[str, float]
    oos_summary: dict[str, float]
    oos_curve: pl.DataFrame


@dataclass(frozen=True)
class WalkForwardResult:
    folds: list[FoldResult]
    stitched_curve: pl.DataFrame
    summary: dict[str, float]


def run_walkforward(
    warehouse: Warehouse,
    strategy_factory: StrategyFactory,
    optimizer: Optimizer,
    cost_model: CostModel,
    start: date,
    end: date,
    is_days: int,
    oos_days: int,
    initial_cash: float,
    objective_metric: str = "total_return",
    warmup_days: int = 0,
) -> WalkForwardResult:
    """Run walk-forward optimization and return stitched OOS equity curve.

    For each fold produced by make_folds:
    1. Call optimizer to find best params over the IS window.
    2. Re-run IS with those params to record IS metrics.
    3. Run OOS with those params to record OOS metrics and the equity curve.

    The OOS segments are then stitched into a single compounding equity curve.

    ``warmup_days`` is passed to all three Backtest runs per fold (evaluate
    closure, IS re-run, OOS run) so the optimizer's view of signal behaviour
    matches the OOS deployment.  See module docstring for full warm-up semantics.
    """
    valid = {"total_return", "cagr", "sharpe", "sortino", "calmar", "max_drawdown"}
    if objective_metric not in valid:
        raise ValueError(
            f"unknown objective_metric {objective_metric!r}; choose from {sorted(valid)}"
        )

    fold_results: list[FoldResult] = []

    for fold in make_folds(start, end, is_days=is_days, oos_days=oos_days):

        def evaluate(params: Params, _fold: Fold = fold) -> float:
            result = Backtest(
                warehouse=warehouse,
                strategy=strategy_factory(params),
                cost_model=cost_model,
                start=_fold.is_start,
                end=_fold.is_end,
                initial_cash=initial_cash,
                warmup_days=warmup_days,
            ).run()
            return float(result.summary[objective_metric])

        best = optimizer(fold, evaluate)

        is_result = Backtest(
            warehouse=warehouse,
            strategy=strategy_factory(best),
            cost_model=cost_model,
            start=fold.is_start,
            end=fold.is_end,
            initial_cash=initial_cash,
            warmup_days=warmup_days,
        ).run()

        oos_result = Backtest(
            warehouse=warehouse,
            strategy=strategy_factory(best),
            cost_model=cost_model,
            start=fold.oos_start,
            end=fold.oos_end,
            initial_cash=initial_cash,
            warmup_days=warmup_days,
        ).run()

        fold_results.append(
            FoldResult(
                fold=fold,
                params=best,
                is_summary=is_result.summary,
                oos_summary=oos_result.summary,
                oos_curve=oos_result.equity_curve,
            )
        )

    stitched = _stitch([f.oos_curve for f in fold_results], initial_cash)
    summary = _summarize_folds(fold_results, stitched)
    return WalkForwardResult(folds=fold_results, stitched_curve=stitched, summary=summary)


def _stitch(curves: list[pl.DataFrame], initial_cash: float) -> pl.DataFrame:
    """Chain OOS segments: each segment's returns compound on the prior terminal.

    Each segment is rescaled so its first equity value equals the running level,
    then advances that level to the segment's last rescaled value.

    Empty curves list: returns a typed empty DataFrame so summarize() can handle
    it gracefully (summarize returns all-zero metrics for n < 2 rows).

    Seam assumptions:
    - Positions are effectively valued at mark at each segment boundary; sell
      costs (fees/shipping) are never paid at seams, so the stitched curve is an
      upper bound on realized compounding.
    - Each segment runs from initial_cash; stitching is a display rescaling —
      strategies never see accumulated profits (no capacity/sizing effects across
      segments).
    """
    if not curves:
        return pl.DataFrame(schema=_CURVE_SCHEMA)

    days: list[date] = []
    equity: list[float] = []
    level = initial_cash

    for curve in curves:
        eq = curve.sort("date")
        if eq.height == 0:
            raise ValueError(
                "OOS segment produced an empty equity curve;"
                " check inputs (date range, warehouse data)"
            )
        base = float(eq["equity"][0])
        if base <= 0.0:
            raise ValueError(
                f"OOS segment starting {eq['date'][0]} has non-positive base equity {base}"
            )
        for d, e in zip(eq["date"].to_list(), eq["equity"].to_list(), strict=True):
            days.append(d)
            equity.append(level * float(e) / base)
        level = equity[-1]

    if not days:
        return pl.DataFrame(schema=_CURVE_SCHEMA)

    return pl.DataFrame({"date": days, "equity": equity}, schema=_CURVE_SCHEMA)


def _summarize_folds(
    folds: list[FoldResult],
    stitched: pl.DataFrame,
) -> dict[str, float]:
    """Aggregate IS/OOS metrics and compute the overfitting gap.

    overfitting_gap = mean IS CAGR - mean OOS CAGR (annualized).
    Using CAGR makes IS and OOS windows horizon-comparable: a constant-edge
    strategy with unequal IS/OOS lengths (e.g. 180d vs 60d) would show a
    spurious gap if raw total_return were used. A large positive gap indicates
    the optimizer is fitting to noise.
    """

    def _mean(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    is_tr_mean = _mean([f.is_summary["total_return"] for f in folds])
    oos_tr_mean = _mean([f.oos_summary["total_return"] for f in folds])
    is_cagr_mean = _mean([f.is_summary["cagr"] for f in folds])
    oos_cagr_mean = _mean([f.oos_summary["cagr"] for f in folds])

    # summarize() returns all-zero dict for frames with < 2 rows, so it is
    # always safe to call here — even for an empty stitched curve.
    stitched_metrics = {f"stitched_{k}": v for k, v in summarize(stitched).items()}

    return {
        **stitched_metrics,
        "is_total_return_mean": is_tr_mean,
        "oos_total_return_mean": oos_tr_mean,
        "is_cagr_mean": is_cagr_mean,
        "oos_cagr_mean": oos_cagr_mean,
        "overfitting_gap": is_cagr_mean - oos_cagr_mean,
    }
