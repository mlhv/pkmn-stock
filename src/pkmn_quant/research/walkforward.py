"""Walk-forward: per fold, optimize in-sample, freeze, run out-of-sample, stitch.

The stitched curve is built ONLY from out-of-sample segments — it is the
closest a backtest gets to 'how this would actually have gone'. The gap
between mean IS and mean OOS return measures overfitting.

Design note: ``Params`` is defined locally (not imported from search.py) so
that this module does not pull in optuna at import time. Callers that use
``optimize_params`` from search.py will have optuna loaded already; callers
that inject a trivial fake optimizer (e.g. tests) pay no import cost.
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
) -> WalkForwardResult:
    """Run walk-forward optimization and return stitched OOS equity curve.

    For each fold produced by make_folds:
    1. Call optimizer to find best params over the IS window.
    2. Re-run IS with those params to record IS metrics.
    3. Run OOS with those params to record OOS metrics and the equity curve.

    The OOS segments are then stitched into a single compounding equity curve.
    """
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
        ).run()

        oos_result = Backtest(
            warehouse=warehouse,
            strategy=strategy_factory(best),
            cost_model=cost_model,
            start=fold.oos_start,
            end=fold.oos_end,
            initial_cash=initial_cash,
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
    """
    if not curves:
        return pl.DataFrame(schema=_CURVE_SCHEMA)

    days: list[date] = []
    equity: list[float] = []
    level = initial_cash

    for curve in curves:
        eq = curve.sort("date")
        if eq.height == 0:
            continue
        base = float(eq["equity"][0])
        if base <= 0.0:
            continue
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

    overfitting_gap = mean IS total_return - mean OOS total_return.
    A large positive gap indicates the optimizer is fitting to noise.
    """

    def _mean(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    is_mean = _mean([f.is_summary["total_return"] for f in folds])
    oos_mean = _mean([f.oos_summary["total_return"] for f in folds])

    # summarize() returns all-zero dict for frames with < 2 rows, so it is
    # always safe to call here — even for an empty stitched curve.
    stitched_metrics = {f"stitched_{k}": v for k, v in summarize(stitched).items()}

    return {
        **stitched_metrics,
        "is_total_return_mean": is_mean,
        "oos_total_return_mean": oos_mean,
        "overfitting_gap": is_mean - oos_mean,
    }
