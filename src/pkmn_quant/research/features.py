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
