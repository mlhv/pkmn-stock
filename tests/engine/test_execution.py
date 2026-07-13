from datetime import date

import pytest

from pkmn_quant.engine.costs import CostModel
from pkmn_quant.engine.execution import ExecutionSimulator, Order
from pkmn_quant.engine.portfolio import Asset, Fill, Portfolio
from pkmn_quant.engine.quotes import Quote

A = Asset(product_id=1, sub_type="Normal")
DAY = date(2025, 6, 2)
CM = CostModel(fee_rate=0.10, shipping_per_line=1.0)
IMPACT_SIM = ExecutionSimulator(CostModel(impact_enabled=True))


def test_buy_fills_at_market_plus_shipping() -> None:
    sim = ExecutionSimulator(CM)
    p = Portfolio(cash=1000.0)
    fills = sim.execute([Order(asset=A, quantity=2)], prices={A: 10.0}, portfolio=p, day=DAY)
    assert len(fills) == 1
    f = fills[0]
    assert f.quantity == 2
    assert f.price == pytest.approx(10.0)
    assert f.fees == pytest.approx(1.0)  # one shipping charge per order line
    assert p.positions[A].quantity == 2
    assert p.cash == pytest.approx(1000.0 - 20.0 - 1.0)


def test_sell_fills_net_of_fees() -> None:
    sim = ExecutionSimulator(CM)
    p = Portfolio(cash=100.0)
    sim.execute([Order(asset=A, quantity=3)], prices={A: 10.0}, portfolio=p, day=DAY)
    fills = sim.execute([Order(asset=A, quantity=-2)], prices={A: 10.0}, portfolio=p, day=DAY)
    f = fills[0]
    assert f.quantity == -2
    assert f.price == pytest.approx(10.0)
    # fees = fee_rate on proceeds + one shipping: 2*10*0.10 + 1.0
    assert f.fees == pytest.approx(3.0)


def test_no_price_no_fill() -> None:
    sim = ExecutionSimulator(CM)
    p = Portfolio(cash=100.0)
    fills = sim.execute([Order(asset=A, quantity=1)], prices={}, portfolio=p, day=DAY)
    assert fills == []
    assert p.cash == 100.0


def test_buy_clipped_by_liquidity() -> None:
    cm = CostModel(liquidity_tiers=((100.0, 2),))
    sim = ExecutionSimulator(cm)
    p = Portfolio(cash=10_000.0)
    fills = sim.execute([Order(asset=A, quantity=50)], prices={A: 10.0}, portfolio=p, day=DAY)
    assert fills[0].quantity == 2


def test_buy_clipped_by_cash() -> None:
    sim = ExecutionSimulator(CM)
    p = Portfolio(cash=25.0)
    # each unit costs 10; shipping 1 on the line; affordable: 2 units (21) not 3 (31)
    fills = sim.execute([Order(asset=A, quantity=20)], prices={A: 10.0}, portfolio=p, day=DAY)
    assert fills[0].quantity == 2
    assert p.cash >= 0.0


def test_sell_clipped_to_held_never_short() -> None:
    sim = ExecutionSimulator(CM)
    p = Portfolio(cash=100.0)
    sim.execute([Order(asset=A, quantity=1)], prices={A: 10.0}, portfolio=p, day=DAY)
    fills = sim.execute([Order(asset=A, quantity=-5)], prices={A: 10.0}, portfolio=p, day=DAY)
    assert fills[0].quantity == -1
    assert A not in p.positions


def test_sell_with_no_position_no_fill() -> None:
    sim = ExecutionSimulator(CM)
    p = Portfolio(cash=0.0)
    fills = sim.execute([Order(asset=A, quantity=-3)], prices={A: 10.0}, portfolio=p, day=DAY)
    assert fills == []


def test_split_orders_share_daily_liquidity_cap() -> None:
    cm = CostModel(liquidity_tiers=((100.0, 8),))
    sim = ExecutionSimulator(cm)
    p = Portfolio(cash=10_000.0)
    fills = sim.execute(
        [Order(asset=A, quantity=8), Order(asset=A, quantity=8)],
        prices={A: 10.0},
        portfolio=p,
        day=DAY,
    )
    assert sum(f.quantity for f in fills) == 8  # cap is per asset-day, not per order


def test_zero_price_never_fills() -> None:
    sim = ExecutionSimulator(CM)
    p = Portfolio(cash=100.0)
    fills = sim.execute([Order(asset=A, quantity=1)], prices={A: 0.0}, portfolio=p, day=DAY)
    assert fills == []
    assert p.cash == 100.0


def test_buy_fill_carries_impact() -> None:
    pf = Portfolio(cash=1000.0)
    fills = IMPACT_SIM.execute(
        [Order(asset=A, quantity=4)],
        {A: 25.57},
        pf,
        date(2025, 6, 2),
        quotes={A: Quote(mid=29.64, low=21.50)},
    )
    # Q=8; impact = 4.07 * 4 * 4 / 16 = 4.07
    assert len(fills) == 1
    assert fills[0].price == pytest.approx(25.57)  # print unchanged
    assert fills[0].impact == pytest.approx(4.07)
    assert pf.cash == pytest.approx(1000.0 - 4 * 25.57 - 1.0 - 4.07)


def test_buy_without_quote_has_zero_impact() -> None:
    pf = Portfolio(cash=1000.0)
    fills = IMPACT_SIM.execute([Order(asset=A, quantity=4)], {A: 25.57}, pf, date(2025, 6, 2))
    assert fills[0].impact == 0.0


def test_buy_shrinks_qty_until_impact_affordable() -> None:
    # cash 100, market 12 (Q=8), mid 16: flat afford = floor(99/12) = 8, but
    # 8*12 + 1 + impact(8)=16 -> 113 > 100. Shrink: q=7 costs 84+1+12.25 =
    # 97.25 <= 100. Executor must fill 7, impact 12.25.
    pf = Portfolio(cash=100.0)
    fills = IMPACT_SIM.execute(
        [Order(asset=A, quantity=10)],
        {A: 12.0},
        pf,
        date(2025, 6, 2),
        quotes={A: Quote(mid=16.0, low=None)},
    )
    assert fills[0].quantity == 7
    assert fills[0].impact == pytest.approx(12.25)
    assert pf.cash == pytest.approx(100.0 - 97.25)


def test_split_orders_pay_same_impact_as_one() -> None:
    quotes = {A: Quote(mid=29.64, low=21.50)}
    pf1 = Portfolio(cash=10_000.0)
    IMPACT_SIM.execute(
        [Order(asset=A, quantity=8)],
        {A: 25.57},
        pf1,
        date(2025, 6, 2),
        quotes=quotes,
    )
    pf2 = Portfolio(cash=10_000.0)
    IMPACT_SIM.execute(
        [Order(asset=A, quantity=3), Order(asset=A, quantity=5)],
        {A: 25.57},
        pf2,
        date(2025, 6, 2),
        quotes=quotes,
    )
    # Two shipping lines differ; strip that out and compare pure impact+price.
    assert pf2.cash == pytest.approx(pf1.cash - 1.0)


def test_sell_fill_carries_impact() -> None:
    pf = Portfolio(cash=0.0)
    pf.apply(Fill(day=date(2025, 6, 1), asset=A, quantity=8, price=20.0, fees=0.0))
    fills = IMPACT_SIM.execute(
        [Order(asset=A, quantity=-8)],
        {A: 25.57},
        pf,
        date(2025, 6, 2),
        quotes={A: Quote(mid=None, low=21.50)},
    )
    assert fills[0].impact == pytest.approx(16.28)  # 4.07 * 8 * 8 / 16
