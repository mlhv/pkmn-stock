from datetime import date, timedelta

import polars as pl
import pytest

from pkmn_quant.engine.costs import CostModel
from pkmn_quant.engine.portfolio import Asset, Position
from pkmn_quant.engine.strategy import Context
from pkmn_quant.strategies.cost_aware_reversion import CostAwareReversion


def _history(rows: list[tuple[date, int, str, float]]) -> pl.DataFrame:
    # Explicit schema so an EMPTY history is still date/int/str/float typed —
    # an untyped empty frame would break the strategy's date filter.
    return pl.DataFrame(
        {
            "date": [r[0] for r in rows],
            "product_id": [r[1] for r in rows],
            "sub_type": [r[2] for r in rows],
            "market": [r[3] for r in rows],
        },
        schema={"date": pl.Date, "product_id": pl.Int64, "sub_type": pl.Utf8, "market": pl.Float64},
    )


def _products(ids_kinds: list[tuple[int, str]]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "product_id": [i for i, _ in ids_kinds],
            "group_id": [1] * len(ids_kinds),
            "name": [f"P{i}" for i, _ in ids_kinds],
            "rarity": [None] * len(ids_kinds),
            "kind": [k for _, k in ids_kinds],
            "released_on": [date(2024, 1, 1)] * len(ids_kinds),
        }
    )


def _ctx(today, history, products, positions, cash, marks) -> Context:
    return Context(
        today=today,
        history=history,
        products=products,
        positions=positions,
        cash=cash,
        marks=marks,
    )


def test_entry_fires_when_rebound_clears_hurdle() -> None:
    """high 100, mark 70: dip 0.30 >= 0.25; rebound 100/70-1 = 0.4286;
    hurdle = 0.1275 + 2*1/70 = 0.1561; 0.1561 + 0.05 = 0.2061 <= 0.4286 -> BUY.
    Sized: budget = 1000 * 0.10 = 100 -> floor(100/70) = 1 unit."""
    s = CostAwareReversion(dip_window_days=30, dip_threshold=0.25, min_edge=0.05, budget_frac=0.10)
    today = date(2026, 3, 1)
    hist = _history([(today - timedelta(days=20), 1, "Normal", 100.0), (today, 1, "Normal", 70.0)])
    a = Asset(1, "Normal")
    [order] = s.on_bar(_ctx(today, hist, _products([(1, "single")]), {}, 1000.0, {a: 70.0}))
    assert (order.asset, order.quantity) == (a, 1)


def test_cheap_card_rejected_by_shipping_hurdle() -> None:
    """high 6, mark 4: dip 0.333, rebound 0.50 — but hurdle = 0.1275 + 2*1/4
    = 0.6275; + 0.05 = 0.6775 > 0.50 -> shipping kills the trade, no order."""
    s = CostAwareReversion(dip_window_days=30, dip_threshold=0.25, min_edge=0.05, min_price=3.0)
    today = date(2026, 3, 1)
    hist = _history([(today - timedelta(days=20), 1, "Normal", 6.0), (today, 1, "Normal", 4.0)])
    a = Asset(1, "Normal")
    assert s.on_bar(_ctx(today, hist, _products([(1, "single")]), {}, 1000.0, {a: 4.0})) == []


def test_sealed_products_are_candidates_too() -> None:
    """Universe is both kinds: an identical dip on a sealed product enters."""
    s = CostAwareReversion(dip_window_days=30, dip_threshold=0.25, min_edge=0.05)
    today = date(2026, 3, 1)
    hist = _history([(today - timedelta(days=20), 1, "Normal", 100.0), (today, 1, "Normal", 70.0)])
    a = Asset(1, "Normal")
    [order] = s.on_bar(_ctx(today, hist, _products([(1, "sealed")]), {}, 1000.0, {a: 70.0}))
    assert order.quantity > 0


def test_time_exit_at_max_hold_days() -> None:
    s = CostAwareReversion(max_hold_days=120, take_profit=10.0)
    a = Asset(1, "Normal")
    pos = {a: Position(quantity=2, avg_cost=50.0, opened_on=date(2026, 1, 1))}
    empty = _history([])
    ctx = _ctx(date(2026, 5, 1), empty, _products([(1, "single")]), pos, 0.0, {a: 50.0})
    [order] = s.on_bar(ctx)  # 2026-05-01 - 2026-01-01 = 120 days
    assert order.quantity == -2


def test_take_profit_exit() -> None:
    s = CostAwareReversion(max_hold_days=9999, take_profit=1.25)
    a = Asset(1, "Normal")
    pos = {a: Position(quantity=1, avg_cost=40.0, opened_on=date(2026, 4, 30))}
    empty = _history([])
    ctx = _ctx(date(2026, 5, 1), empty, _products([(1, "single")]), pos, 0.0, {a: 50.0})
    [order] = s.on_bar(ctx)  # 50 >= 40 * 1.25
    assert order.quantity == -1


