from datetime import date, timedelta

import polars as pl

from pkmn_quant.engine.portfolio import Asset, Position
from pkmn_quant.engine.strategy import Context
from pkmn_quant.strategies.momentum import CrossSectionalMomentum

TODAY = date(2025, 6, 10)
HOT = Asset(product_id=1, sub_type="Holofoil")
COLD = Asset(product_id=2, sub_type="Holofoil")

PRODUCTS = pl.DataFrame(
    {
        "product_id": [1, 2],
        "group_id": [10, 10],
        "name": ["Hot", "Cold"],
        "rarity": ["Rare", "Rare"],
        "kind": ["single", "single"],
        "released_on": [TODAY - timedelta(days=200)] * 2,
    }
)


def two_asset_history(lookback_days: int) -> pl.DataFrame:
    past = TODAY - timedelta(days=lookback_days)
    rows = []
    for pid, past_px, now_px in [(1, 10.0, 20.0), (2, 10.0, 9.0)]:
        for d, px in [(past, past_px), (TODAY, now_px)]:
            rows.append(
                {
                    "date": d,
                    "product_id": pid,
                    "sub_type": "Holofoil",
                    "low": 1.0,
                    "mid": 1.0,
                    "high": 1.0,
                    "market": px,
                }
            )
    return pl.DataFrame(rows)


def make_ctx(
    cash: float = 1000.0, positions: dict[Asset, Position] | None = None, today: date = TODAY
) -> Context:
    return Context(
        today=today,
        history=two_asset_history(30),
        products=PRODUCTS,
        positions=positions or {},
        cash=cash,
        marks={HOT: 20.0, COLD: 9.0},
    )


def test_buys_top_momentum_only() -> None:
    strat = CrossSectionalMomentum(lookback_days=30, top_n=1, rebalance_days=30)
    orders = strat.on_bar(make_ctx())
    assert [(o.asset, o.quantity) for o in orders] == [(HOT, 50)]  # floor(1000/1/20)


def test_no_action_between_rebalances() -> None:
    strat = CrossSectionalMomentum(lookback_days=30, top_n=1, rebalance_days=30)
    strat.on_bar(make_ctx())
    assert strat.on_bar(make_ctx(today=TODAY + timedelta(days=5))) == []


def test_rebalance_sells_dropped_names_first() -> None:
    strat = CrossSectionalMomentum(lookback_days=30, top_n=1, rebalance_days=1)
    positions = {COLD: Position(quantity=10, avg_cost=10.0)}
    orders = strat.on_bar(make_ctx(positions=positions))
    assert orders[0].asset == COLD and orders[0].quantity == -10  # sell first
    assert orders[1].asset == HOT and orders[1].quantity > 0


def test_reset_clears_rebalance_clock() -> None:
    strat = CrossSectionalMomentum(lookback_days=30, rebalance_days=30)
    strat.on_bar(make_ctx())
    strat.reset()
    assert strat.on_bar(make_ctx(today=TODAY + timedelta(days=1))) != []
