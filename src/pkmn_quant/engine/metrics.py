"""Honest, homemade performance metrics for an equity curve.

quantstats-style tearsheets arrive in Plan 3; these cover the essentials
with visible math. Annualization uses 365: card prices print every day.
"""

from __future__ import annotations

import math
from typing import cast

import polars as pl

TRADING_DAYS_PER_YEAR = 365


def summarize(equity_curve: pl.DataFrame) -> dict[str, float]:
    """Metrics from a frame with `date` and `equity` columns (sorted by date).

    Sharpe assumes a zero risk-free rate. CAGR is annualized and therefore
    unreliable for curves much shorter than ~30 days.
    """
    eq = equity_curve.sort("date")["equity"]
    n = len(eq)
    if n < 2:
        return {"total_return": 0.0, "cagr": 0.0, "max_drawdown": 0.0, "sharpe": 0.0}

    ratio = float(eq[-1]) / float(eq[0])
    total_return = ratio - 1.0
    years = (n - 1) / TRADING_DAYS_PER_YEAR
    # ratio <= 0 (equity wiped out or negative) would make the fractional
    # power complex; report -1.0 (total loss) instead of crashing.
    cagr = ratio ** (1 / years) - 1.0 if years > 0 and ratio > 0 else -1.0 if ratio <= 0 else 0.0

    running_max = eq.cum_max()
    drawdowns = eq / running_max - 1.0
    max_drawdown_val = drawdowns.min()
    max_drawdown = float(cast(float, max_drawdown_val)) if max_drawdown_val is not None else 0.0

    daily = (eq / eq.shift(1) - 1.0).drop_nulls()
    std_val = daily.std() if len(daily) > 1 else None
    std = float(cast(float, std_val)) if std_val is not None else 0.0
    mean_val = daily.mean()
    sharpe = (
        0.0
        if std == 0.0 or mean_val is None
        else float(cast(float, mean_val)) / std * math.sqrt(TRADING_DAYS_PER_YEAR)
    )

    return {
        "total_return": float(total_return),
        "cagr": float(cagr),
        "max_drawdown": max_drawdown,
        "sharpe": sharpe,
    }
