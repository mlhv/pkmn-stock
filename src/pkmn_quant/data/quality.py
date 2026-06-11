"""Quality gates applied to each day's prices before they enter the warehouse.

Bad rows are quarantined with a reason code, never silently dropped.
"""

from __future__ import annotations

import polars as pl

# Day-over-day moves beyond this factor are treated as feed errors.
# Strict inequality: a move of exactly 10x is clean, symmetric on both sides.
JUMP_FACTOR = 10.0


def apply_quality_gates(
    prices: pl.DataFrame, previous: pl.DataFrame | None
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Split a day's prices into (clean, quarantined-with-reason).

    `previous` must be the CLEAN output of the prior day's call (or None):
    unique (product_id, sub_type) keys and market > 0 for every row. Passing
    an ungated frame can multiply rows through the join and divide by zero.
    The clean frame has the original columns; the quarantined frame adds
    a `reason` column.
    """
    df = prices.with_columns(
        pl.when(pl.col("market").is_null())
        .then(pl.lit("null_market"))
        .when(pl.col("market") <= 0)
        .then(pl.lit("nonpositive_market"))
        .when(pl.struct(["product_id", "sub_type"]).is_duplicated())
        .then(pl.lit("duplicate"))
        .otherwise(pl.lit(None, dtype=pl.Utf8))
        .alias("reason")
    )

    if previous is not None and previous.height > 0:
        prev = previous.select("product_id", "sub_type", pl.col("market").alias("prev_market"))
        ratio = pl.col("market") / pl.col("prev_market")
        df = (
            df.join(prev, on=["product_id", "sub_type"], how="left")
            .with_columns(
                pl.when(
                    pl.col("reason").is_null()
                    & pl.col("prev_market").is_not_null()
                    & ((ratio > JUMP_FACTOR) | (ratio < 1 / JUMP_FACTOR))
                )
                .then(pl.lit("price_jump"))
                .otherwise(pl.col("reason"))
                .alias("reason")
            )
            .drop("prev_market")
        )

    clean = df.filter(pl.col("reason").is_null()).drop("reason")
    quarantined = df.filter(pl.col("reason").is_not_null())
    return clean, quarantined
