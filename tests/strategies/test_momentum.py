from datetime import date, timedelta

import polars as pl
import pytest

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
    """With a held position whose opened_on is TODAY, a call 5 days later is
    not yet due (5 < 30) and must return [].  The derived-clock path replaces
    the old _last_rebalance sentinel.
    """
    strat = CrossSectionalMomentum(lookback_days=30, top_n=1, rebalance_days=30)
    # Simulate holding HOT after a fill on TODAY; opened_on=TODAY.
    positions = {HOT: Position(quantity=50, avg_cost=20.0, opened_on=TODAY)}
    ctx = make_ctx(cash=0.0, positions=positions, today=TODAY + timedelta(days=5))
    assert strat.on_bar(ctx) == []


def test_rebalance_sells_dropped_names_first() -> None:
    strat = CrossSectionalMomentum(lookback_days=30, top_n=1, rebalance_days=1)
    # opened_on=TODAY-1: (today - opened_on).days = 1 >= rebalance_days=1, so due.
    positions = {COLD: Position(quantity=10, avg_cost=10.0, opened_on=TODAY - timedelta(days=1))}
    orders = strat.on_bar(make_ctx(positions=positions))
    assert orders[0].asset == COLD and orders[0].quantity == -10  # sell first
    assert orders[1].asset == HOT and orders[1].quantity > 0


def test_strategy_is_stateless_across_instances() -> None:
    """The strategy carries no mutable clock: two fresh instances called with
    the same Context produce identical results (replaced _last_rebalance test).
    """
    strat1 = CrossSectionalMomentum(lookback_days=30, rebalance_days=30)
    strat2 = CrossSectionalMomentum(lookback_days=30, rebalance_days=30)
    ctx = make_ctx()
    orders1 = strat1.on_bar(ctx)
    orders2 = strat2.on_bar(ctx)
    assert orders1 == orders2


def test_overweight_name_not_trimmed() -> None:
    """Names that stay in target are never trimmed (long-only, entry-only
    weighting): winners drift above equal weight over time.
    """
    strat = CrossSectionalMomentum(lookback_days=30, top_n=1, rebalance_days=1)
    # HOT has top momentum (100% return). Hold 100 shares at $20 = $2000 held,
    # equity = $2000 (no cash), per_name = $2000, qty = floor(0) = 0. No buy.
    # opened_on=TODAY-1: (today - opened_on).days = 1 >= rebalance_days=1, so due.
    positions = {HOT: Position(quantity=100, avg_cost=10.0, opened_on=TODAY - timedelta(days=1))}
    orders = strat.on_bar(make_ctx(cash=0.0, positions=positions))
    # HOT is in target; held_value = 2000 = per_name, so no buy needed.
    # No sells (HOT in target). Result: no orders.
    assert orders == []


def test_min_price_excludes_cheap_assets() -> None:
    """Assets below min_price are excluded from target, whether via fresh
    strategy (empty target -> no orders on empty portfolio) or held position
    (dropped from target -> sell).
    """
    strat = CrossSectionalMomentum(lookback_days=30, top_n=2, rebalance_days=1, min_price=25.0)
    # Both HOT (20.0) and COLD (9.0) are below min_price=25.0, so target is empty.
    # Fresh portfolio: no positions to sell, no target to buy -> empty orders.
    orders = strat.on_bar(make_ctx())
    assert orders == []

    # Now with a held non-target position: COLD is held but excluded by min_price.
    strat2 = CrossSectionalMomentum(lookback_days=30, top_n=2, rebalance_days=1, min_price=25.0)
    # opened_on=TODAY-1: (today - opened_on).days = 1 >= rebalance_days=1, so due.
    positions = {COLD: Position(quantity=10, avg_cost=10.0, opened_on=TODAY - timedelta(days=1))}
    orders = strat2.on_bar(make_ctx(positions=positions))
    # COLD not in target (min_price), so it must be sold.
    assert len(orders) == 1
    assert orders[0].asset == COLD
    assert orders[0].quantity == -10


def test_empty_target_returns_only_sells() -> None:
    """When lookback is longer than available history, the momentum frame is
    empty, target is empty, and only sells are emitted (for any held positions).
    """
    strat = CrossSectionalMomentum(lookback_days=90, top_n=1, rebalance_days=1)
    # lookback_days=90 but history only spans 30 days (TODAY-30 to TODAY).
    # window_start = TODAY - 90, which is before history start -> past frame empty.
    # opened_on=TODAY-1: (today - opened_on).days = 1 >= rebalance_days=1, so due.
    positions = {HOT: Position(quantity=50, avg_cost=10.0, opened_on=TODAY - timedelta(days=1))}
    orders = strat.on_bar(make_ctx(positions=positions))
    # Only sell: HOT is not in empty target.
    assert len(orders) == 1
    assert orders[0].asset == HOT
    assert orders[0].quantity == -50


def _mk_ctx(
    today: date,
    positions: dict[Asset, Position],
    cash: float,
    marks: dict[Asset, float],
) -> Context:
    """Minimal Context: rebalance-clock tests need no price history."""
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


def test_rebalance_clock_derived_from_newest_opened_on() -> None:
    s = CrossSectionalMomentum(rebalance_days=30)
    a, b = Asset(1, "Normal"), Asset(2, "Normal")
    positions = {
        a: Position(quantity=1, avg_cost=10.0, opened_on=date(2026, 1, 1)),
        b: Position(quantity=1, avg_cost=10.0, opened_on=date(2026, 1, 20)),
    }
    # 29 days after the NEWEST buy: not due, no orders at all.
    ctx = _mk_ctx(date(2026, 2, 18), positions, 100.0, {a: 10.0, b: 10.0})
    assert s.on_bar(ctx) == []
    # 30 days after the newest buy: due — with no candidates in empty history,
    # everything held is sold (dropped out of the empty target).
    positions2 = {
        a: Position(quantity=1, avg_cost=10.0, opened_on=date(2026, 1, 1)),
        b: Position(quantity=1, avg_cost=10.0, opened_on=date(2026, 1, 20)),
    }
    ctx2 = _mk_ctx(date(2026, 2, 19), positions2, 100.0, {a: 10.0, b: 10.0})
    orders = s.on_bar(ctx2)
    assert sorted(o.quantity for o in orders) == [-1, -1]


def test_flat_portfolio_is_always_due() -> None:
    s = CrossSectionalMomentum(rebalance_days=30)
    ctx = _mk_ctx(date(2026, 2, 18), {}, 100.0, {})
    # Flat + no candidates -> no orders, but it must not raise and must not
    # depend on any internal clock.
    assert s.on_bar(ctx) == []
    assert not hasattr(s, "_last_rebalance")


def test_momentum_none_opened_on_raises() -> None:
    s = CrossSectionalMomentum(rebalance_days=30)
    a = Asset(1, "Normal")
    ctx = _mk_ctx(date(2026, 2, 18), {a: Position(quantity=1, avg_cost=10.0)}, 100.0, {a: 10.0})
    with pytest.raises(ValueError, match="opened_on"):
        s.on_bar(ctx)
