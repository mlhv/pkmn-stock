from datetime import date, timedelta

import polars as pl
import pytest

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
    # Buy order emitted on TODAY, filled at T+1 (TODAY+1); opened_on=TODAY+1.
    # hold_days=10: exit fires when (later - opened_on).days >= 10.
    # With later=TODAY+11 and opened_on=TODAY+1, days held = 10 → exit.
    hist = history_for([(TODAY - timedelta(days=7), 100.0), (TODAY, 60.0)])
    strat = DipBuyer(dip_threshold=0.30, hold_days=10, budget_frac=0.5)
    later = TODAY + timedelta(days=11)
    positions = {CARD: Position(quantity=8, avg_cost=60.0, opened_on=TODAY + timedelta(days=1))}
    orders = strat.on_bar(make_ctx(hist, {CARD: 61.0}, positions=positions, today=later))
    assert [(o.asset, o.quantity) for o in orders] == [(CARD, -8)]


def test_exits_on_take_profit_before_hold_days() -> None:
    hist = history_for([(TODAY - timedelta(days=7), 100.0), (TODAY, 60.0)])
    strat = DipBuyer(dip_threshold=0.30, hold_days=30, take_profit=1.2, budget_frac=0.5)
    # opened_on=TODAY+1 (T+1 fill); soon=TODAY+2 means 1 day held < hold_days=30.
    positions = {CARD: Position(quantity=8, avg_cost=60.0, opened_on=TODAY + timedelta(days=1))}
    soon = TODAY + timedelta(days=2)
    orders = strat.on_bar(make_ctx(hist, {CARD: 75.0}, positions=positions, today=soon))
    assert [(o.asset, o.quantity) for o in orders] == [(CARD, -8)]  # 75 >= 60*1.2


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


def _mk_ctx(
    today: date,
    positions: dict[Asset, Position],
    cash: float,
    marks: dict[Asset, float],
) -> Context:
    """Minimal Context for exit-rule tests: entries need history, exits don't."""
    empty_prices = pl.DataFrame(
        schema={"date": pl.Date, "product_id": pl.Int64, "sub_type": pl.Utf8, "market": pl.Float64}
    )
    products = pl.DataFrame(
        {
            "product_id": [1],
            "group_id": [1],
            "name": ["X"],
            "rarity": [None],
            "kind": ["single"],
            "released_on": [date(2024, 1, 1)],
        }
    )
    return Context(
        today=today,
        history=empty_prices,
        products=products,
        positions=positions,
        cash=cash,
        marks=marks,
    )


def test_time_exit_uses_opened_on() -> None:
    s = DipBuyer(hold_days=30, take_profit=10.0)  # take_profit unreachable
    a = Asset(1, "Normal")
    held_29 = _mk_ctx(
        date(2026, 2, 3),
        {a: Position(quantity=2, avg_cost=10.0, opened_on=date(2026, 1, 5))},
        100.0,
        {a: 10.0},
    )
    assert s.on_bar(held_29) == []  # 29 days held: no exit
    held_30 = _mk_ctx(
        date(2026, 2, 4),
        {a: Position(quantity=2, avg_cost=10.0, opened_on=date(2026, 1, 5))},
        100.0,
        {a: 10.0},
    )
    [order] = s.on_bar(held_30)
    assert order.quantity == -2  # 30 days held: full exit


def test_none_opened_on_raises() -> None:
    s = DipBuyer()
    a = Asset(1, "Normal")
    ctx = _mk_ctx(date(2026, 2, 4), {a: Position(quantity=1, avg_cost=10.0)}, 100.0, {a: 10.0})
    with pytest.raises(ValueError, match="opened_on"):
        s.on_bar(ctx)


def test_dip_buyer_is_stateless_across_bars() -> None:
    """No _entries: the same instance gives identical answers for identical
    Contexts — the property that makes single-bar live invocation safe."""
    s = DipBuyer(hold_days=30, take_profit=10.0)
    a = Asset(1, "Normal")
    ctx = _mk_ctx(
        date(2026, 2, 4),
        {a: Position(quantity=2, avg_cost=10.0, opened_on=date(2026, 1, 5))},
        100.0,
        {a: 10.0},
    )
    first = s.on_bar(ctx)
    ctx2 = _mk_ctx(
        date(2026, 2, 4),
        {a: Position(quantity=2, avg_cost=10.0, opened_on=date(2026, 1, 5))},
        100.0,
        {a: 10.0},
    )
    assert s.on_bar(ctx2) == first
    assert not hasattr(s, "_entries")


def test_time_exit_fires_without_a_mark() -> None:
    """A held position whose asset no longer prints a price (no mark) must
    still exit on the hold-day clock — take-profit needs a mark, age doesn't."""
    s = DipBuyer(hold_days=30, take_profit=10.0)
    a = Asset(1, "Normal")
    ctx = _mk_ctx(
        date(2026, 2, 4),
        {a: Position(quantity=2, avg_cost=10.0, opened_on=date(2026, 1, 5))},
        100.0,
        {},  # no marks at all
    )
    [order] = s.on_bar(ctx)
    assert order.quantity == -2
