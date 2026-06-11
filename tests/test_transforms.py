from datetime import date
from typing import Any

import polars as pl

from pkmn_quant.data.transforms import prices_frame, products_frame

PRODUCT_SINGLE: dict[str, Any] = {
    "productId": 666999,
    "name": "Mega Charizard X ex - 200/180",
    "groupId": 24541,
    "presaleInfo": {"isPresale": False, "releasedOn": "2026-02-20T00:00:00", "note": None},
    "extendedData": [
        {"name": "Number", "displayName": "Number", "value": "200/180"},
        {"name": "Rarity", "displayName": "Rarity", "value": "Special Illustration Rare"},
    ],
}

PRODUCT_SEALED: dict[str, Any] = {
    "productId": 666906,
    "name": "Mega Evolution: Ascended Heroes Collection - Erika",
    "groupId": 24541,
    "presaleInfo": {"isPresale": False, "releasedOn": None, "note": None},
    "extendedData": [],
}

PRODUCT_CODE_CARD: dict[str, Any] = {
    "productId": 667000,
    "name": "Mega Charizard X ex - Code Card",
    "groupId": 24541,
    "presaleInfo": {"isPresale": False, "releasedOn": None, "note": None},
    "extendedData": [{"name": "Rarity", "displayName": "Rarity", "value": "Code Card"}],
}


def test_products_frame() -> None:
    df = products_frame([PRODUCT_SINGLE, PRODUCT_SEALED, PRODUCT_CODE_CARD])
    assert df.height == 3
    single = df.filter(pl.col("product_id") == 666999)
    assert single["rarity"][0] == "Special Illustration Rare"
    assert single["kind"][0] == "single"
    assert single["released_on"][0] == date(2026, 2, 20)
    sealed = df.filter(pl.col("product_id") == 666906)
    assert sealed["rarity"][0] is None
    assert sealed["kind"][0] == "sealed"
    assert sealed["released_on"][0] is None
    code_card = df.filter(pl.col("product_id") == 667000)
    assert code_card["kind"][0] == "excluded"


def test_prices_frame() -> None:
    day = date(2025, 6, 1)
    rows_by_group = {
        24541: [
            {
                "productId": 666906,
                "lowPrice": 23.95,
                "midPrice": 32.81,
                "highPrice": 55.0,
                "marketPrice": 32.98,
                "directLowPrice": None,
                "subTypeName": "Normal",
            }
        ]
    }
    df = prices_frame(day, rows_by_group)
    assert df.height == 1
    row = df.row(0, named=True)
    assert row["date"] == day
    assert row["product_id"] == 666906
    assert row["sub_type"] == "Normal"
    assert row["market"] == 32.98


def test_prices_frame_flattens_multiple_groups() -> None:
    day = date(2025, 6, 1)
    row = {
        "productId": 1,
        "lowPrice": 1.0,
        "midPrice": 2.0,
        "highPrice": 3.0,
        "marketPrice": 2.5,
        "directLowPrice": None,
        "subTypeName": "Normal",
    }
    df = prices_frame(day, {24541: [row], 3170: [{**row, "productId": 2}]})
    assert df.height == 2
    assert sorted(df["product_id"].to_list()) == [1, 2]


def test_empty_frames_have_schema() -> None:
    df = prices_frame(date(2025, 6, 1), {})
    assert df.height == 0
    assert set(df.columns) == {"date", "product_id", "sub_type", "low", "mid", "high", "market"}


def test_empty_products_frame_has_schema() -> None:
    df = products_frame([])
    assert df.height == 0
    assert set(df.columns) == {"product_id", "group_id", "name", "rarity", "kind", "released_on"}
