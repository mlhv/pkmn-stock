"""Feature/label builders: hand-derived values + the leakage regression tests."""

import math
from datetime import date, timedelta
from itertools import pairwise

import polars as pl

from pkmn_quant.research.features import (
    FEATURE_COLS,
    FEATURE_COLS_V2,
    ID_COLS,
    build_features,
    build_features_v2,
    build_training_frame,
)


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


def _daily(pid: int, start: date, prices: list[float]) -> list[tuple[date, int, str, float]]:
    return [(start + timedelta(days=i), pid, "Normal", p) for i, p in enumerate(prices)]


def test_feature_cols_are_exported() -> None:
    """FEATURE_COLS is part of the public API consumed by the strategy."""
    assert len(FEATURE_COLS) == 8
    assert "ret_7d" in FEATURE_COLS
    assert "label" not in FEATURE_COLS


def test_features_hand_derived() -> None:
    """price[i] = 100 + max(0, i-9): flat at 100 through day 9, then +1/day,
    ending 130 on day 39 (= as_of). ret_7d uses the last price at or before
    as_of - 7 = day 32 -> 123, so ret_7d = 130/123 - 1. ret_30d uses day 9
    -> 100, so ret_30d = 130/100 - 1. dip_90d = 0 (as_of IS the high)."""
    start = date(2025, 1, 1)
    prices = [100.0 + max(0, i - 9) for i in range(40)]
    h = _history(_daily(1, start, prices))
    as_of = start + timedelta(days=39)
    feats = build_features(h, _products([(1, "single")]), as_of)
    row = feats.row(by_predicate=pl.col("product_id") == 1, named=True)
    assert abs(row["ret_7d"] - (130.0 / 123.0 - 1.0)) < 1e-12
    assert abs(row["ret_30d"] - (130.0 / 100.0 - 1.0)) < 1e-12
    assert row["dip_90d"] == 0.0
    assert row["is_sealed"] == 0.0
    assert row["days_since_release"] == float((as_of - date(2024, 1, 1)).days)


def test_short_history_features_are_null_not_wrong() -> None:
    """3 prints: 90d/30d lookbacks have no price at/before the window start
    -> those features are null (model handles nulls natively), never 0."""
    start = date(2025, 1, 1)
    h = _history(_daily(1, start, [100.0, 101.0, 102.0]))
    feats = build_features(h, _products([(1, "single")]), start + timedelta(days=2))
    row = feats.row(by_predicate=pl.col("product_id") == 1, named=True)
    assert row["ret_90d"] is None
    assert row["log_price"] is not None


def test_universe_is_assets_printing_on_as_of() -> None:
    start = date(2025, 1, 1)
    rows = _daily(1, start, [100.0] * 10) + _daily(2, start, [50.0] * 9)  # 2 stops early
    feats = build_features(
        _history(rows), _products([(1, "single"), (2, "sealed")]), start + timedelta(days=9)
    )
    assert feats["product_id"].to_list() == [1]


def test_leakage_appending_future_rows_changes_nothing() -> None:
    """THE guard: features and training frame as of D are bit-identical
    whether or not the history contains rows after D."""
    start = date(2025, 1, 1)
    as_of = start + timedelta(days=120)
    past = _daily(1, start, [100.0 + i * 0.1 for i in range(121)])
    future = _daily(1, as_of + timedelta(days=1), [999.0] * 30)
    products = _products([(1, "single")])
    f_clean = build_features(_history(past), products, as_of)
    f_dirty = build_features(_history(past + future), products, as_of)
    assert f_clean.equals(f_dirty)
    kw = dict(horizon_days=14, train_days=90, stride_days=14)
    t_clean = build_training_frame(_history(past), products, as_of, **kw)
    t_dirty = build_training_frame(_history(past + future), products, as_of, **kw)
    assert t_clean.equals(t_dirty)


def test_label_legality_boundary() -> None:
    """Training dates run back from exactly as_of - horizon; nothing later."""
    start = date(2025, 1, 1)
    as_of = start + timedelta(days=120)
    h = _history(_daily(1, start, [100.0] * 121))
    t = build_training_frame(
        h, _products([(1, "single")]), as_of, horizon_days=30, train_days=120, stride_days=30
    )
    assert t["date"].max() == as_of - timedelta(days=30)
    assert t["date"].min() >= as_of - timedelta(days=120)
    # stride: consecutive training dates 30 days apart
    ds = sorted(t["date"].unique().to_list())
    assert len(ds) >= 2  # need at least two dates to check stride
    assert all((b - a).days == 30 for a, b in pairwise(ds))


