from datetime import date, timedelta

import polars as pl
import pytest

from pkmn_quant.engine.portfolio import Asset, Position
from pkmn_quant.engine.strategy import Context
from pkmn_quant.strategies.ml_ranker import MLRanker

START = date(2025, 1, 1)
TODAY = START + timedelta(days=199)


def _history(rows: list[tuple[date, int, str, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "date": [r[0] for r in rows],
            "product_id": [r[1] for r in rows],
            "sub_type": [r[2] for r in rows],
            "market": [r[3] for r in rows],
        },
        schema={"date": pl.Date, "product_id": pl.Int64, "sub_type": pl.Utf8, "market": pl.Float64},
    )


def _products() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "product_id": [1, 2],
            "group_id": [1, 1],
            "name": ["Riser", "Faller"],
            "rarity": [None, None],
            "kind": ["single", "sealed"],
            "released_on": [date(2024, 1, 1), date(2024, 1, 1)],
        }
    )


def _trend_history() -> pl.DataFrame:
    """Asset 1 rises 0.5%/day, asset 2 falls 0.5%/day, 200 daily prints."""
    rows: list[tuple[date, int, str, float]] = []
    for i in range(200):
        d = START + timedelta(days=i)
        rows.append((d, 1, "Normal", 100.0 * 1.005**i))
        rows.append((d, 2, "Normal", 100.0 * 0.995**i))
    return _history(rows)


def _ranker(**kw: object) -> MLRanker:
    defaults: dict[str, object] = dict(
        horizon_days=14,
        rebalance_days=30,
        top_n=1,
        train_days=120,
        stride_days=7,
        min_train_rows=10,
    )
    defaults.update(kw)
    return MLRanker(**defaults)  # type: ignore[arg-type]


def _ctx(positions: dict[Asset, Position], cash: float, history: pl.DataFrame) -> Context:
    latest = history.filter(pl.col("date") == TODAY)
    marks = {
        Asset(int(r["product_id"]), str(r["sub_type"])): float(r["market"])
        for r in latest.iter_rows(named=True)
    }
    return Context(
        today=TODAY,
        history=history,
        products=_products(),
        positions=positions,
        cash=cash,
        marks=marks,
    )


def test_ranker_buys_the_riser_not_the_faller() -> None:
    """Synthetic monotone data: the trained model must rank the riser first."""
    orders = _ranker().on_bar(_ctx({}, 1000.0, _trend_history()))
    buys = [o for o in orders if o.quantity > 0]
    assert buys and all(o.asset == Asset(1, "Normal") for o in buys)


def test_ranker_sells_dropout_holding() -> None:
    """Held faller, top_n=1 -> faller leaves the target, riser enters."""
    pos = {Asset(2, "Normal"): Position(quantity=3, avg_cost=80.0, opened_on=START)}
    orders = _ranker().on_bar(_ctx(pos, 1000.0, _trend_history()))
    sells = [o for o in orders if o.quantity < 0]
    assert [(-o.quantity, o.asset) for o in sells] == [(3, Asset(2, "Normal"))]
    assert orders[0].quantity < 0  # sells emitted before buys


def test_rebalance_clock_derived_from_opened_on() -> None:
    """Held 29 days: not due, no orders, no training."""
    pos = {
        Asset(1, "Normal"): Position(
            quantity=1, avg_cost=100.0, opened_on=TODAY - timedelta(days=29)
        )
    }
    assert _ranker().on_bar(_ctx(pos, 100.0, _trend_history())) == []


def test_none_opened_on_raises() -> None:
    pos = {Asset(1, "Normal"): Position(quantity=1, avg_cost=100.0)}
    with pytest.raises(ValueError, match="opened_on"):
        _ranker().on_bar(_ctx(pos, 100.0, _trend_history()))


def test_deterministic_and_stateless() -> None:
    """Same Context twice on one instance AND on a fresh instance -> same orders."""
    h = _trend_history()
    s = _ranker()
    first = s.on_bar(_ctx({}, 1000.0, h))
    assert s.on_bar(_ctx({}, 1000.0, h)) == first
    assert _ranker().on_bar(_ctx({}, 1000.0, h)) == first


def test_degenerate_training_data_no_orders() -> None:
    """min_train_rows unmet (default 200 vs tiny history) -> hold: no orders."""
    rows = [(TODAY - timedelta(days=i), 1, "Normal", 100.0) for i in range(20, -1, -1)]
    s = MLRanker()  # default min_train_rows=200
    assert s.on_bar(_ctx({}, 1000.0, _history(rows))) == []


def test_min_price_excludes_cheap_assets() -> None:
    """Riser priced below min_price -> excluded from target -> no buys for it."""
    rows: list[tuple[date, int, str, float]] = []
    for i in range(200):
        d = START + timedelta(days=i)
        rows.append((d, 1, "Normal", 1.0 * 1.005**i))  # ends ~2.70 < 3.0
        rows.append((d, 2, "Normal", 100.0 * 0.995**i))
    orders = _ranker(min_price=3.0, top_n=2).on_bar(_ctx({}, 1000.0, _history(rows)))
    assert all(o.asset != Asset(1, "Normal") for o in orders if o.quantity > 0)


def test_all_null_feature_column_does_not_crash_fit() -> None:
    """Early-history folds: no asset has a price 90 days back, so ret_90d is
    null in EVERY training row. sklearn 1.9's binner crashes on all-NaN
    columns; the strategy must drop all-null features for that fit instead.
    History spans 70 days, so ret_90d is null everywhere while ret_7d/30d
    exist; with min_train_rows=10 the model must still fit and rank."""
    rows: list[tuple[date, int, str, float]] = []
    start = TODAY - timedelta(days=69)  # 70 days of history, < 90
    for i in range(70):
        d = start + timedelta(days=i)
        rows.append((d, 1, "Normal", 100.0 * 1.01**i))
        rows.append((d, 2, "Normal", 100.0 * 0.99**i))
    orders = _ranker().on_bar(_ctx({}, 1000.0, _history(rows)))
    buys = [o for o in orders if o.quantity > 0]
    assert buys and all(o.asset == Asset(1, "Normal") for o in buys)


def test_ranking_follows_momentum_not_price_level() -> None:
    """Discriminating case: the RISER is cheap (ends ~13.5, above min_price 3),
    the FALLER is expensive (ends ~135). A model ranking by log_price alone
    would buy the faller; learning the momentum features picks the riser."""
    rows: list[tuple[date, int, str, float]] = []
    for i in range(200):
        d = START + timedelta(days=i)
        rows.append((d, 1, "Normal", 5.0 * 1.005**i))  # cheap riser -> ~13.5
        rows.append((d, 2, "Normal", 370.0 * 0.995**i))  # expensive faller -> ~135
    orders = _ranker().on_bar(_ctx({}, 1000.0, _history(rows)))
    buys = [o for o in orders if o.quantity > 0]
    assert buys and all(o.asset == Asset(1, "Normal") for o in buys)
