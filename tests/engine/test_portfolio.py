from datetime import date

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pkmn_quant.engine.portfolio import Asset, Fill, Portfolio

A = Asset(product_id=1, sub_type="Normal")
B = Asset(product_id=2, sub_type="Holofoil")
DAY = date(2025, 6, 2)


def fill(asset: Asset, qty: int, price: float, fees: float = 0.0) -> Fill:
    return Fill(day=DAY, asset=asset, quantity=qty, price=price, fees=fees)


def test_buy_updates_cash_position_and_avg_cost() -> None:
    p = Portfolio(cash=1000.0)
    p.apply(fill(A, 2, 10.0, fees=2.0))
    p.apply(fill(A, 1, 16.0, fees=1.0))
    assert p.cash == pytest.approx(1000.0 - 2 * 10.0 - 2.0 - 16.0 - 1.0)
    pos = p.positions[A]
    assert pos.quantity == 3
    assert pos.avg_cost == pytest.approx(12.0)  # (20 + 16) / 3, fees excluded
    assert p.realized_pnl == pytest.approx(-3.0)  # buy fees hit realized_pnl


def test_sell_realizes_pnl_against_avg_cost() -> None:
    p = Portfolio(cash=0.0)
    p.apply(fill(A, 3, 12.0))
    p.apply(fill(A, -1, 20.0, fees=3.0))
    assert p.realized_pnl == pytest.approx(20.0 - 12.0 - 3.0)
    assert p.positions[A].quantity == 2
    assert p.positions[A].avg_cost == pytest.approx(12.0)  # unchanged by sells


def test_position_closed_when_fully_sold() -> None:
    p = Portfolio(cash=0.0)
    p.apply(fill(A, 1, 5.0))
    p.apply(fill(A, -1, 6.0))
    assert A not in p.positions


def test_oversell_rejected() -> None:
    p = Portfolio(cash=100.0)
    p.apply(fill(A, 1, 5.0))
    with pytest.raises(ValueError, match="cannot sell"):
        p.apply(fill(A, -2, 6.0))


def test_equity_marks_positions_to_market() -> None:
    p = Portfolio(cash=100.0)
    p.apply(fill(A, 2, 10.0))
    p.apply(fill(B, 1, 50.0))
    equity = p.equity({A: 15.0, B: 40.0})
    assert equity == pytest.approx((100.0 - 20.0 - 50.0) + 2 * 15.0 + 40.0)


def test_ledger_records_every_fill() -> None:
    p = Portfolio(cash=100.0)
    p.apply(fill(A, 1, 5.0))
    p.apply(fill(A, -1, 6.0))
    assert len(p.ledger) == 2


@given(
    st.lists(
        st.tuples(
            st.integers(min_value=1, max_value=5),  # buy qty
            st.floats(min_value=0.5, max_value=100.0),  # buy price
            st.floats(min_value=0.5, max_value=100.0),  # later sell price
            st.floats(min_value=0.0, max_value=5.0),  # fees each side
        ),
        min_size=1,
        max_size=20,
    )
)
def test_accounting_identity_holds(trades: list[tuple[int, float, float, float]]) -> None:
    """Full round-trips: buy then sell everything, so final cash must equal
    initial + realized P&L exactly, and no positions remain."""
    initial = 10_000.0
    p = Portfolio(cash=initial)
    for i, (qty, buy_px, sell_px, fee) in enumerate(trades):
        asset = Asset(product_id=i, sub_type="Normal")
        p.apply(Fill(day=DAY, asset=asset, quantity=qty, price=buy_px, fees=fee))
        p.apply(Fill(day=DAY, asset=asset, quantity=-qty, price=sell_px, fees=fee))
    assert p.cash == pytest.approx(initial + p.realized_pnl)
    assert p.positions == {}


@given(
    st.lists(
        st.tuples(
            st.integers(min_value=1, max_value=5),
            st.floats(min_value=0.5, max_value=100.0),
        ),
        min_size=1,
        max_size=10,
    )
)
def test_avg_cost_is_quantity_weighted_mean(buys: list[tuple[int, float]]) -> None:
    p = Portfolio(cash=100_000.0)
    asset = Asset(product_id=99, sub_type="Normal")
    total_qty = 0
    total_cost = 0.0
    for qty, price in buys:
        p.apply(Fill(day=DAY, asset=asset, quantity=qty, price=price, fees=0.0))
        total_qty += qty
        total_cost += qty * price
    assert p.positions[asset].avg_cost == pytest.approx(total_cost / total_qty)
