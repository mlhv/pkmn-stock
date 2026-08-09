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

    A **non-positive** relative spread is treated the same way, and that is
    a deliberate reversal of the obvious reading. `low == mid` scores 0.0,
    the tightest possible quote, and used to rank BEST of all — but low is
    the cheapest listing and mid the median listing, so their being equal
    means there is only one listing (or a handful at one price): the
    signature of no depth at all, not of a liquid book. That is not
    hypothetical. Product 91594 (`low == mid == 5000`, last print 19 months
    before the window ended) sorted top of the screened sealed universe on
    exactly this key. `low > mid` is incoherent for the same pair of
    definitions and is folded in with it. Since density saturates at the
    first bar (see the module docstring) this tiebreaker IS the screen
    there, so ranking degenerate quotes first was ranking illiquidity first.
    Infinity for all of them: no usable evidence of depth, sort last, stay
    buyable. `CostModel._impact` already reads a non-positive spread the
    same way — as "no walk to charge", not as a free fill.
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
    rel_spread = (pl.col("mid") - pl.col("low")) / pl.col("mid")
    return (
        density.join(latest, on=["product_id", "sub_type"], how="left")
        .with_columns(
            pl.when(
                pl.col("mid").is_null()
                | (pl.col("mid") <= 0)
                | pl.col("low").is_null()
                # Degenerate quote: one listing (low == mid) or a crossed one
                # (low > mid). Suspect, not best — see the docstring.
                | (rel_spread <= 0)
            )
            .then(float("inf"))
            .otherwise(rel_spread)
            .alias("spread")
        )
        .select("product_id", "sub_type", "n_prints", "spread")
        .sort(
            ["n_prints", "spread", "product_id", "sub_type"],
            descending=[True, False, False, False],
            nulls_last=True,
        )
    )
