"""Pure feature/label builders for the ML ranker.

Leakage rules enforced here (and pinned by tests):
- build_features(h, p, as_of) reads only rows with date <= as_of (it filters
  first, so passing a frame containing future rows is harmless).
- build_training_frame only emits training dates D <= as_of - horizon_days:
  the k-day-forward label reads a price at most `horizon_days` after D,
  which is still <= as_of. Today's cross-section is predicted on, never
  trained on.
- stride_days-spaced training dates keep overlapping forward-return labels
  from masquerading as independent samples (default stride = horizon).
"""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from pkmn_quant.engine.costs import CostModel

ID_COLS = ["product_id", "sub_type"]
# Order matters: the strategy feeds columns to sklearn in this order.
FEATURE_COLS = [
    "ret_7d",
    "ret_30d",
    "ret_90d",
    "vol_30d",
    "dip_90d",
    "log_price",
    "days_since_release",
    "is_sealed",
]


def _last_price_at_or_before(h: pl.DataFrame, day: date, alias: str) -> pl.DataFrame:
    return (
        h.filter(pl.col("date") <= day)
        .sort("date")
        .group_by(ID_COLS)
        .agg(pl.col("market").last().alias(alias))
    )


def build_features(history: pl.DataFrame, products: pl.DataFrame, as_of: date) -> pl.DataFrame:
    """One row per asset printing on `as_of`; columns ID_COLS + market + FEATURE_COLS."""
    h = history.select("date", *ID_COLS, "market").filter(pl.col("date") <= as_of)
    feats = h.filter(pl.col("date") == as_of).select(*ID_COLS, "market")
    for w in (7, 30, 90):
        past = _last_price_at_or_before(h, as_of - timedelta(days=w), f"_past_{w}")
        feats = (
            feats.join(past, on=ID_COLS, how="left")
            .with_columns((pl.col("market") / pl.col(f"_past_{w}") - 1.0).alias(f"ret_{w}d"))
            .drop(f"_past_{w}")
        )
    # dip_90d is a windowed max (date > as_of-90); ret_90d is a point-anchor (at/before as_of-90).
    high = (
        h.filter(pl.col("date") > as_of - timedelta(days=90))
        .group_by(ID_COLS)
        .agg(pl.col("market").max().alias("_high"))
    )
    feats = (
        feats.join(high, on=ID_COLS, how="left")
        .with_columns((1.0 - pl.col("market") / pl.col("_high")).alias("dip_90d"))
        .drop("_high")
    )
    vol = (
        h.filter(pl.col("date") > as_of - timedelta(days=30))
        .sort("date")
        .group_by(ID_COLS)
        .agg(pl.col("market").pct_change().std().alias("vol_30d"))
    )
    feats = feats.join(vol, on=ID_COLS, how="left")
    meta = products.select(
        "product_id",
        (pl.col("kind") == "sealed").cast(pl.Float64).alias("is_sealed"),
        # Negative when released_on > as_of; harmless for tree models and intentionally kept.
        (pl.lit(as_of, dtype=pl.Date) - pl.col("released_on"))
        .dt.total_days()
        .cast(pl.Float64)
        .alias("days_since_release"),
    )
    feats = feats.join(meta, on="product_id", how="left").with_columns(
        pl.col("market").log().alias("log_price")
    )
    return feats.select(*ID_COLS, "market", *FEATURE_COLS)


def build_training_frame(
    history: pl.DataFrame,
    products: pl.DataFrame,
    as_of: date,
    horizon_days: int,
    train_days: int,
    stride_days: int,
) -> pl.DataFrame:
    """Feature rows at strided past dates + `label` = horizon-forward return.

    Emitted columns: date, ID_COLS, market, FEATURE_COLS, label. Rows whose
    label price is missing are dropped. May be empty (thin history) — the
    caller decides whether that is enough to fit a model.
    """
    h = history.select("date", *ID_COLS, "market").filter(pl.col("date") <= as_of)
    frames: list[pl.DataFrame] = []
    d = as_of - timedelta(days=horizon_days)  # newest legal training date
    lower = as_of - timedelta(days=train_days)
    while d >= lower:
        feats = build_features(h, products, d)
        if feats.height:
            label_price = _last_price_at_or_before(h, d + timedelta(days=horizon_days), "_lbl")
            frames.append(
                feats.join(label_price, on=ID_COLS, how="left")
                .with_columns(
                    (pl.col("_lbl") / pl.col("market") - 1.0).alias("label"),
                    pl.lit(d, dtype=pl.Date).alias("date"),
                )
                .drop("_lbl")
                .drop_nulls("label")
            )
        d -= timedelta(days=stride_days)
    if not frames:
        return pl.DataFrame(
            schema={
                "date": pl.Date,
                "product_id": pl.Int64,
                "sub_type": pl.Utf8,
                "market": pl.Float64,
                **{c: pl.Float64 for c in FEATURE_COLS},
                "label": pl.Float64,
            }
        )
    return pl.concat(frames).select("date", *ID_COLS, "market", *FEATURE_COLS, "label")


# ---- v2 (ml-ranker-v2): friction-aware feature set -------------------------
# v1 (FEATURE_COLS/build_features/build_training_frame) is frozen for
# reproducibility; v2 appends 8 leakage-bounded features. mid/low are carried
# through the output for the net-of-cost label, but are NOT model inputs.

FEATURE_COLS_V2 = [
    *FEATURE_COLS,
    "spread_frac",
    "mid_gap",
    "spread_30d_mean",
    "ret_accel",
    "drawdown_180d",
    "vol_ratio",
    "xs_rank_ret_30d",
    "days_priced",
]


