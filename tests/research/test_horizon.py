"""Horizon-matched evaluation: each round trip against the sealed index over
that trip's own entry and exit dates."""

from datetime import date

import polars as pl
import pytest

from pkmn_quant.research.horizon import horizon_report

FILL_SCHEMA = {
    "day": pl.Date,
    "product_id": pl.Int64,
    "sub_type": pl.Utf8,
    "quantity": pl.Int64,
    "price": pl.Float64,
    "fees": pl.Float64,
    "impact": pl.Float64,
}


def _fills(rows: list[dict[str, object]]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=FILL_SCHEMA)


def _index(points: list[tuple[date, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        [{"date": d, "equity": e} for d, e in points],
        schema={"date": pl.Date, "equity": pl.Float64},
    )


def test_edge_is_strategy_return_minus_index_return_over_the_same_dates() -> None:
    """The whole point: compare against holding sealed for the SAME window.

    Hand-derivation: buy 1 @ 100 (fees 1, impact 0) on 2025-01-01, sell 1 @
    120 (fees 1, impact 0) on 2025-03-02. Cost basis 101, proceeds 119,
    strategy return 119/101 - 1 = +17.8218%. Index 1000 -> 1100 = +10%.
    Edge = +7.8218 pts.
    """
    fills = _fills(
        [
            {
                "day": date(2025, 1, 1),
                "product_id": 1,
                "sub_type": "Normal",
                "quantity": 1,
                "price": 100.0,
                "fees": 1.0,
                "impact": 0.0,
            },
            {
                "day": date(2025, 3, 2),
                "product_id": 1,
                "sub_type": "Normal",
                "quantity": -1,
                "price": 120.0,
                "fees": 1.0,
                "impact": 0.0,
            },
        ]
    )
    index = _index([(date(2025, 1, 1), 1000.0), (date(2025, 3, 2), 1100.0)])
    rep = horizon_report(fills, index, final_marks={}, last_day=date(2025, 3, 2))
    assert len(rep.trips) == 1
    trip = rep.trips[0]
    assert trip.holding_days == 60
    assert trip.strategy_return == pytest.approx(0.178218, abs=1e-6)
    assert trip.index_return == pytest.approx(0.10, abs=1e-12)
    assert trip.edge == pytest.approx(0.078218, abs=1e-6)
    assert trip.still_open is False
    assert rep.holding_buckets["1-3m"] == 1


def test_fifo_splits_one_sell_across_two_buy_lots() -> None:
    """Two buys then one sell of both units must produce two round trips,
    each priced against its own lot."""
    fills = _fills(
        [
            {
                "day": date(2025, 1, 1),
                "product_id": 1,
                "sub_type": "Normal",
                "quantity": 1,
                "price": 100.0,
                "fees": 0.0,
                "impact": 0.0,
            },
            {
                "day": date(2025, 2, 1),
                "product_id": 1,
                "sub_type": "Normal",
                "quantity": 1,
                "price": 200.0,
                "fees": 0.0,
                "impact": 0.0,
            },
            {
                "day": date(2025, 3, 1),
                "product_id": 1,
                "sub_type": "Normal",
                "quantity": -2,
                "price": 300.0,
                "fees": 0.0,
                "impact": 0.0,
            },
        ]
    )
    index = _index(
        [(date(2025, 1, 1), 1000.0), (date(2025, 2, 1), 1000.0), (date(2025, 3, 1), 1000.0)]
    )
    rep = horizon_report(fills, index, final_marks={}, last_day=date(2025, 3, 1))
    assert [t.entry for t in rep.trips] == [date(2025, 1, 1), date(2025, 2, 1)]
    assert rep.trips[0].strategy_return == pytest.approx(2.0)  # 100 -> 300
    assert rep.trips[1].strategy_return == pytest.approx(0.5)  # 200 -> 300


def test_open_position_is_closed_at_the_final_mark_and_flagged() -> None:
    """Open trades must be counted but never silently mixed with completed
    ones."""
    fills = _fills(
        [
            {
                "day": date(2025, 1, 1),
                "product_id": 1,
                "sub_type": "Normal",
                "quantity": 1,
                "price": 100.0,
                "fees": 0.0,
                "impact": 0.0,
            },
        ]
    )
    index = _index([(date(2025, 1, 1), 1000.0), (date(2025, 3, 2), 1100.0)])
    rep = horizon_report(
        fills, index, final_marks={(1, "Normal"): 150.0}, last_day=date(2025, 3, 2)
    )
    assert len(rep.trips) == 1
    assert rep.trips[0].still_open is True
    assert rep.trips[0].exit == date(2025, 3, 2)
    assert rep.trips[0].strategy_return == pytest.approx(0.5)


def test_same_day_entry_and_exit_has_zero_holding_days() -> None:
    fills = _fills(
        [
            {
                "day": date(2025, 1, 1),
                "product_id": 1,
                "sub_type": "Normal",
                "quantity": 1,
                "price": 100.0,
                "fees": 0.0,
                "impact": 0.0,
            },
            {
                "day": date(2025, 1, 1),
                "product_id": 1,
                "sub_type": "Normal",
                "quantity": -1,
                "price": 110.0,
                "fees": 0.0,
                "impact": 0.0,
            },
        ]
    )
    index = _index([(date(2025, 1, 1), 1000.0)])
    rep = horizon_report(fills, index, final_marks={}, last_day=date(2025, 1, 1))
    assert rep.trips[0].holding_days == 0
    assert rep.trips[0].index_return == 0.0


def test_index_uses_the_nearest_prior_date_when_a_day_is_missing() -> None:
    """Non-trading days must not silently become a zero index return."""
    fills = _fills(
        [
            {
                "day": date(2025, 1, 1),
                "product_id": 1,
                "sub_type": "Normal",
                "quantity": 1,
                "price": 100.0,
                "fees": 0.0,
                "impact": 0.0,
            },
            {
                "day": date(2025, 3, 2),
                "product_id": 1,
                "sub_type": "Normal",
                "quantity": -1,
                "price": 100.0,
                "fees": 0.0,
                "impact": 0.0,
            },
        ]
    )
    index = _index([(date(2025, 1, 1), 1000.0), (date(2025, 2, 28), 1100.0)])
    rep = horizon_report(fills, index, final_marks={}, last_day=date(2025, 3, 2))
    assert rep.trips[0].index_return == pytest.approx(0.10)


def test_a_sell_with_no_matching_buy_raises() -> None:
    """The engine cannot produce this; if it appears the artifact is wrong and
    must not be silently averaged into a result."""
    fills = _fills(
        [
            {
                "day": date(2025, 1, 1),
                "product_id": 1,
                "sub_type": "Normal",
                "quantity": -1,
                "price": 100.0,
                "fees": 0.0,
                "impact": 0.0,
            },
        ]
    )
    index = _index([(date(2025, 1, 1), 1000.0)])
    with pytest.raises(ValueError, match="no matching buy"):
        horizon_report(fills, index, final_marks={}, last_day=date(2025, 1, 1))


def test_win_rate_and_median_edge_summarise_the_distribution() -> None:
    """Three trips, two beating the flat index."""
    rows: list[dict[str, object]] = []
    for _i, (pid, exit_price) in enumerate([(1, 120.0), (2, 120.0), (3, 80.0)]):
        rows.append(
            {
                "day": date(2025, 1, 1),
                "product_id": pid,
                "sub_type": "Normal",
                "quantity": 1,
                "price": 100.0,
                "fees": 0.0,
                "impact": 0.0,
            }
        )
        rows.append(
            {
                "day": date(2025, 3, 2),
                "product_id": pid,
                "sub_type": "Normal",
                "quantity": -1,
                "price": exit_price,
                "fees": 0.0,
                "impact": 0.0,
            }
        )
    index = _index([(date(2025, 1, 1), 1000.0), (date(2025, 3, 2), 1000.0)])
    rep = horizon_report(_fills(rows), index, final_marks={}, last_day=date(2025, 3, 2))
    assert rep.win_rate == pytest.approx(2 / 3)
    assert rep.median_edge == pytest.approx(0.20)