def test_none_opened_on_raises() -> None:
    s = CostAwareReversion()
    a = Asset(1, "Normal")
    pos = {a: Position(quantity=1, avg_cost=40.0)}
    ctx = _ctx(date(2026, 5, 1), _history([]), _products([(1, "single")]), pos, 0.0, {a: 50.0})
    with pytest.raises(ValueError, match="opened_on"):
        s.on_bar(ctx)


def test_entry_at_exact_dip_threshold_enters() -> None:
    """Pin the strict-< dip gate: dip == dip_threshold passes (not rejected).

    Arithmetic (exact in IEEE 754 — both values are powers of two):
      high=100, mark=75, dip_threshold=0.25
      dip = 1 - 75/100 = 0.25  (= 0x1.0p-2 exactly)
      Gate: dip < 0.25 → False  → candidate survives

    Hurdle check (comfortably cleared):
      rebound = 100/75 - 1 ≈ 0.3333
      hurdle  = 0.1275 + 2*1/75 ≈ 0.1542
      hurdle + min_edge(0.05) ≈ 0.2042  ≤ 0.3333  → passes

    Sizing: cash=1000, budget_frac=0.10 → budget=100, floor(100/75)=1 unit.
    """
    s = CostAwareReversion(dip_window_days=30, dip_threshold=0.25, min_edge=0.05, budget_frac=0.10)
    today = date(2026, 3, 1)
    hist = _history([(today - timedelta(days=20), 1, "Normal", 100.0), (today, 1, "Normal", 75.0)])
    a = Asset(1, "Normal")
    [order] = s.on_bar(_ctx(today, hist, _products([(1, "single")]), {}, 1000.0, {a: 75.0}))
    assert (order.asset, order.quantity) == (a, 1)


def test_entry_at_exact_hurdle_boundary_enters() -> None:
    """Pin the strict-< hurdle gate: rebound == hurdle + min_edge passes (not rejected).

    Arithmetic (exact in IEEE 754 — all values are multiples of 0.25):
      CostModel(fee_rate=0.25, shipping_per_line=0.0): hurdle = 0.25 + 2*0/mark = 0.25
      min_edge = 0.25
      hurdle + min_edge = 0.50  (= 0x1.0p-1 exactly)

      high=96, mark=64: rebound = 96/64 - 1 = 0.5  (= 0x1.0p-1 exactly)
      Gate: rebound < hurdle + min_edge  →  0.5 < 0.5  → False  → candidate survives

    Dip check (comfortably cleared):
      dip = 1 - 64/96 = 1/3 ≈ 0.3333  ≥  dip_threshold=0.25  → passes

    Sizing: cash=1000, budget_frac=0.10 → budget=100, floor(100/64)=1 unit.
    """
    cost_model = CostModel(fee_rate=0.25, shipping_per_line=0.0)
    s = CostAwareReversion(
        dip_window_days=30,
        dip_threshold=0.25,
        min_edge=0.25,
        budget_frac=0.10,
        costs=cost_model,
    )
    today = date(2026, 3, 1)
    hist = _history([(today - timedelta(days=20), 1, "Normal", 96.0), (today, 1, "Normal", 64.0)])
    a = Asset(1, "Normal")
    [order] = s.on_bar(_ctx(today, hist, _products([(1, "single")]), {}, 1000.0, {a: 64.0}))
    assert (order.asset, order.quantity) == (a, 1)


def test_candidate_dropped_when_budget_buys_zero_units() -> None:
    """Pin the floor-budget gate: dip and hurdle both cleared but qty==0 → no order.

    Arithmetic:
      high=100, mark=70: dip = 0.30 ≥ 0.25 ✓
      rebound = 100/70 - 1 ≈ 0.4286
      hurdle  = 0.1275 + 2*1/70 ≈ 0.1561; + min_edge(0.05) ≈ 0.2061 ≤ 0.4286 ✓

    Sizing: cash=100, budget_frac=0.10 → budget=10, floor(10/70)=0 → dropped.
    """
    s = CostAwareReversion(dip_window_days=30, dip_threshold=0.25, min_edge=0.05, budget_frac=0.10)
    today = date(2026, 3, 1)
    hist = _history([(today - timedelta(days=20), 1, "Normal", 100.0), (today, 1, "Normal", 70.0)])
    a = Asset(1, "Normal")
    assert s.on_bar(_ctx(today, hist, _products([(1, "single")]), {}, 100.0, {a: 70.0})) == []