def build_features_v2(history: pl.DataFrame, products: pl.DataFrame, as_of: date) -> pl.DataFrame:
    """One row per asset printing on as_of; ID_COLS + market/mid/low +
    FEATURE_COLS_V2. Same leakage rule as v1: reads only rows <= as_of."""
    h = history.select("date", *ID_COLS, "market", "low", "mid").filter(pl.col("date") <= as_of)
    base = build_features(h.select("date", *ID_COLS, "market"), products, as_of)
    today = h.filter(pl.col("date") == as_of).select(*ID_COLS, "low", "mid")
    f = base.join(today, on=ID_COLS, how="left").with_columns(
        ((pl.col("market") - pl.col("low")) / pl.col("market")).alias("spread_frac"),
        ((pl.col("market") - pl.col("mid")) / pl.col("market")).alias("mid_gap"),
        (pl.col("ret_7d") - pl.col("ret_30d")).alias("ret_accel"),
    )
    spread30 = (
        h.filter(pl.col("date") > as_of - timedelta(days=30))
        .group_by(ID_COLS)
        .agg(
            ((pl.col("market") - pl.col("low")) / pl.col("market")).mean().alias("spread_30d_mean")
        )
    )
    high180 = (
        h.filter(pl.col("date") > as_of - timedelta(days=180))
        .group_by(ID_COLS)
        .agg(pl.col("market").max().alias("_high180"))
    )
    vol7 = (
        h.filter(pl.col("date") > as_of - timedelta(days=7))
        .sort("date")
        .group_by(ID_COLS)
        .agg(pl.col("market").pct_change().std().alias("_vol7"))
    )
    counts = h.group_by(ID_COLS).agg(pl.len().cast(pl.Float64).alias("days_priced"))
    f = (
        f.join(spread30, on=ID_COLS, how="left")
        .join(high180, on=ID_COLS, how="left")
        .join(vol7, on=ID_COLS, how="left")
        .join(counts, on=ID_COLS, how="left")
        .with_columns(
            (1.0 - pl.col("market") / pl.col("_high180")).alias("drawdown_180d"),
            (pl.col("_vol7") / pl.col("vol_30d")).alias("vol_ratio"),
            (pl.col("ret_30d").rank() / pl.col("ret_30d").count()).alias("xs_rank_ret_30d"),
        )
        .drop("_high180", "_vol7")
    )
    return f.select(*ID_COLS, "market", "mid", "low", *FEATURE_COLS_V2)


def cost_frac_expr(cm: CostModel) -> pl.Expr:
    """Round-trip cost of one unit as a fraction of `market`, vectorized.

    Identity with the scalar CostModel (pinned by test):
      (buy_price + buy_impact(qty=1)) - (sell_proceeds - sell_impact(qty=1))
      = 2*shipping + market*fee_rate + buy_impact + sell_impact
    Impact terms are zero when disabled, quotes missing, or quotes crossed —
    mirroring CostModel._impact, never inventing costs from missing data.
    """
    market = pl.col("market")
    cap: pl.Expr = pl.lit(float(cm.fallback_max_qty))
    for threshold, qty in reversed(cm.liquidity_tiers):
        cap = pl.when(market < threshold).then(float(qty)).otherwise(cap)
    buy_spread = pl.col("mid") - market
    sell_spread = market - pl.col("low")
    buy_imp = (
        pl.when(pl.lit(cm.impact_enabled) & (buy_spread > 0))
        .then(buy_spread / (2.0 * cap))
        .otherwise(0.0)
        .fill_null(0.0)
    )
    sell_imp = (
        pl.when(pl.lit(cm.impact_enabled) & (sell_spread > 0))
        .then(sell_spread / (2.0 * cap))
        .otherwise(0.0)
        .fill_null(0.0)
    )
    round_trip = 2.0 * cm.shipping_per_line + market * cm.fee_rate + buy_imp + sell_imp
    return round_trip / market


def build_training_frame_v2(
    history: pl.DataFrame,
    products: pl.DataFrame,
    as_of: date,
    horizon_days: int,
    train_days: int,
    stride_days: int,
    cost_model: CostModel,
) -> pl.DataFrame:
    """v1's strided training frame with v2 features and NET labels:
    label = horizon forward return - round-trip cost fraction at the
    training date's own prices/quotes. Same leakage bound as v1."""
    h = history.select("date", *ID_COLS, "market", "low", "mid").filter(pl.col("date") <= as_of)
    frames: list[pl.DataFrame] = []
    d = as_of - timedelta(days=horizon_days)
    lower = as_of - timedelta(days=train_days)
    while d >= lower:
        feats = build_features_v2(h, products, d)
        if feats.height:
            label_price = _last_price_at_or_before(
                h.select("date", *ID_COLS, "market"), d + timedelta(days=horizon_days), "_lbl"
            )
            frames.append(
                feats.join(label_price, on=ID_COLS, how="left")
                .with_columns(
                    ((pl.col("_lbl") / pl.col("market") - 1.0) - cost_frac_expr(cost_model)).alias(
                        "label"
                    ),
                    pl.lit(d, dtype=pl.Date).alias("date"),
                )
                .drop("_lbl")
                .drop_nulls("label")
            )
        d -= timedelta(days=stride_days)
    if not frames:
        return pl.DataFrame(
            schema={
                "date": pl.Date,
                "product_id": pl.Int64,
                "sub_type": pl.Utf8,
                "market": pl.Float64,
                **{c: pl.Float64 for c in FEATURE_COLS_V2},
                "label": pl.Float64,
            }
        )
    return pl.concat(frames).select("date", *ID_COLS, "market", *FEATURE_COLS_V2, "label")
