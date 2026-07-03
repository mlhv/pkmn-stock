from datetime import date

import polars as pl

from pkmn_quant.engine.portfolio import Asset, Position
from pkmn_quant.engine.strategy import Context
from pkmn_quant.strategies.buy_and_hold import BuyAndHold

D1 = date(2025, 6, 1)
SEALED_A = Asset(product_id=1, sub_type="Normal")
SEALED_B = Asset(product_id=2, sub_type="Normal")
SINGLE_C = Asset(product_id=3, sub_type="Holofoil")

PRODUCTS = pl.DataFrame(
    {
        "product_id": [1, 2, 3],
        "group_id": [10, 10, 10],
        "name": ["Box A", "ETB B", "Card C"],
        "rarity": [None, None, "Rare"],
        "kind": ["sealed", "sealed", "single"],
        "released_on": [D1, D1, D1],
    }
)


def make_ctx(
    cash: float,
    positions: dict[Asset, Position] | None = None,
    marks: dict[Asset, float] | None = None,
) -> Context:
    return Context(
        today=D1,
        history=pl.DataFrame(),
        products=PRODUCTS,
        positions=positions or {},
        cash=cash,
        marks=marks if marks is not None else {SEALED_A: 100.0, SEALED_B: 50.0, SINGLE_C: 10.0},
    )


def test_first_bar_equal_weights_sealed_universe() -> None:
    strat = BuyAndHold(kind="sealed")
    orders = strat.on_bar(make_ctx(cash=300.0))
    by_asset = {o.asset: o.quantity for o in orders}
    # $150 budget per sealed asset: 1x A ($100), 3x B ($50)
    assert by_asset == {SEALED_A: 1, SEALED_B: 3}


def test_never_orders_again_after_first_bar() -> None:
    strat = BuyAndHold(kind="sealed")
    strat.on_bar(make_ctx(cash=300.0))
    assert strat.on_bar(make_ctx(cash=300.0)) == []


def test_skips_assets_without_marks() -> None:
    strat = BuyAndHold(kind="sealed")
    orders = strat.on_bar(make_ctx(cash=300.0, marks={SEALED_A: 100.0}))  # B has no price yet
    assert [o.asset for o in orders] == [SEALED_A]


def test_name_includes_kind() -> None:
    assert BuyAndHold(kind="sealed").name == "buy-and-hold-sealed"


def test_reset_allows_reuse_across_runs() -> None:
    strat = BuyAndHold(kind="sealed")
    assert strat.on_bar(make_ctx(cash=300.0)) != []
    assert strat.on_bar(make_ctx(cash=300.0)) == []
    strat.reset()
    assert strat.on_bar(make_ctx(cash=300.0)) != []
