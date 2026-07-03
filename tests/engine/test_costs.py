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
