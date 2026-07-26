"""Capability tests for run_single_backtest (Plan 2b). Native engine only."""

from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA
from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.native import SeededHolding
from pkmn_quant.engine.portfolio import Asset
from pkmn_quant.research.backtest_run import (
    BacktestRunResult,
    resolve_params,
    run_single_backtest,
)

START = date(2025, 1, 1)
PRODUCTS = pl.DataFrame(
    {
        "product_id": [1],
        "group_id": [1],
        "name": ["Box A"],
        "rarity": [None],
        "kind": ["sealed"],
        "released_on": [date(2024, 11, 1)],
    }
)


def _seed(root: Path, days: int = 6) -> None:
    w = Warehouse(Paths(root=root))
    for i in range(days):
        day = START + timedelta(days=i)
        market = 10.0 + i
        w.write_prices(
            day,
            pl.DataFrame(
                [
                    {
                        "date": day,
                        "product_id": 1,
                        "sub_type": "Normal",
                        "low": round(market * 0.9, 2),
                        "mid": round(market * 1.15, 2),
                        "high": round(market * 3.0, 2),
                        "market": market,
                    }
                ],
                schema=PRICE_SCHEMA,
            ),
        )
    w.write_products(PRODUCTS)


def _run(root: Path, strategy: str, params=None, holdings=None, cash=1000.0):
    return run_single_backtest(
        root=root,
        strategy_name=strategy,
        params=params or {},
        cash=cash,
        holdings=holdings or [],
        start=START,
        end=START + timedelta(days=5),
    )


# --- resolve_params -------------------------------------------------------


def test_resolve_params_fills_defaults() -> None:
    p = resolve_params("sealed-accumulation", {})
    assert p == {"min_drawdown": 0.25, "take_profit": 1.5, "min_age_days": 60}


def test_resolve_params_applies_override_and_coerces() -> None:
    p = resolve_params("sealed-accumulation", {"min_age_days": 90.0, "take_profit": 2.0})
    assert p["min_age_days"] == 90 and isinstance(p["min_age_days"], int)
    assert p["take_profit"] == 2.0


def test_resolve_params_rejects_unknown_key() -> None:
    with pytest.raises(ValueError, match="unknown"):
        resolve_params("sealed-accumulation", {"nope": 1})


def test_resolve_params_rejects_out_of_bounds() -> None:
    with pytest.raises(ValueError, match="out of range"):
        resolve_params("sealed-accumulation", {"min_drawdown": 0.99})


def test_resolve_params_accepts_string_params_in_range() -> None:
    # The CLI (`--param k=v`) passes every value as a string; this is the
    # coercion path that path relies on, pinned separately from the
    # numeric-input tests above.
    p = resolve_params("sealed-accumulation", {"min_age_days": "90.0", "take_profit": "2.0"})
    assert p["min_age_days"] == 90 and isinstance(p["min_age_days"], int)
    assert p["take_profit"] == 2.0 and isinstance(p["take_profit"], float)


def test_resolve_params_rejects_out_of_bounds_string() -> None:
    with pytest.raises(ValueError, match="out of range"):
        resolve_params("sealed-accumulation", {"min_drawdown": "0.99"})


def test_resolve_params_rejects_non_numeric_string() -> None:
    with pytest.raises(ValueError, match="min_drawdown"):
        resolve_params("sealed-accumulation", {"min_drawdown": "abc"})


def test_resolve_params_buy_and_hold_takes_none() -> None:
    assert resolve_params("buy-and-hold", {}) == {}
    with pytest.raises(ValueError, match="no tunable"):
        resolve_params("buy-and-hold", {"x": 1})


def test_resolve_params_unknown_strategy() -> None:
    with pytest.raises(ValueError, match="unknown strategy"):
        resolve_params("does-not-exist", {})


# --- run_single_backtest --------------------------------------------------


def test_runs_rule_strategy_writes_artifacts_and_record(tmp_path: Path) -> None:
    _seed(tmp_path)
    out = _run(tmp_path, "sealed-accumulation")
    assert isinstance(out, BacktestRunResult)
    assert out.run_id is not None
    assert out.artifact_dir == tmp_path / "data" / "results" / out.run_id
    assert (out.artifact_dir / "equity.parquet").exists()
    assert (out.artifact_dir / "fills.parquet").exists()
    from pkmn_quant.research.runs import load_runs

    rec = load_runs(tmp_path)[-1]
    assert rec.run_id == out.run_id
    assert rec.config["strategy"] == "sealed-accumulation"
    assert rec.config["params"]["min_drawdown"] == 0.25


def test_runs_buy_and_hold_default(tmp_path: Path) -> None:
    _seed(tmp_path)
    out = _run(tmp_path, "buy-and-hold")
    assert out.run_id is not None
    assert out.result.summary  # has metrics


def test_runs_ml_strategy_via_bridge(tmp_path: Path) -> None:
    # ml-ranker runs through the Python callback bridge (not a native port).
    # Over this short window it never accumulates enough history to train, so
    # it trades little/nothing — but the bridge path is exercised and the run
    # completes. Params stay within ParamSpec bounds (defaults here).
    _seed(tmp_path, days=6)
    out = _run(tmp_path, "ml-ranker")
    assert out.run_id is not None


def test_seeded_holding_flows_through(tmp_path: Path) -> None:
    _seed(tmp_path)
    asset = Asset(product_id=1, sub_type="Normal")
    holding = SeededHolding(asset=asset, quantity=3, avg_cost=9.0, opened_on=date(2024, 12, 1))
    out = _run(tmp_path, "buy-and-hold", holdings=[holding], cash=100.0)
    # day-0: no fill yet, equity = 100 + 3*market_day0(10.0) = 130
    assert out.result.equity_curve["equity"].to_list()[0] == 130.0
    from pkmn_quant.research.runs import load_runs

    rec_holdings = load_runs(tmp_path)[-1].config["holdings"]
    assert rec_holdings == [
        {
            "product_id": 1,
            "sub_type": "Normal",
            "quantity": 3,
            "avg_cost": 9.0,
            "opened_on": "2024-12-01",
        }
    ]


def test_holding_outside_universe_raises(tmp_path: Path) -> None:
    _seed(tmp_path)
    ghost = Asset(product_id=999, sub_type="Normal")
    with pytest.raises(ValueError, match="universe"):
        _run(tmp_path, "buy-and-hold", holdings=[SeededHolding(ghost, 1, 5.0, START)])


def test_holding_without_day_one_mark_raises(tmp_path: Path) -> None:
    # Asset 2 first prints on day 3 — in-universe over the window, but no mark
    # on the start bar, so it cannot be a starting holding.
    _seed(tmp_path)
    w = Warehouse(Paths(root=tmp_path))
    late = START + timedelta(days=3)
    w.write_prices(
        late,
        pl.concat(
            [
                w.load_day(late),
                pl.DataFrame(
                    [
                        {
                            "date": late,
                            "product_id": 2,
                            "sub_type": "Normal",
                            "low": 9.0,
                            "mid": 11.5,
                            "high": 30.0,
                            "market": 10.0,
                        }
                    ],
                    schema=PRICE_SCHEMA,
                ),
            ]
        ),
    )
    with pytest.raises(ValueError, match="start"):
        _run(
            tmp_path,
            "buy-and-hold",
            holdings=[SeededHolding(Asset(2, "Normal"), 1, 5.0, START)],
        )
