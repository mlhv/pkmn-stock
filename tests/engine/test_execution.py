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


def test_buy_below_the_lowest_listing_pays_the_floor_premium() -> None:
    """market (recent SALES) below low (cheapest current LISTING) must not
    let the executor buy at the sales price: there is no such offer.

    Hand-derivation: market 10.0 -> Q=8; low 25.0, mid 25.0, qty 2.
    base = max(10, 25) = 25; premium = (25-10)*2 = 30; walk spread
    mid-base = 0 -> impact 30.0. Print stays 10.0 (Fill.price is always the
    observable print), so total cash out is 2*10 + 1 shipping + 30 = 51 —
    i.e. 2 units at the $25 lowest listing plus shipping, exactly.
    """
    pf = Portfolio(cash=1000.0)
    fills = IMPACT_SIM.execute(
        [Order(asset=A, quantity=2)],
        {A: 10.0},
        pf,
        DAY,
        quotes={A: Quote(mid=25.0, low=25.0)},
    )
    assert len(fills) == 1
    assert fills[0].price == pytest.approx(10.0)  # print unchanged
    assert fills[0].impact == pytest.approx(30.0)
    assert pf.cash == pytest.approx(1000.0 - (2 * 25.0 + 1.0))


def test_buy_floor_binds_the_affordable_quantity() -> None:
    """Cash must buy what the LISTINGS cost, not what the sales print says.

    market 1.0 -> Q=20 (tier <5); low 10.0, mid 10.0; cash 26.0.
    At the sales print $1 the naive clip would be floor(25/1)=20 units; at
    the $10 floor only floor(25/10)=2 fit. Each unit costs 1 (print) + 9
    (premium) = 10, so 2 units cost 2*1 + 1 + 18 = 21 <= 26 and 3 would
    cost 3*1 + 1 + 27 = 31 > 26. Expect quantity 2, impact 18.0.
    """
    pf = Portfolio(cash=26.0)
    fills = IMPACT_SIM.execute(
        [Order(asset=A, quantity=20)],
        {A: 1.0},
        pf,
        DAY,
        quotes={A: Quote(mid=10.0, low=10.0)},
    )
    assert fills[0].quantity == 2
    assert fills[0].impact == pytest.approx(18.0)
    assert pf.cash == pytest.approx(26.0 - 21.0)


def test_buy_depth_cap_is_tiered_on_the_floored_base_not_the_print() -> None:
    """I3 regression: the liquidity tier must describe the product bought.

    `max_daily_qty` is a claim about how much depth a product of a given
    price has, so selecting it from the sales print grants an inverted row
    the depth of a cheaper product than the one actually being transacted.
    Measured on the research window, 346,378 rows (15.8% of the 2.19M
    inverted ones) fall in a different tier under `low` than under `market`.

    Hand-derivation: market 4.0 (tier <5 -> Q=20), low 60.0 and mid 60.0 so
    the base is 60.0 (tier <200 -> Q=3) and the walk term is zero. Order 10
    units against ample cash. Off the print the executor would grant 10 (cap
    20, cash-unbound); off the base it grants 3.
    Impact = premium only = (60 - 4) * 3 = 168.0. Cash out = 3*4 + 1 + 168 =
    181.0, i.e. three units at the $60 lowest listing plus shipping.
    """
    pf = Portfolio(cash=10_000.0)
    fills = IMPACT_SIM.execute(
        [Order(asset=A, quantity=10)],
        {A: 4.0},
        pf,
        DAY,
        quotes={A: Quote(mid=60.0, low=60.0)},
    )
    assert fills[0].quantity == 3
    assert fills[0].impact == pytest.approx(168.0)
    assert pf.cash == pytest.approx(10_000.0 - 181.0)


def test_sell_depth_cap_stays_on_the_print() -> None:
    """The sell side has no floor (tcgcsv publishes no bid series), so its
    tier must keep coming off the print — the asymmetry is deliberate.

    market 4.0 -> Q=20. Hold 12 units, order all 12 out; a base-tiered cap
    would clip to 3. Expect 12.
    """
    pf = Portfolio(cash=0.0)
    pf.apply(Fill(day=DAY, asset=A, quantity=12, price=4.0, fees=0.0, impact=0.0))
    fills = IMPACT_SIM.execute(
        [Order(asset=A, quantity=-12)],
        {A: 4.0},
        pf,
        DAY,
        quotes={A: Quote(mid=60.0, low=3.0)},
    )
    assert fills[0].quantity == -12


def test_flat_cost_sizing_ignores_the_floor_entirely() -> None:
    """With impact OFF the floor must not exist at all — not in the cost, and
    not in the affordability clip either.

    The premium is routed through `impact` by design, so when the impact model
    is disabled there is no premium to reserve cash for and the true constraint
    is the pre-floor one: qty*market + shipping <= cash. Clipping off `base`
    here would bind BELOW that and silently cut real quantities.

    Same fixture as test_buy_floor_binds_the_affordable_quantity (market 1.0 ->
    Q=20, low 10.0, cash 26.0), which fills 2 with impact on. Flat-cost:
    affordable = floor((26-1)/1) = 25, clipped by cap 20 -> 20 units, cost
    20*1 + 1 = 21, cash left 5.0. Identical to the pre-floor engine.
    """
    sim = ExecutionSimulator(CostModel())  # impact_enabled defaults False
    pf = Portfolio(cash=26.0)
    fills = sim.execute(
        [Order(asset=A, quantity=20)],
        {A: 1.0},
        pf,
        DAY,
        quotes={A: Quote(mid=10.0, low=10.0)},
    )
    assert fills[0].quantity == 20
    assert fills[0].impact == 0.0
    assert pf.cash == pytest.approx(5.0)
    # And the floor genuinely is inert, not merely coincidental: the same run
    # without any quote at all must be identical.
    pf_noquote = Portfolio(cash=26.0)
    no_quote = sim.execute([Order(asset=A, quantity=20)], {A: 1.0}, pf_noquote, DAY)
    assert no_quote == fills
    assert pf_noquote.cash == pytest.approx(pf.cash)


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
