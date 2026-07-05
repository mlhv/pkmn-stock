from datetime import date, timedelta

import polars as pl

from pkmn_quant.engine.portfolio import Asset, Position
from pkmn_quant.engine.strategy import Context
from pkmn_quant.strategies.sealed_accumulation import SealedAccumulation

TODAY = date(2025, 6, 1)
BOX = Asset(product_id=1, sub_type="Normal")
FRESH = Asset(product_id=2, sub_type="Normal")  # too young to buy

PRODUCTS = pl.DataFrame(
    {
        "product_id": [1, 2],
        "group_id": [10, 11],
        "name": ["Old Box", "Fresh Box"],
        "rarity": [None, None],
        "kind": ["sealed", "sealed"],
        "released_on": [TODAY - timedelta(days=120), TODAY - timedelta(days=10)],
    }
)


def history_for(asset: Asset, prices: list[tuple[date, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "date": [d for d, _ in prices],
            "product_id": [asset.product_id] * len(prices),
            "sub_type": [asset.sub_type] * len(prices),
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
) -> Context:
    return Context(
        today=TODAY,
        history=history,
        products=products if products is not None else PRODUCTS,
        positions=positions or {},
        cash=cash,
        marks=marks,
    )


def test_buys_aged_drawdown_sealed() -> None:
    # BOX peaked at 100, now 70: 30% drawdown, age 120d -> qualifies.
    hist = history_for(BOX, [(TODAY - timedelta(days=100), 100.0), (TODAY, 70.0)])
    strat = SealedAccumulation(min_drawdown=0.25, budget_frac=0.5)
    orders = strat.on_bar(make_ctx(hist, {BOX: 70.0}))
    assert len(orders) == 1
    assert orders[0].asset == BOX
    assert orders[0].quantity == 7  # floor(1000*0.5 / 70)


def test_ignores_shallow_drawdown() -> None:
    hist = history_for(BOX, [(TODAY - timedelta(days=100), 100.0), (TODAY, 90.0)])
    strat = SealedAccumulation(min_drawdown=0.25)
    assert strat.on_bar(make_ctx(hist, {BOX: 90.0})) == []


def test_ignores_too_young_product() -> None:
    hist = history_for(FRESH, [(TODAY - timedelta(days=5), 100.0), (TODAY, 60.0)])
    strat = SealedAccumulation(min_drawdown=0.25, min_age_days=60)
    assert strat.on_bar(make_ctx(hist, {FRESH: 60.0})) == []


def test_takes_profit_on_held_position() -> None:
    from pkmn_quant.engine.execution import Order

    hist = history_for(BOX, [(TODAY - timedelta(days=100), 100.0), (TODAY, 120.0)])
    strat = SealedAccumulation(take_profit=1.5)
    positions = {BOX: Position(quantity=3, avg_cost=70.0)}
    orders = strat.on_bar(make_ctx(hist, {BOX: 120.0}, positions=positions))
    assert orders == [Order(asset=BOX, quantity=-3)]  # 120 >= 70*1.5


def test_does_not_rebuy_held_asset() -> None:
    hist = history_for(BOX, [(TODAY - timedelta(days=100), 100.0), (TODAY, 70.0)])
    strat = SealedAccumulation(min_drawdown=0.25)
    positions = {BOX: Position(quantity=1, avg_cost=70.0)}
    assert strat.on_bar(make_ctx(hist, {BOX: 70.0}, positions=positions)) == []


def test_max_positions_respected() -> None:
    strat = SealedAccumulation(max_positions=0)
    hist = history_for(BOX, [(TODAY - timedelta(days=100), 100.0), (TODAY, 70.0)])
    assert strat.on_bar(make_ctx(hist, {BOX: 70.0})) == []


def test_reset_clears_nothing_but_is_safe() -> None:
    strat = SealedAccumulation()
    strat.reset()  # stateless besides params; must not raise


def test_ignores_too_old_product() -> None:
    # Product released 400 days ago, max_age_days=365 -> too old, not included.
    TOO_OLD = Asset(product_id=3, sub_type="Normal")
    products = pl.concat(
        [
            PRODUCTS,
            pl.DataFrame(
                {
                    "product_id": [3],
                    "group_id": [12],
                    "name": ["Ancient Box"],
                    "rarity": [None],
                    "kind": ["sealed"],
                    "released_on": [TODAY - timedelta(days=400)],
                }
            ),
        ]
    )
    hist = history_for(TOO_OLD, [(TODAY - timedelta(days=300), 100.0), (TODAY, 50.0)])
    strat = SealedAccumulation(min_drawdown=0.25, max_age_days=365)
    assert strat.on_bar(make_ctx(hist, {TOO_OLD: 50.0}, products=products)) == []


def test_replaces_sold_position_same_bar() -> None:
    # Hold BOX at mark >= take_profit; another qualifying aged product available.
    # Assert: sell BOX, then buy NEW.
    NEW = Asset(product_id=3, sub_type="Normal")
    products = pl.concat(
        [
            PRODUCTS,
            pl.DataFrame(
                {
                    "product_id": [3],
                    "group_id": [12],
                    "name": ["New Old Box"],
                    "rarity": [None],
                    "kind": ["sealed"],
                    "released_on": [TODAY - timedelta(days=120)],
                }
            ),
        ]
    )
    hist_box = history_for(BOX, [(TODAY - timedelta(days=100), 100.0), (TODAY, 120.0)])
    hist_new = history_for(NEW, [(TODAY - timedelta(days=80), 80.0), (TODAY, 50.0)])
    hist = pl.concat([hist_box, hist_new])

    positions = {BOX: Position(quantity=5, avg_cost=70.0)}
    strat = SealedAccumulation(max_positions=1, take_profit=1.5, min_drawdown=0.25)
    orders = strat.on_bar(
        make_ctx(
            hist,
            {BOX: 120.0, NEW: 50.0},
            positions=positions,
            products=products,
        )
    )

    assert len(orders) == 2
    assert orders[0].asset == BOX
    assert orders[0].quantity == -5  # full sell
    assert orders[1].asset == NEW
    assert orders[1].quantity > 0  # buy the new candidate


def test_skips_unaffordable_candidate_for_affordable_one() -> None:
    # Two candidates: deeper-drawdown one is unaffordable (mark > budget),
    # shallower one is affordable. With max_positions=1, affordable one should
    # be bought and no zero-qty order emitted.
    DEEP = Asset(product_id=3, sub_type="Normal")
    SHALLOW = Asset(product_id=4, sub_type="Normal")
    products = pl.concat(
        [
            PRODUCTS,
            pl.DataFrame(
                {
                    "product_id": [3, 4],
                    "group_id": [12, 13],
                    "name": ["Deep Box", "Shallow Box"],
                    "rarity": [None, None],
                    "kind": ["sealed", "sealed"],
                    "released_on": [TODAY - timedelta(days=120), TODAY - timedelta(days=120)],
                }
            ),
        ]
    )

    # DEEP: 60% drawdown but mark=600 (unaffordable with budget=100)
    hist_deep = history_for(DEEP, [(TODAY - timedelta(days=100), 1500.0), (TODAY, 600.0)])
    # SHALLOW: 40% drawdown but mark=50 (affordable)
    hist_shallow = history_for(SHALLOW, [(TODAY - timedelta(days=100), 80.0), (TODAY, 50.0)])
    hist = pl.concat([hist_deep, hist_shallow])

    strat = SealedAccumulation(
        max_positions=1,
        min_drawdown=0.25,
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
