"""plan_paper_fills is pure: recommendations + cash in, ledger event dicts out."""

from datetime import date

import pytest

from pkmn_quant.engine.costs import CostModel
from pkmn_quant.live.paper import plan_paper_fills
from pkmn_quant.live.signals import Recommendation

DAY = date(2026, 7, 11)


def _buy(qty: int, mark: float) -> Recommendation:
    return Recommendation(
        action="BUY",
        product_id=1,
        sub_type="Normal",
        name="X",
        quantity=qty,
        market_price=mark,
        notional=qty * mark,
    )


def _sell(qty: int, mark: float) -> Recommendation:
    return Recommendation(
        action="SELL",
        product_id=2,
        sub_type="Normal",
        name="Y",
        quantity=qty,
        market_price=mark,
        notional=qty * mark,
        avg_cost=50.0,
    )


def test_sell_proceeds_fund_later_buys() -> None:
    """cash 0: the BUY is only affordable because the SELL lands first.
    Sell 2 @ 100: proceeds = 2*100*(1-0.1275) - 1 = 173.50.
    Buy 1 @ 150: affordable = floor((173.50 - 1) / 150) = 1 -> fills."""
    batch = plan_paper_fills([_sell(2, 100.0), _buy(1, 150.0)], 0.0, DAY, CostModel())
    assert [e["kind"] for e in batch] == ["sell", "buy"]
    assert batch[1]["qty"] == 1


def test_buy_dropped_without_prior_sell() -> None:
    """Same BUY, no SELL, cash 0: affordable = floor(-1/150) clamps to 0 -> dropped."""
    assert plan_paper_fills([_buy(1, 150.0)], 0.0, DAY, CostModel()) == []


def test_liquidity_cap_clips_buy() -> None:
    """Mark 100 sits in the (200.0, 3) tier -> cap 3, even with ample cash."""
    [event] = plan_paper_fills([_buy(10, 100.0)], 10_000.0, DAY, CostModel())
    assert event["qty"] == 3


def test_liquidity_cap_clips_sell() -> None:
    [event] = plan_paper_fills([_sell(10, 100.0)], 0.0, DAY, CostModel())
    assert event["qty"] == 3


def test_unaffordable_buy_clips_to_zero_and_drops() -> None:
    """cash 50 < 100 + shipping: floor((50-1)/100) = 0 -> no event at all.
    This is the case daily.json used to miscount as a recorded buy."""
    assert plan_paper_fills([_buy(1, 100.0)], 50.0, DAY, CostModel()) == []


def test_empty_recommendations_empty_batch() -> None:
    assert plan_paper_fills([], 1_000.0, DAY, CostModel()) == []


def test_event_shape_and_fee_arithmetic() -> None:
    """Buy fees = shipping only; sell fees = qty*mark*fee_rate + shipping.
    Sell 2 @ 100 -> fees = 2*100*0.1275 + 1 = 26.50. Dates = the run day."""
    batch = plan_paper_fills([_sell(2, 100.0), _buy(1, 10.0)], 0.0, DAY, CostModel())
    sell, buy = batch
    assert sell == {
        "date": "2026-07-11",
        "kind": "sell",
        "product_id": 2,
        "sub_type": "Normal",
        "qty": 2,
        "price": 100.0,
        "fees": 26.5,
        "impact": 0.0,
    }
    assert buy["fees"] == 1.0
    assert buy["date"] == "2026-07-11"


def test_buy_fill_records_impact_and_respects_cash() -> None:
    costs = CostModel(impact_enabled=True)
    rec = Recommendation(
        action="BUY",
        product_id=1,
        sub_type="Normal",
        name="Box",
        quantity=10,
        market_price=12.0,
        notional=120.0,
        mid=16.0,
        low=None,
    )
    batch = plan_paper_fills([rec], cash=100.0, day=date(2026, 7, 13), costs=costs)
    # Same arithmetic as the executor golden: fill 7, impact 12.25.
    assert len(batch) == 1
    assert batch[0]["qty"] == 7
    assert batch[0]["impact"] == pytest.approx(12.25)


def test_buy_below_the_lowest_listing_pays_the_floor_premium() -> None:
    """The paper planner mirrors _fill_buy's executable-price floor.

    market 10.0 (recent SALES) under low 25.0 (cheapest current LISTING),
    mid 25.0, qty 2, cap 8 (tier <50): base = max(10, 25) = 25, premium =
    (25-10)*2 = 30, walk spread mid-base = 0 -> impact 30.0. The recorded
    price stays the print, same as a backtest fill.
    """
    costs = CostModel(impact_enabled=True)
    rec = Recommendation(
        action="BUY",
        product_id=1,
        sub_type="Normal",
        name="Box",
        quantity=2,
        market_price=10.0,
        notional=20.0,
        mid=25.0,
        low=25.0,
    )
    [event] = plan_paper_fills([rec], cash=1_000.0, day=date(2026, 7, 13), costs=costs)
    assert event["qty"] == 2
    assert event["price"] == pytest.approx(10.0)
    assert event["impact"] == pytest.approx(30.0)


def test_buy_floor_binds_the_affordable_quantity() -> None:
    """Cash must buy what the listings cost, not what the sales print says.

    market 1.0 -> cap 20 (tier <5); low 10.0, mid 10.0; cash 26.0.
    Off the print the clip would be floor(25/1) = 20 units; off the $10 floor
    only floor(25/10) = 2 fit, costing 2*1 + 1 + (10-1)*2 = 21 <= 26.
    """
    costs = CostModel(impact_enabled=True)
    rec = Recommendation(
        action="BUY",
        product_id=1,
        sub_type="Normal",
        name="Card",
        quantity=20,
        market_price=1.0,
        notional=20.0,
        mid=10.0,
        low=10.0,
    )
    [event] = plan_paper_fills([rec], cash=26.0, day=date(2026, 7, 13), costs=costs)
    assert event["qty"] == 2
    assert event["impact"] == pytest.approx(18.0)


def test_sell_fill_records_impact() -> None:
    costs = CostModel(impact_enabled=True)
    rec = Recommendation(
        action="SELL",
        product_id=1,
        sub_type="Normal",
        name="Box",
        quantity=8,
        market_price=25.57,
        notional=204.56,
        mid=None,
        low=21.50,
    )
    batch = plan_paper_fills([rec], cash=0.0, day=date(2026, 7, 13), costs=costs)
    assert batch[0]["impact"] == pytest.approx(16.28)


def test_no_quote_zero_impact_matches_old_numbers() -> None:
    costs = CostModel(impact_enabled=True)
    rec = Recommendation(
        action="BUY",
        product_id=1,
        sub_type="Normal",
        name="Box",
        quantity=8,
        market_price=12.0,
        notional=96.0,
    )
    batch = plan_paper_fills([rec], cash=100.0, day=date(2026, 7, 13), costs=costs)
    assert batch[0]["qty"] == 8
    assert batch[0]["impact"] == pytest.approx(0.0)
