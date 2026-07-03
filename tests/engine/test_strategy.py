from datetime import date

import polars as pl

from pkmn_quant.engine.execution import Order
from pkmn_quant.engine.portfolio import Asset, Portfolio
from pkmn_quant.engine.strategy import Context, Strategy


class Noop(Strategy):
    def on_bar(self, ctx: Context) -> list[Order]:
        return []


def test_strategy_is_abstract() -> None:
    import pytest

    with pytest.raises(TypeError):
        Strategy()  # type: ignore[abstract]


def test_context_exposes_read_state() -> None:
    products = pl.DataFrame({"product_id": [1], "kind": ["sealed"]})
    history = pl.DataFrame({"date": [date(2025, 6, 1)], "product_id": [1]})
    p = Portfolio(cash=500.0)
    ctx = Context(
        today=date(2025, 6, 1),
        history=history,
        products=products,
        positions=p.positions,
        cash=p.cash,
        marks={Asset(1, "Normal"): 10.0},
    )
    assert ctx.cash == 500.0
    assert ctx.today == date(2025, 6, 1)
    assert Noop().on_bar(ctx) == []
