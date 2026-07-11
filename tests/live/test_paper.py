"""plan_paper_fills is pure: recommendations + cash in, ledger event dicts out."""

from datetime import date

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
    }
    assert buy["fees"] == 1.0
    assert buy["date"] == "2026-07-11"
