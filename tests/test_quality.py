from datetime import date
from typing import Any

import polars as pl

from pkmn_quant.data.quality import apply_quality_gates
from pkmn_quant.data.transforms import PRICE_SCHEMA

DAY = date(2025, 6, 2)
PREV_DAY = date(2025, 6, 1)


def frame(rows: list[dict[str, Any]]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=PRICE_SCHEMA)


def row(
    product_id: int, market: float | None, day: date = DAY, sub_type: str = "Normal"
) -> dict[str, Any]:
    return {
        "date": day,
        "product_id": product_id,
        "sub_type": sub_type,
        "low": 1.0,
        "mid": 2.0,
        "high": 3.0,
        "market": market,
    }


def test_clean_rows_pass_through() -> None:
    clean, quarantined = apply_quality_gates(frame([row(1, 10.0)]), previous=None)
    assert clean.height == 1
    assert quarantined.height == 0


def test_null_and_nonpositive_market_quarantined() -> None:
    clean, quarantined = apply_quality_gates(
        frame([row(1, None), row(2, 0.0), row(3, 5.0)]), previous=None
    )
    assert clean["product_id"].to_list() == [3]
    reasons = dict(
        zip(quarantined["product_id"].to_list(), quarantined["reason"].to_list(), strict=True)
    )
    assert reasons == {1: "null_market", 2: "nonpositive_market"}


def test_duplicates_quarantined() -> None:
    clean, quarantined = apply_quality_gates(frame([row(1, 10.0), row(1, 11.0)]), previous=None)
    assert clean.height == 0
    assert quarantined["reason"].to_list() == ["duplicate", "duplicate"]


def test_same_product_different_subtype_not_duplicate() -> None:
    clean, _ = apply_quality_gates(
        frame([row(1, 10.0, sub_type="Normal"), row(1, 12.0, sub_type="Holofoil")]),
        previous=None,
    )
    assert clean.height == 2


def test_price_jump_quarantined() -> None:
    previous = frame([row(1, 10.0, day=PREV_DAY), row(2, 10.0, day=PREV_DAY)])
    clean, quarantined = apply_quality_gates(
        frame([row(1, 150.0), row(2, 11.0)]), previous=previous
    )
    assert clean["product_id"].to_list() == [2]
    assert quarantined["reason"].to_list() == ["price_jump"]


def test_price_crash_quarantined() -> None:
    previous = frame([row(1, 10.0, day=PREV_DAY)])
    clean, quarantined = apply_quality_gates(
        frame([row(1, 0.5)]),  # ratio 0.05: a 20x crash
        previous=previous,
    )
    assert clean.height == 0
    assert quarantined["reason"].to_list() == ["price_jump"]


def test_new_product_without_history_passes() -> None:
    previous = frame([row(1, 10.0, day=PREV_DAY)])
    clean, quarantined = apply_quality_gates(frame([row(99, 500.0)]), previous=previous)
    assert clean.height == 1
    assert quarantined.height == 0
