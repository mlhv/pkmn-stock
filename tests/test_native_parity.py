"""Differential tests: NativeBacktest (C++) vs Backtest (Python reference).

Every assertion is EXACT (==) — bit-for-bit parity is the acceptance bar
(spec 2026-07-14). A tolerance here would hide real divergence.
"""

from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA
from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.backtest import Backtest, Result
from pkmn_quant.engine.costs import CostModel
from pkmn_quant.engine.native import NativeBacktest, NativeStrategySpec
from pkmn_quant.strategies.buy_and_hold import BuyAndHold

START = date(2025, 1, 1)

# (product_id, sub_type) -> base price. product 4 has two sub_types (an
# insertion-order tie on product_id); product 6 sits below min_price.
BASES: dict[tuple[int, str], float] = {
    (1, "Normal"): 80.0,
    (2, "Normal"): 40.0,
    (3, "Normal"): 25.0,
    (4, "Normal"): 12.0,
    (4, "Foil"): 18.0,
    (5, "Normal"): 6.0,
    (6, "Normal"): 1.5,
}

PRODUCTS = pl.DataFrame(
    {
        "product_id": [1, 2, 3, 4, 5, 6],
        "group_id": [1, 1, 1, 1, 1, 1],
        "name": ["Box A", "Box B", "Card C", "Card D", "Card E", "Penny F"],
        "rarity": [None, None, "Rare", "Rare", "Holo", "Common"],
        "kind": ["sealed", "sealed", "single", "single", "single", "single"],
        "released_on": [
            date(2024, 11, 1),
            date(2024, 6, 1),
            date(2024, 11, 1),
            date(2024, 11, 1),
            date(2024, 11, 1),
            date(2024, 11, 1),
        ],
    }
)


def _path(i: int) -> float:
    """Deterministic ramp-crash-recover cycle: guarantees dips, drawdowns,
    momentum reversals, and take-profit recoveries within 25 days."""
    i = i % 25
    if i < 10:
        return 1.0 + 0.05 * i  # ramp to 1.45
    if i < 15:
        return 0.8 - 0.05 * (i - 10)  # crash to 0.60
    return 0.62 + 0.03 * (i - 15)  # recovery


def seed_rich(root: Path, n_days: int = 40) -> None:
    w = Warehouse(Paths(root=root))
    for i in range(n_days):
        day = START + timedelta(days=i)
        if i % 9 == 4:  # market-wide gap day
            continue
        rows = []
        for (pid, st), base in BASES.items():
            if (pid * 3 + i) % 11 == 0:  # per-asset missing prints
                continue
            market = round(base * _path(i + pid), 2)
            rows.append(
                {
                    "date": day,
                    "product_id": pid,
                    "sub_type": st,
                    "low": round(market * 0.9, 2),
                    "mid": round(market * 1.15, 2),
                    "high": round(market * 3.0, 2),
                    "market": market,
                }
            )
        w.write_prices(day, pl.DataFrame(rows, schema=PRICE_SCHEMA))
    w.write_products(PRODUCTS)


def assert_results_equal(py: Result, cpp: Result) -> None:
    assert py.equity_curve["date"].to_list() == cpp.equity_curve["date"].to_list()
    assert py.equity_curve["equity"].to_list() == cpp.equity_curve["equity"].to_list()
    assert len(py.fills) == len(cpp.fills)
    for a, b in zip(py.fills, cpp.fills, strict=True):
        assert (a.day, a.asset, a.quantity) == (b.day, b.asset, b.quantity)
        assert a.price == b.price
        assert a.fees == b.fees
        assert a.impact == b.impact
    assert py.summary == cpp.summary
    assert py.strategy_name == cpp.strategy_name


@pytest.mark.parametrize("impact", [False, True])
def test_buy_and_hold_parity(tmp_path: Path, impact: bool) -> None:
    seed_rich(tmp_path)
    wh = Warehouse(Paths(root=tmp_path))
    cm = CostModel(impact_enabled=impact)
    end = START + timedelta(days=39)
    py = Backtest(
        warehouse=wh,
        strategy=BuyAndHold(kind="sealed"),
        cost_model=cm,
        start=START,
        end=end,
        initial_cash=1000.0,
    ).run()
    cpp = NativeBacktest(
        warehouse=wh,
        strategy=NativeStrategySpec("buy-and-hold", {}, kind="sealed"),
        cost_model=cm,
        start=START,
        end=end,
        initial_cash=1000.0,
    ).run()
    assert len(py.fills) > 0  # the test must not pass vacuously
    assert_results_equal(py, cpp)


def test_buy_and_hold_parity_single_universe(tmp_path: Path) -> None:
    """Exercises the marks insertion-order tie: product 4 Normal vs Foil."""
    seed_rich(tmp_path)
    wh = Warehouse(Paths(root=tmp_path))
    cm = CostModel()
    end = START + timedelta(days=39)
    py = Backtest(
        warehouse=wh,
        strategy=BuyAndHold(kind="single"),
        cost_model=cm,
        start=START,
        end=end,
        initial_cash=1000.0,
    ).run()
    cpp = NativeBacktest(
        warehouse=wh,
        strategy=NativeStrategySpec("buy-and-hold", {}, kind="single"),
        cost_model=cm,
        start=START,
        end=end,
        initial_cash=1000.0,
    ).run()
    assert len(py.fills) > 0
    assert_results_equal(py, cpp)


def test_warmup_days_parity(tmp_path: Path) -> None:
    seed_rich(tmp_path)
    wh = Warehouse(Paths(root=tmp_path))
    cm = CostModel(impact_enabled=True)
    start = START + timedelta(days=15)
    end = START + timedelta(days=39)
    py = Backtest(
        warehouse=wh,
        strategy=BuyAndHold(kind="sealed"),
        cost_model=cm,
        start=start,
        end=end,
        initial_cash=1000.0,
        warmup_days=10,
    ).run()
    cpp = NativeBacktest(
        warehouse=wh,
        strategy=NativeStrategySpec("buy-and-hold", {}, kind="sealed"),
        cost_model=cm,
        start=start,
        end=end,
        initial_cash=1000.0,
        warmup_days=10,
    ).run()
    assert_results_equal(py, cpp)


def test_unknown_strategy_raises_value_error(tmp_path: Path) -> None:
    seed_rich(tmp_path, n_days=3)
    wh = Warehouse(Paths(root=tmp_path))
    with pytest.raises(ValueError):
        NativeBacktest(
            warehouse=wh,
            strategy=NativeStrategySpec("nope", {}),
            cost_model=CostModel(),
            start=START,
            end=START + timedelta(days=2),
            initial_cash=100.0,
        ).run()
