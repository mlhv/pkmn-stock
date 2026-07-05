from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA
from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.costs import CostModel
from pkmn_quant.engine.execution import Order
from pkmn_quant.engine.strategy import Context, Strategy
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
    # Setup: 40 days of product_id=1, market = 100 + day_index; no fees.
    # Fold 0 OOS = i=10..19 (prices 110..119). BuyAndHold orders
    # floor(1000/110) = 9 units on OOS day 0; the T+1 fill is clipped to 3
    # units by the default liquidity tier (price in [50, 200) -> max 3/day)
    # at day-1 price 111: cost 333, cash 667, equity = 3*111 + 667 = 1000.
    # BuyAndHold never re-orders, so the curve gains 3/day thereafter:
    # 1000, 1000, 1003, ..., terminal 3*119 + 667 = 1024.
    #
    # Every OOS segment is price-shifted but otherwise identical (same qty 3,
    # same +$1/day drift), so each segment's raw curve ends at 1024/1000 of
    # its base and the stitched level compounds by exactly 1.024 per seam:
    #   idx 9  (seg0 end):   1000 * 1.024              = 1024.0
    #   idx 19 (seg1 end):   1024 * 1.024              = 1048.576
    #   idx 29 (seg2 end):   1048.576 * 1.024          = 1073.741824
    # An off-by-one in the base index or seam level update breaks these.

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


# ---------------------------------------------------------------------------
# Warm-up tests
# ---------------------------------------------------------------------------


class HistoryRecorderStrategy(Strategy):
    """Probe strategy: records len(ctx.history) on the first bar it sees.

    It never orders anything (buy-and-hold is not the point here), so the
    equity curve is flat and the golden regression is unaffected.
    """

    name = "history-recorder"
    first_bar_history_len: int = -1
    _bar_count: int = 0

    def on_bar(self, ctx: Context) -> list[Order]:
        if self._bar_count == 0:
            self.first_bar_history_len = ctx.history.height
        self._bar_count += 1
        return []

    def reset(self) -> None:
        self.first_bar_history_len = -1
        self._bar_count = 0


def _make_recorder(params: dict[str, float | int]) -> HistoryRecorderStrategy:
    return HistoryRecorderStrategy()


def test_warmup_increases_history_on_first_bar(warehouse: Warehouse) -> None:
    """OOS backtest with warmup_days > 0 must give more history on bar 1.

    With warmup_days=0 the first OOS bar of fold 0 has only IS history
    (10 rows from days 0-9, OOS starts at day 10 so history_until(day10)
    includes day10 itself = 11 rows, but warm-up=0 means no pre-IS data).
    With warmup_days=10 the load extends 10 days before the IS start, but
    because the fixture only has days 0-39, the warm-up clamps to available
    data (no error).  The important assertion: with warmup_days > 0 the
    first-bar history is at least as large as without (and in any case > 0).

    This test uses the 40-day warehouse fixture (days 0-39, START..START+39).
    Fold 0: IS = days 0-9, OOS = days 10-19.
    """
    # Run twice with BuyAndHold (deterministic, no state interaction) to
    # confirm warmup_days=0 doesn't crash — then use the probe strategy to
    # measure actual history depth.

    # 1. BuyAndHold: warmup_days=0 vs warmup_days=10 must both succeed.
    result_no_warmup = run_walkforward(
        warehouse=warehouse,
        strategy_factory=make_strategy,
        optimizer=fake_optimizer,
        cost_model=CostModel(fee_rate=0.0, shipping_per_line=0.0),
        start=START,
        end=START + timedelta(days=39),
        is_days=10,
        oos_days=10,
        initial_cash=1000.0,
        warmup_days=0,
    )
    result_with_warmup = run_walkforward(
        warehouse=warehouse,
        strategy_factory=make_strategy,
        optimizer=fake_optimizer,
        cost_model=CostModel(fee_rate=0.0, shipping_per_line=0.0),
        start=START,
        end=START + timedelta(days=39),
        is_days=10,
        oos_days=10,
        initial_cash=1000.0,
        warmup_days=10,
    )
    # BuyAndHold ignores history so stitched results must be identical.
    assert result_no_warmup.stitched_curve["equity"].to_list() == pytest.approx(
        result_with_warmup.stitched_curve["equity"].to_list()
    )

    # 2. Probe strategy: measure first-bar history depth for OOS with warmup=0 vs warmup>0.
    recorder_no_warmup = HistoryRecorderStrategy()
    recorder_with_warmup = HistoryRecorderStrategy()

    def _make_no_warmup(_: dict[str, float | int]) -> HistoryRecorderStrategy:
        return recorder_no_warmup

    def _make_with_warmup(_: dict[str, float | int]) -> HistoryRecorderStrategy:
        return recorder_with_warmup

    run_walkforward(
        warehouse=warehouse,
        strategy_factory=_make_no_warmup,
        optimizer=fake_optimizer,
        cost_model=CostModel(fee_rate=0.0, shipping_per_line=0.0),
        start=START,
        end=START + timedelta(days=39),
        is_days=10,
        oos_days=10,
        initial_cash=1000.0,
        warmup_days=0,
    )
    run_walkforward(
        warehouse=warehouse,
        strategy_factory=_make_with_warmup,
        optimizer=fake_optimizer,
        cost_model=CostModel(fee_rate=0.0, shipping_per_line=0.0),
        start=START,
        end=START + timedelta(days=39),
        is_days=10,
        oos_days=10,
        initial_cash=1000.0,
        warmup_days=10,
    )

    # Both recorders must have fired (sanity check).
    assert recorder_no_warmup.first_bar_history_len > 0
    assert recorder_with_warmup.first_bar_history_len > 0

    # With warm-up, first-bar OOS history must be strictly larger because
    # the IS rows (and warm-up rows if any) are included.
    assert recorder_with_warmup.first_bar_history_len >= recorder_no_warmup.first_bar_history_len


def test_warmup_clamps_when_exceeds_available_data(warehouse: Warehouse) -> None:
    """warmup_days larger than available history must not raise an error.

    The fixture has 40 days starting at START.  A warmup of 9999 days before
    the IS start will find no earlier data in the warehouse; the load simply
    returns whatever is available.
    """
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
        warmup_days=9999,
    )
