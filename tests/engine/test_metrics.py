from datetime import date

import polars as pl
import pytest

from pkmn_quant.engine.metrics import summarize


def curve(values: list[float]) -> pl.DataFrame:
    days = pl.date_range(date(2025, 6, 1), date(2025, 6, len(values)), interval="1d", eager=True)
    return pl.DataFrame({"date": days, "equity": values})


def test_flat_curve() -> None:
    s = summarize(curve([100.0, 100.0, 100.0]))
    assert s["total_return"] == pytest.approx(0.0)
    assert s["max_drawdown"] == pytest.approx(0.0)


def test_total_return_and_drawdown() -> None:
    s = summarize(curve([100.0, 120.0, 90.0, 108.0]))
    assert s["total_return"] == pytest.approx(0.08)
    assert s["max_drawdown"] == pytest.approx(-0.25)  # 120 -> 90


def test_sharpe_sign() -> None:
    up = summarize(curve([100.0, 101.0, 102.0, 103.0]))
    down = summarize(curve([100.0, 99.0, 98.0, 97.0]))
    assert up["sharpe"] > 0
    assert down["sharpe"] < 0


def test_single_point_curve_degrades_gracefully() -> None:
    s = summarize(curve([100.0]))
    assert s["total_return"] == pytest.approx(0.0)
    assert s["sharpe"] == 0.0


def test_negative_equity_does_not_crash() -> None:
    # Pathological but possible: fees on penny-card sells can drive cash negative.
    s = summarize(curve([100.0, 50.0, -10.0]))
    assert s["cagr"] == pytest.approx(-1.0)
    assert s["total_return"] == pytest.approx(-1.1)
