from datetime import date, timedelta

import polars as pl

from pkmn_quant.engine.portfolio import Asset, Position
from pkmn_quant.engine.strategy import Context
from pkmn_quant.strategies.dip_buyer import DipBuyer

TODAY = date(2025, 6, 10)
CARD = Asset(product_id=1, sub_type="Holofoil")

PRODUCTS = pl.DataFrame(
    {
        "product_id": [1],
        "group_id": [10],
        "name": ["Chase Card"],
        "rarity": ["Special Illustration Rare"],
        "kind": ["single"],
        "released_on": [TODAY - timedelta(days=200)],
    }
)


def history_for(prices: list[tuple[date, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "date": [d for d, _ in prices],
            "product_id": [1] * len(prices),
            "sub_type": ["Holofoil"] * len(prices),
            "low": [1.0] * len(prices),
            "mid": [1.0] * len(prices),
            "high": [1.0] * len(prices),
            "market": [p for _, p in prices],
        }
    )


def make_ctx(
    history: pl.DataFrame,
    marks: dict[Asset, float],
    cash: float = 1000.0,
    positions: dict[Asset, Position] | None = None,
    products: pl.DataFrame | None = None,
    today: date = TODAY,
) -> Context:
    return Context(
        today=today,
        history=history,
        products=products if products is not None else PRODUCTS,
        positions=positions or {},
        cash=cash,
        marks=marks,
    )


def test_buys_sharp_dip() -> None:
    hist = history_for([(TODAY - timedelta(days=7), 100.0), (TODAY, 60.0)])  # -40%
    strat = DipBuyer(dip_window_days=7, dip_threshold=0.30, budget_frac=0.5)
    orders = strat.on_bar(make_ctx(hist, {CARD: 60.0}))
    assert [(o.asset, o.quantity) for o in orders] == [(CARD, 8)]  # floor(500/60)


def test_ignores_small_dip() -> None:
    hist = history_for([(TODAY - timedelta(days=7), 100.0), (TODAY, 85.0)])  # -15%
    strat = DipBuyer(dip_threshold=0.30)
    assert strat.on_bar(make_ctx(hist, {CARD: 85.0})) == []


def test_exits_after_hold_days() -> None:
    hist = history_for([(TODAY - timedelta(days=7), 100.0), (TODAY, 60.0)])
    strat = DipBuyer(dip_threshold=0.30, hold_days=10, budget_frac=0.5)
    strat.on_bar(make_ctx(hist, {CARD: 60.0}))  # records entry intent today
    later = TODAY + timedelta(days=11)
    positions = {CARD: Position(quantity=8, avg_cost=60.0)}
    orders = strat.on_bar(make_ctx(hist, {CARD: 61.0}, positions=positions, today=later))
    assert [(o.asset, o.quantity) for o in orders] == [(CARD, -8)]


def test_exits_on_take_profit_before_hold_days() -> None:
    hist = history_for([(TODAY - timedelta(days=7), 100.0), (TODAY, 60.0)])
    strat = DipBuyer(dip_threshold=0.30, hold_days=30, take_profit=1.2, budget_frac=0.5)
    strat.on_bar(make_ctx(hist, {CARD: 60.0}))
    positions = {CARD: Position(quantity=8, avg_cost=60.0)}
    soon = TODAY + timedelta(days=2)
    orders = strat.on_bar(make_ctx(hist, {CARD: 75.0}, positions=positions, today=soon))
    assert [(o.asset, o.quantity) for o in orders] == [(CARD, -8)]  # 75 >= 60*1.2


def test_reset_clears_entry_dates() -> None:
    hist = history_for([(TODAY - timedelta(days=7), 100.0), (TODAY, 60.0)])
    strat = DipBuyer(dip_threshold=0.30, budget_frac=0.5)
    strat.on_bar(make_ctx(hist, {CARD: 60.0}))
    assert strat._entries
    strat.reset()
    assert not strat._entries


def test_skips_unaffordable_candidate_for_affordable_one() -> None:
    # Two candidates: deeper dip unaffordable (mark > budget), shallower dip
    # affordable. With max_positions=1, affordable one should be bought.
    DEEP = Asset(product_id=2, sub_type="Holofoil")
    SHALLOW = Asset(product_id=3, sub_type="Holofoil")
    products = pl.concat(
        [
            PRODUCTS,
            pl.DataFrame(
                {
                    "product_id": [2, 3],
                    "group_id": [11, 12],
                    "name": ["Deep Card", "Shallow Card"],
                    "rarity": ["Rare", "Rare"],
                    "kind": ["single", "single"],
                    "released_on": [TODAY - timedelta(days=200), TODAY - timedelta(days=200)],
                }
            ),
        ]
    )

    # DEEP: 60% dip but mark=600 (unaffordable with budget=100)
    hist_deep = history_for(
        [
            (TODAY - timedelta(days=7), 1500.0),
            (TODAY, 600.0),
        ]
    )
    # Adjust to product_id=2
    hist_deep = hist_deep.with_columns(pl.col("product_id").replace(1, 2))

    # SHALLOW: 40% dip but mark=50 (affordable)
    hist_shallow = history_for(
        [
            (TODAY - timedelta(days=7), 80.0),
            (TODAY, 50.0),
        ]
    )
    # Adjust to product_id=3
    hist_shallow = hist_shallow.with_columns(pl.col("product_id").replace(1, 3))
    hist = pl.concat([hist_deep, hist_shallow])

    strat = DipBuyer(
        dip_window_days=7,
        dip_threshold=0.25,
        max_positions=1,
        budget_frac=0.1,  # budget = 100
    )
    orders = strat.on_bar(
        make_ctx(
            hist,
            {DEEP: 600.0, SHALLOW: 50.0},
            cash=1000.0,
            products=products,
        )
    )

    # Should buy only SHALLOW (affordable), not DEEP (floor(100/600)=0).
    assert len(orders) == 1
    assert orders[0].asset == SHALLOW
    assert orders[0].quantity == 2  # floor(100/50)
