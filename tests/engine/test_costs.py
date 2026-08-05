import itertools

import pytest

from pkmn_quant.engine.costs import CostModel


def test_default_round_trip_loses_about_15_percent() -> None:
    cm = CostModel()
    buy = cm.buy_price(market=100.0)
    proceeds = cm.sell_proceeds(market=100.0)
    assert buy == pytest.approx(101.0)  # market + $1 shipping
    assert proceeds == pytest.approx(100.0 * (1 - 0.1275) - 1.0)
    loss = (buy - proceeds) / buy
    assert 0.13 < loss < 0.16  # the honest hurdle


def test_liquidity_caps_tiered_by_price() -> None:
    cm = CostModel()
    assert cm.max_daily_qty(market=2.0) == 20
    assert cm.max_daily_qty(market=30.0) == 8
    assert cm.max_daily_qty(market=150.0) == 3
    assert cm.max_daily_qty(market=1500.0) == 1


def test_custom_parameters() -> None:
    cm = CostModel(fee_rate=0.10, shipping_per_line=0.0)
    assert cm.buy_price(50.0) == pytest.approx(50.0)
    assert cm.sell_proceeds(50.0) == pytest.approx(45.0)


def test_serializable_for_result_reports() -> None:
    cm = CostModel()
    d = cm.as_dict()
    assert d["fee_rate"] == pytest.approx(0.1275)
    assert d["liquidity_tiers"] == [(5.0, 20), (50.0, 8), (200.0, 3)]


def test_total_costs_charge_shipping_once_per_line() -> None:
    cm = CostModel(fee_rate=0.10, shipping_per_line=1.0)
    assert cm.total_buy_cost(10.0, 5) == pytest.approx(51.0)  # not 55
    assert cm.total_sell_proceeds(10.0, 5) == pytest.approx(44.0)  # 50*0.9 - 1


def test_liquidity_cap_exact_threshold_falls_to_next_tier() -> None:
    cm = CostModel()
    assert cm.max_daily_qty(market=5.0) == 8  # strict <: $5.00 is mid-tier
    assert cm.max_daily_qty(market=50.0) == 3


IMPACT_MODEL = CostModel(impact_enabled=True)


def test_impact_disabled_by_default_is_zero() -> None:
    m = CostModel()
    assert m.buy_impact(25.57, 29.64, 21.50, 8) == 0.0
    assert m.sell_impact(25.57, 21.50, 8) == 0.0


def test_buy_impact_full_cap_is_half_spread_per_unit() -> None:
    # market 25.57, mid 29.64 -> spread 4.07; Q=8 (tier: <50 -> 8).
    # q=8, used=0: 4.07 * 8 * 8 / 16 = 16.28 total = half spread per unit.
    assert IMPACT_MODEL.buy_impact(25.57, 29.64, 21.50, 8) == pytest.approx(16.28)


def test_sell_impact_walks_toward_low() -> None:
    # spread 25.57-21.50 = 4.07; same arithmetic as the buy side.
    assert IMPACT_MODEL.sell_impact(25.57, 21.50, 8) == pytest.approx(16.28)


def test_impact_monotone_in_qty() -> None:
    impacts = [IMPACT_MODEL.buy_impact(25.57, 29.64, 21.50, q) for q in range(9)]
    assert impacts[0] == 0.0
    assert all(a < b for a, b in itertools.pairwise(impacts))


def test_impact_split_invariance() -> None:
    # Splitting one order into two must cost exactly the same total impact:
    # the second order walks the book from where the first stopped.
    whole = IMPACT_MODEL.buy_impact(25.57, 29.64, 21.50, 8)
    split = IMPACT_MODEL.buy_impact(25.57, 29.64, 21.50, 3) + IMPACT_MODEL.buy_impact(
        25.57, 29.64, 21.50, 5, used=3
    )
    assert split == pytest.approx(whole)


def test_impact_crossed_or_missing_quote_is_zero() -> None:
    assert IMPACT_MODEL.buy_impact(25.57, 25.57, 21.50, 8) == 0.0  # flat quote
    assert IMPACT_MODEL.buy_impact(25.57, 20.00, 21.50, 8) == 0.0  # crossed (mid < market)
    assert IMPACT_MODEL.buy_impact(25.57, None, 21.50, 8) == 0.0  # missing mid
    assert IMPACT_MODEL.sell_impact(25.57, 30.00, 8) == 0.0  # crossed (low > market)
    assert IMPACT_MODEL.sell_impact(25.57, None, 8) == 0.0  # missing low


def test_impact_q1_tier_single_unit_pays_half_spread() -> None:
    # $250 product -> fallback tier Q=1. One unit pays half the spread:
    # (300-250) * 1 * 1 / 2 = 25. Deliberate: one-sale-a-day markets do not
    # hand you the ideal market price (spec, "Formula" section).
    assert IMPACT_MODEL.buy_impact(250.0, 300.0, 225.0, 1) == pytest.approx(25.0)


def test_buy_impact_charges_the_premium_to_reach_the_lowest_listing() -> None:
    """market is sales-derived, low is the cheapest current listing. You
    cannot lift an offer that does not exist, so a buy pays at least `low`
    and the shortfall is a cost.

    Hand-derivation: market 10, low 25, mid 25, qty 2, used 0, q_cap 8.
    base = max(10, 25) = 25. Floor premium = (25 - 10) * 2 = 30.
    Walk spread = mid - base = 0, so the walk term is 0. Total 30.
    """
    cm = CostModel(impact_enabled=True)
    assert cm.buy_impact(10.0, 25.0, 25.0, qty=2) == 30.0


def test_buy_impact_walks_from_the_floor_not_from_market() -> None:
    """With a floor in play the walk starts at the floor, not at market.

    Hand-derivation: market 10, low 20, mid 40, qty 2, used 0, q_cap 8.
    base = 20. Premium = (20 - 10) * 2 = 20.
    Walk spread = 40 - 20 = 20 -> 20 * 2 * (0 + 2) / (2 * 8) = 5.0.
    Total 25.0.
    """
    cm = CostModel(impact_enabled=True)
    assert cm.buy_impact(10.0, 40.0, 20.0, qty=2) == 25.0


def test_buy_impact_unchanged_when_market_is_at_or_above_low() -> None:
    """The ordinary case is untouched: no premium, walk from market to mid."""
    cm = CostModel(impact_enabled=True)
    assert cm.buy_impact(30.0, 40.0, 20.0, qty=2) == cm.buy_impact(30.0, 40.0, None, qty=2)


def test_buy_impact_with_no_low_falls_back_to_market() -> None:
    """A missing low is not an excuse to invent one."""
    cm = CostModel(impact_enabled=True)
    assert cm.buy_impact(10.0, 40.0, None, qty=1) > 0.0


def test_buy_impact_is_zero_when_disabled_even_with_a_floor() -> None:
    cm = CostModel(impact_enabled=False)
    assert cm.buy_impact(10.0, 40.0, 1000.0, qty=5) == 0.0


def test_as_dict_includes_impact_flag() -> None:
    assert CostModel().as_dict()["impact_enabled"] is False
    assert IMPACT_MODEL.as_dict()["impact_enabled"] is True