def test_vol_30d_hand_derived() -> None:
    """Prices 100, 110, 99 on consecutive days (all within the 30d window):
    daily returns 0.10 and -0.10; sample std (ddof=1) of [0.10, -0.10] =
    sqrt(((0.10-0.0)**2 + (-0.10-0.0)**2) / 1) = sqrt(0.02) = 0.1414...
    A constant-price asset has returns [0.0, 0.0] -> std 0.0 exactly."""
    start = date(2025, 1, 1)
    rows = _daily(1, start, [100.0, 110.0, 99.0]) + _daily(2, start, [50.0, 50.0, 50.0])
    feats = build_features(
        _history(rows), _products([(1, "single"), (2, "single")]), start + timedelta(days=2)
    )
    r1 = feats.row(by_predicate=pl.col("product_id") == 1, named=True)
    r2 = feats.row(by_predicate=pl.col("product_id") == 2, named=True)
    assert abs(r1["vol_30d"] - math.sqrt(0.02)) < 1e-12
    assert r2["vol_30d"] == 0.0


def test_log_price_hand_derived() -> None:
    start = date(2025, 1, 1)
    feats = build_features(_history(_daily(1, start, [100.0])), _products([(1, "single")]), start)
    row = feats.row(by_predicate=pl.col("product_id") == 1, named=True)
    assert abs(row["log_price"] - math.log(100.0)) < 1e-12


def test_label_is_forward_return() -> None:
    """Price 100 at D, 110 at D+14 -> label 0.10."""
    start = date(2025, 1, 1)
    prices = [100.0] * 47 + [110.0] * 14  # jump at day 47
    h = _history(_daily(1, start, prices))
    as_of = start + timedelta(days=60)
    t = build_training_frame(
        h, _products([(1, "single")]), as_of, horizon_days=14, train_days=30, stride_days=14
    )
    row = t.filter(pl.col("date") == as_of - timedelta(days=14)).row(0, named=True)
    assert abs(row["label"] - 0.10) < 1e-12


def _hist_v2() -> pl.DataFrame:
    """3 assets, 40 daily rows each, with quotes; asset 3 starts late."""
    rows = []
    for i in range(40):
        d = date(2025, 1, 1) + timedelta(days=i)
        rows.append(
            {
                "date": d,
                "product_id": 1,
                "sub_type": "Normal",
                "market": 10.0 + i * 0.1,
                "low": 9.0 + i * 0.1,
                "mid": 10.5 + i * 0.1,
            }
        )
        rows.append(
            {
                "date": d,
                "product_id": 2,
                "sub_type": "Normal",
                "market": 50.0 - i * 0.2,
                "low": 48.0 - i * 0.2,
                "mid": 51.0 - i * 0.2,
            }
        )
        if i >= 30:
            rows.append(
                {
                    "date": d,
                    "product_id": 3,
                    "sub_type": "Normal",
                    "market": 5.0,
                    "low": None,
                    "mid": None,
                }
            )
    return pl.DataFrame(rows)


def _products_v2() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "product_id": [1, 2, 3],
            "group_id": [1, 1, 1],
            "name": ["A", "B", "C"],
            "rarity": [None, None, None],
            "kind": ["sealed", "single", "single"],
            "released_on": [date(2024, 12, 1)] * 3,
        }
    )


def test_feature_cols_v2_are_exported() -> None:
    """FEATURE_COLS_V2 extends FEATURE_COLS with the 8 new v2 features."""
    assert [
        *FEATURE_COLS,
        "spread_frac",
        "mid_gap",
        "spread_30d_mean",
        "ret_accel",
        "drawdown_180d",
        "vol_ratio",
        "xs_rank_ret_30d",
        "days_priced",
    ] == FEATURE_COLS_V2


def test_v2_formulas_hand_checked() -> None:
    as_of = date(2025, 2, 9)  # day index 39
    f = build_features_v2(_hist_v2(), _products_v2(), as_of)
    r1 = f.filter(pl.col("product_id") == 1).row(0, named=True)
    m, lo, mid = 13.9, 12.9, 14.4  # asset 1 on day 39
    assert abs(r1["spread_frac"] - (m - lo) / m) < 1e-12
    assert abs(r1["mid_gap"] - (m - mid) / m) < 1e-12
    assert abs(r1["ret_accel"] - (r1["ret_7d"] - r1["ret_30d"])) < 1e-12
    assert r1["days_priced"] == 40.0
    r3 = f.filter(pl.col("product_id") == 3).row(0, named=True)
    assert r3["spread_frac"] is None  # no quotes -> null, not invented
    assert r3["days_priced"] == 10.0
    # asset 1 rose, asset 2 fell: cross-sectional rank of ret_30d separates them
    r2 = f.filter(pl.col("product_id") == 2).row(0, named=True)
    assert r1["xs_rank_ret_30d"] > r2["xs_rank_ret_30d"]
    # drawdown: asset 2 is at its 180d low -> big drawdown; asset 1 at high -> 0
    assert abs(r1["drawdown_180d"]) < 1e-12
    assert r2["drawdown_180d"] > 0.1


