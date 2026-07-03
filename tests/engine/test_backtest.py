from datetime import date
from pathlib import Path

import polars as pl
import pytest

from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA
from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.backtest import Backtest
from pkmn_quant.engine.costs import CostModel
from pkmn_quant.engine.execution import Order
from pkmn_quant.engine.portfolio import Asset
from pkmn_quant.engine.strategy import Context, Strategy
from tests.helpers import price_row

D1, D2, D3 = date(2025, 6, 1), date(2025, 6, 2), date(2025, 6, 3)
A = Asset(product_id=1, sub_type="Normal")

row = price_row


@pytest.fixture
def warehouse(tmp_path: Path) -> Warehouse:
    w = Warehouse(Paths(root=tmp_path))
    w.write_prices(D1, pl.DataFrame([row(D1, 1, 10.0)], schema=PRICE_SCHEMA))
    w.write_prices(D2, pl.DataFrame([row(D2, 1, 10.0)], schema=PRICE_SCHEMA))
    w.write_prices(D3, pl.DataFrame([row(D3, 1, 20.0)], schema=PRICE_SCHEMA))
    w.write_products(
        pl.DataFrame(
            {
                "product_id": [1],
                "group_id": [1],
                "name": ["X"],
                "rarity": [None],
                "kind": ["sealed"],
                "released_on": [D1],
            }
        )
    )
    return w


class BuyOnceDayOne(Strategy):
    name = "buy-once"

    def on_bar(self, ctx: Context) -> list[Order]:
        if not ctx.positions:
            return [Order(asset=A, quantity=1)]
        return []


def test_t_plus_1_fill_and_final_equity(warehouse: Warehouse) -> None:
    # Zero-cost model isolates the accounting: order on D1 fills at D2's price.
    result = Backtest(
        warehouse=warehouse,
        strategy=BuyOnceDayOne(),
        cost_model=CostModel(fee_rate=0.0, shipping_per_line=0.0),
        start=D1,
        end=D3,
        initial_cash=100.0,
    ).run()
    assert len(result.fills) == 1
    assert result.fills[0].day == D2
    assert result.fills[0].price == pytest.approx(10.0)
    curve = result.equity_curve
    assert curve["equity"].to_list() == pytest.approx([100.0, 100.0, 110.0])
    assert result.summary["total_return"] == pytest.approx(0.10)


class LookaheadProbe(Strategy):
    name = "probe"

    def __init__(self) -> None:
        self.violations = 0

    def on_bar(self, ctx: Context) -> list[Order]:
        if ctx.history.height and ctx.history["date"].max() > ctx.today:
            self.violations += 1
        return []


def test_no_lookahead(warehouse: Warehouse) -> None:
    probe = LookaheadProbe()
    Backtest(
        warehouse=warehouse,
        strategy=probe,
        cost_model=CostModel(),
        start=D1,
        end=D3,
        initial_cash=100.0,
    ).run()
    assert probe.violations == 0


def test_result_serializes_cost_model(warehouse: Warehouse) -> None:
    result = Backtest(
        warehouse=warehouse,
        strategy=BuyOnceDayOne(),
        cost_model=CostModel(),
        start=D1,
        end=D3,
        initial_cash=100.0,
    ).run()
    assert result.cost_model["fee_rate"] == pytest.approx(0.1275)
