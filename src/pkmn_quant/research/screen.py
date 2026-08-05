"""Rank assets by tradeability: print density first, quote sanity second.

Density is the honest primary key, but at the first bar of a backtest the
available history is only whatever precedes the start date (22 days for a
2024-03-01 start), so density saturates and cannot discriminate. Relative
spread carries the load there, and doubles as a quote-sanity filter:
products whose listings are internally consistent sort ahead of those whose
are not.

Leakage-bounded by construction — callers pass `today` and rows after it are
dropped, so this can be called from inside a strategy's on_bar.

Mirrored bit-for-bit by the C++ engine's BuyAndHold (see
cpp/src/pkmn_engine/strategies/buy_and_hold.cpp), which reads the same
quantities through MarketView::print_count_at_or_before and
MarketView::quote_at_or_before. Any change to the sort key or to the
undefined-spread rule must land on both sides in the same commit.
"""

from __future__ import annotations

from datetime import date

import polars as pl


def rank_assets(history: pl.DataFrame, today: date) -> pl.DataFrame:
    """One row per (product_id, sub_type), best-first.

    Sort key: n_prints descending, then relative spread (mid-low)/mid
    ascending, then product_id, then sub_type — the last two purely so the
    order is total and reproducible, which the C++ port depends on.

    The quote feeding the spread is the asset's most recent print at or
    before `today`. A row whose mid or low is null, or whose mid is <= 0,
    has an undefined spread and is given infinity so it sorts last without
    being dropped — never null, so the returned `spread` is always a usable
    float and matches the NaN handling on the C++ side.
    """
    seen = history.filter(pl.col("date") <= today)
    density = seen.group_by("product_id", "sub_type").agg(
        pl.col("date").n_unique().alias("n_prints")
    )
    latest = (
        seen.sort("date")
        .group_by("product_id", "sub_type")
        .agg(pl.col("low").last().alias("low"), pl.col("mid").last().alias("mid"))
    )
    return (
        density.join(latest, on=["product_id", "sub_type"], how="left")
        .with_columns(
            pl.when(pl.col("mid").is_null() | (pl.col("mid") <= 0) | pl.col("low").is_null())
            .then(float("inf"))
            .otherwise((pl.col("mid") - pl.col("low")) / pl.col("mid"))
            .alias("spread")
        )
        .select("product_id", "sub_type", "n_prints", "spread")
        .sort(
            ["n_prints", "spread", "product_id", "sub_type"],
            descending=[True, False, False, False],
            nulls_last=True,
        )
    )
