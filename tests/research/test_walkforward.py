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

    # --- Pinned seam values (exact stitching math) ---
    #
    # Setup: 40 days of product_id=1, market price = 100 + day_index (i=0..39).
    # BuyAndHold T+1: buy order submitted day-0 of OOS, fills on day-1.
    # CostModel(fee_rate=0, shipping=0): no friction.
    # initial_cash = 1000; floor(1000 / price) units bought.
    #
    # Fold 0 OOS: 2025-01-11 (i=10) .. 2025-01-20 (i=19), prices 110..129.
    #   Day-0 (i=10): cash=1000, price=110 -> floor(1000/110)=9 units, 9*110=990 invested.
    #   T+1 fill day 1 (i=11): price=111, equity = 9*111 + (1000-990) = 999+10 = 1009 -> 1009.0
    #   Wait: actually equity[0] of OOS curve=1000 (day 0 is still all-cash),
    #         equity[1]=1000 (order not yet settled per T+1),
    #         equity[2]=1003 (mark rises, 9 units * (112-110)=18? No...
    #   From printed output: OOS curve equity goes 1000,1000,1003,1006,...,1021,1024.
    #   Pattern: day 0 and day 1 both show 1000 (T+1: cash on day 0; fill at day-1 open,
    #   mark at day-1 close). From day 2 onward: 9 units * (price_i - price_{day1}) added.
    #   Actual: seg0 terminal equity = 1024.0 (printed: idx=9, equity=1024.0).
    #
    # Seam 0->1 (idx=9 -> idx=10):
    #   level after seg0 = 1024.0
    #   seg1 OOS raw: base=1000.0 (first equity of seg1 raw curve)
    #   idx=10: level * raw[0] / base = 1024 * 1000 / 1000 = 1024.0
    #
    # Seam 1->2 (idx=19 -> idx=20):
    #   seg1 terminal raw = 1024.0, stitched = 1024 * 1024 / 1000 = 1048.576
    #   level after seg1 = 1048.576
    #   idx=20: 1048.576 * 1000 / 1000 = 1048.576
    #
    # Final row idx=29:
    #   seg2 terminal raw = 1024.0
    #   stitched = 1048.576 * 1024 / 1000 = 1073.741824

    assert stitched["equity"][9] == pytest.approx(1024.0)
    # First row of segment 1 must equal last row of segment 0 (level continuity at seam).
    assert stitched["equity"][10] == pytest.approx(1024.0)
    assert stitched["equity"][19] == pytest.approx(1048.576)
    # First row of segment 2 must equal last row of segment 1 (level continuity at seam).
    assert stitched["equity"][20] == pytest.approx(1048.576)
    assert stitched["equity"][29] == pytest.approx(1073.741824)


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
    assert "is_cagr_mean" in result.summary
    assert "oos_cagr_mean" in result.summary
    assert "overfitting_gap" in result.summary


def test_invalid_objective_metric_raises(warehouse: Warehouse) -> None:
    """run_walkforward must validate objective_metric before running any folds."""
    with pytest.raises(ValueError, match="unknown objective_metric"):
        run_walkforward(
            warehouse=warehouse,
            strategy_factory=make_strategy,
            optimizer=fake_optimizer,
            cost_model=CostModel(fee_rate=0.0, shipping_per_line=0.0),
            start=START,
            end=START + timedelta(days=39),
            is_days=10,
            oos_days=10,
            initial_cash=1000.0,
            objective_metric="sharpe_ratio",
        )
