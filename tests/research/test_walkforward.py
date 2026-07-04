from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA
from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.costs import CostModel
from pkmn_quant.engine.strategy import Strategy
from pkmn_quant.research.folds import Fold
from pkmn_quant.research.walkforward import WalkForwardResult, run_walkforward
from pkmn_quant.strategies.buy_and_hold import BuyAndHold
from tests.helpers import price_row

START = date(2025, 1, 1)


@pytest.fixture
def warehouse(tmp_path: Path) -> Warehouse:
    w = Warehouse(Paths(root=tmp_path))
    # 40 days of a single sealed product drifting upward.
    for i in range(40):
        d = START + timedelta(days=i)
        w.write_prices(d, pl.DataFrame([price_row(d, 1, 100.0 + i)], schema=PRICE_SCHEMA))
    w.write_products(
        pl.DataFrame(
            {
                "product_id": [1],
                "group_id": [1],
                "name": ["Box"],
                "rarity": [None],
                "kind": ["sealed"],
                "released_on": [START],
            }
        )
    )
    return w


def make_strategy(params: dict[str, float | int]) -> Strategy:
    return BuyAndHold(kind="sealed")


def fake_optimizer(fold: Fold, evaluate: object) -> dict[str, float | int]:
    return {}  # no params to tune; skips optuna entirely


def test_walkforward_stitches_oos_segments(warehouse: Warehouse) -> None:
    result = run_walkforward(
        warehouse=warehouse,
        strategy_factory=make_strategy,
        optimizer=fake_optimizer,
        cost_model=CostModel(fee_rate=0.0, shipping_per_line=0.0),
        start=START,
        end=START + timedelta(days=39),
        is_days=10,
        oos_days=10,
        initial_cash=1000.0,
    )
    assert isinstance(result, WalkForwardResult)
    assert len(result.folds) == 3  # days 0-39: IS 10 + 3 full OOS decades fit
    stitched = result.stitched_curve
    # Stitched curve is continuous: each segment starts where the last ended.
    assert stitched["equity"][0] == pytest.approx(1000.0)
    diffs = stitched.with_columns((pl.col("equity") / pl.col("equity").shift(1) - 1).alias("r"))[
        "r"
    ].drop_nulls()
    assert float(diffs.abs().max()) < 0.10  # no discontinuity spikes at seams
    # Each fold records params and both IS and OOS summaries.
    f = result.folds[0]
    assert f.params == {}
    assert "total_return" in f.oos_summary and "total_return" in f.is_summary


def test_overfitting_gap_computed(warehouse: Warehouse) -> None:
    result = run_walkforward(
        warehouse=warehouse,
        strategy_factory=make_strategy,
        optimizer=fake_optimizer,
        cost_model=CostModel(fee_rate=0.0, shipping_per_line=0.0),
        start=START,
        end=START + timedelta(days=39),
        is_days=10,
        oos_days=10,
        initial_cash=1000.0,
    )
    assert "is_total_return_mean" in result.summary
    assert "oos_total_return_mean" in result.summary
    assert "overfitting_gap" in result.summary
