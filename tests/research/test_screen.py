"""The liquidity screen: rank assets by print density, then quote sanity."""

from datetime import date

import polars as pl

from pkmn_quant.research.screen import rank_assets

D = date(2025, 6, 3)


def _rows(rows: list[tuple[int, str, date, float, float, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {"product_id": p, "sub_type": s, "date": d, "low": lo, "mid": md, "market": mk}
            for p, s, d, lo, md, mk in rows
        ]
    )


def test_denser_asset_ranks_first() -> None:
    """Print density is the primary key: three prints beats one."""
    df = _rows(
        [
            (1, "Normal", date(2025, 6, 1), 10.0, 12.0, 11.0),
            (1, "Normal", date(2025, 6, 2), 10.0, 12.0, 11.0),
            (1, "Normal", D, 10.0, 12.0, 11.0),
            (2, "Normal", D, 10.0, 12.0, 11.0),
        ]
    )
    out = rank_assets(df, today=D)
    assert out["product_id"].to_list() == [1, 2]
    assert out["n_prints"].to_list() == [3, 1]


def test_tighter_spread_breaks_a_density_tie() -> None:
    """Equal density -> the tighter relative spread wins. This is the key that
    does the real work at the first bar, where density saturates."""
    df = _rows(
        [
            (1, "Normal", D, 5.0, 10.0, 7.0),  # spread 0.5
            (2, "Normal", D, 9.0, 10.0, 9.5),  # spread 0.1
        ]
    )
    assert rank_assets(df, today=D)["product_id"].to_list() == [2, 1]


def test_missing_mid_sorts_last() -> None:
    """A product with no mid has an undefined spread; it must sort last, not
    first, and must not be dropped."""
    df = _rows([(1, "Normal", D, 9.0, 10.0, 9.5)])
    df = pl.concat(
        [
            df,
            pl.DataFrame(
                [
                    {
                        "product_id": 2,
                        "sub_type": "Normal",
                        "date": D,
                        "low": 9.0,
                        "mid": None,
                        "market": 9.5,
                    }
                ],
                schema=df.schema,
            ),
        ]
    )
    out = rank_assets(df, today=D)
    assert out["product_id"].to_list() == [1, 2]


def test_missing_low_sorts_last() -> None:
    """A null low leaves the spread undefined just as a null mid does.

    Without this the spread column would carry a null, and BuyAndHold's sort
    key would compare None against a float (TypeError) — and the C++ port,
    which sees NaN, would silently order it differently. Infinity in both.
    """
    df = _rows([(1, "Normal", D, 9.0, 10.0, 9.5)])
    df = pl.concat(
        [
            df,
            pl.DataFrame(
                [
                    {
                        "product_id": 2,
                        "sub_type": "Normal",
                        "date": D,
                        "low": None,
                        "mid": 10.0,
                        "market": 9.5,
                    }
                ],
                schema=df.schema,
            ),
        ]
    )
    out = rank_assets(df, today=D)
    assert out["product_id"].to_list() == [1, 2]
    assert out["spread"].to_list() == [0.1, float("inf")]


def test_zero_spread_sorts_last_not_first() -> None:
    """C1(b) regression: `low == mid` is no depth, not the tightest book.

    A zero relative spread used to score 0.0 and rank ahead of every real
    quote. At the first bar density saturates, so this key IS the screen, and
    it was selecting for illiquidity: product 91594 (`low == mid == 5000`,
    last print 19 months before the window ended) topped the screened sealed
    universe on exactly this and took half the benchmark account.
    """
    df = _rows(
        [
            (1, "Normal", D, 10.0, 10.0, 10.0),  # single listing: spread 0.0
            (2, "Normal", D, 9.0, 10.0, 9.5),  # a real book: spread 0.1
        ]
    )
    out = rank_assets(df, today=D)
    assert out["product_id"].to_list() == [2, 1]
    assert out["spread"].to_list() == [0.1, float("inf")]


def test_crossed_quote_sorts_last() -> None:
    """`low > mid` is incoherent (cheapest listing above the median one), and
    its negative spread used to rank BETTER than a zero one. Same bucket."""
    df = _rows(
        [
            (1, "Normal", D, 12.0, 10.0, 11.0),  # crossed: spread -0.2
            (2, "Normal", D, 9.0, 10.0, 9.5),
        ]
    )
    out = rank_assets(df, today=D)
    assert out["product_id"].to_list() == [2, 1]
    assert out["spread"].to_list() == [0.1, float("inf")]


def test_product_id_is_the_final_tie_break() -> None:
    """Identical density and spread must still order deterministically."""
    df = _rows([(7, "Normal", D, 9.0, 10.0, 9.5), (3, "Normal", D, 9.0, 10.0, 9.5)])
    assert rank_assets(df, today=D)["product_id"].to_list() == [3, 7]


def test_sub_types_rank_independently() -> None:
    """Marks are keyed by (product_id, sub_type), so the screen must be too."""
    df = _rows(
        [
            (1, "Normal", date(2025, 6, 1), 9.0, 10.0, 9.5),
            (1, "Normal", D, 9.0, 10.0, 9.5),
            (1, "Holofoil", D, 9.0, 10.0, 9.5),
        ]
    )
    out = rank_assets(df, today=D)
    assert out.select("product_id", "sub_type").rows() == [(1, "Normal"), (1, "Holofoil")]


def test_history_after_today_is_ignored() -> None:
    """rank_assets must never count a print the strategy cannot have seen."""
    df = _rows(
        [
            (1, "Normal", D, 9.0, 10.0, 9.5),
            (1, "Normal", date(2025, 6, 10), 9.0, 10.0, 9.5),
        ]
    )
    assert rank_assets(df, today=D)["n_prints"].to_list() == [1]


def test_spread_uses_the_latest_quote_at_or_before_today() -> None:
    """The quote read is the most recent print <= today, not the first one.

    The C++ port mirrors this with a binary search over the per-asset series;
    reading the wrong end of the series is a silent parity break.
    """
    df = _rows(
        [
            (1, "Normal", date(2025, 6, 1), 1.0, 10.0, 9.5),  # stale: spread 0.9
            (1, "Normal", D, 9.0, 10.0, 9.5),  # latest: spread 0.1
        ]
    )
    assert rank_assets(df, today=D)["spread"].to_list() == [0.1]