def test_v2_leakage_bounded() -> None:
    """Appending future rows must not change any v2 feature (as_of filter)."""
    as_of = date(2025, 2, 5)
    base = build_features_v2(_hist_v2(), _products_v2(), as_of)
    future = pl.DataFrame(
        [
            {
                "date": date(2025, 3, 1),
                "product_id": 1,
                "sub_type": "Normal",
                "market": 999.0,
                "low": 990.0,
                "mid": 999.5,
            }
        ]
    )
    poisoned = pl.concat([_hist_v2(), future], how="diagonal")
    assert base.equals(build_features_v2(poisoned, _products_v2(), as_of))


def test_v2_carries_v1_columns_unchanged() -> None:
    as_of = date(2025, 2, 9)
    v1 = build_features(_hist_v2().select("date", *ID_COLS, "market"), _products_v2(), as_of)
    v2 = build_features_v2(_hist_v2(), _products_v2(), as_of)
    joined = v1.join(v2, on=ID_COLS, suffix="_v2")
    for c in FEATURE_COLS:
        a, b = joined[c].to_list(), joined[f"{c}_v2"].to_list()
        assert a == b, c


def test_cost_frac_expr_matches_scalar_cost_model() -> None:
    """The vectorized label cost must equal the scalar CostModel arithmetic:
    cost = (buy_price + buy_impact) - (sell_proceeds - sell_impact), as a
    fraction of market, for one unit. Swept across tier edges and quote
    shapes (present / missing / crossed)."""
    from pkmn_quant.engine.costs import CostModel
    from pkmn_quant.research.features import cost_frac_expr

    cm = CostModel(impact_enabled=True)
    cases = []
    for market in (0.5, 4.99, 5.0, 49.99, 50.0, 199.99, 200.0, 350.0):
        for mid, low in (
            (market * 1.05, market * 0.9),  # normal quotes
            (None, None),  # missing
            (market * 0.95, market * 1.1),  # crossed both ways -> zero impact
        ):
            cases.append({"market": market, "mid": mid, "low": low})
    frame = pl.DataFrame(cases).with_columns(cost_frac_expr(cm).alias("frac"))
    for row in frame.iter_rows(named=True):
        m, mid, low = row["market"], row["mid"], row["low"]
        scalar = (
            (cm.buy_price(m) + cm.buy_impact(m, mid, 1))
            - (cm.sell_proceeds(m) - cm.sell_impact(m, low, 1))
        ) / m
        assert abs(row["frac"] - scalar) < 1e-12, (m, mid, low)


def test_cost_frac_hand_derivation() -> None:
    """market=10, mid=10.5, low=9, defaults (fee 12.75%, ship $1, cap 8 for
    5<=p<50, impact on): cost = 2*1 + 10*0.1275 + (10.5-10)/16 + (10-9)/16
    = 2 + 1.275 + 0.03125 + 0.0625 = 3.36875; frac = 0.336875."""
    from pkmn_quant.engine.costs import CostModel
    from pkmn_quant.research.features import cost_frac_expr

    f = pl.DataFrame([{"market": 10.0, "mid": 10.5, "low": 9.0}]).with_columns(
        cost_frac_expr(CostModel(impact_enabled=True)).alias("frac")
    )
    assert abs(f["frac"][0] - 0.336875) < 1e-12


def test_training_frame_v2_labels_are_net() -> None:
    """label_v2 == v1 gross label minus that row's cost fraction."""
    from pkmn_quant.engine.costs import CostModel
    from pkmn_quant.research.features import (
        build_training_frame,
        build_training_frame_v2,
        cost_frac_expr,
    )

    cm = CostModel(impact_enabled=True)
    kw = dict(as_of=date(2025, 2, 9), horizon_days=10, train_days=30, stride_days=10)
    v1 = build_training_frame(_hist_v2().select("date", *ID_COLS, "market"), _products_v2(), **kw)
    v2 = build_training_frame_v2(_hist_v2(), _products_v2(), cost_model=cm, **kw)
    j = v1.join(v2, on=["date", *ID_COLS], suffix="_v2")
    assert j.height > 0
    costs = (
        _hist_v2()
        .join(j.select("date", *ID_COLS), on=["date", *ID_COLS], how="semi")
        .with_columns(cost_frac_expr(cm).alias("frac"))
        .sort("date", "product_id")
    )
    jj = j.sort("date", "product_id")
    for gross, net, frac in zip(
        jj["label"].to_list(),
        jj["label_v2"].to_list(),
        costs["frac"].to_list(),
        strict=True,
    ):
        assert abs(net - (gross - frac)) < 1e-12
