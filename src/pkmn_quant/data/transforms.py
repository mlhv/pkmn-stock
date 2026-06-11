"""Pure transforms from tcgcsv JSON payloads to warehouse tables."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import polars as pl

from pkmn_quant.data.classify import classify_kind

PRICE_SCHEMA: pl.Schema = pl.Schema(
    {
        "date": pl.Date,
        "product_id": pl.Int64,
        "sub_type": pl.Utf8,
        "low": pl.Float64,
        "mid": pl.Float64,
        "high": pl.Float64,
        "market": pl.Float64,
    }
)

PRODUCT_SCHEMA: pl.Schema = pl.Schema(
    {
        "product_id": pl.Int64,
        "group_id": pl.Int64,
        "name": pl.Utf8,
        "rarity": pl.Utf8,
        "kind": pl.Utf8,
        "released_on": pl.Date,
    }
)


def prices_frame(day: date, rows_by_group: dict[int, list[dict[str, Any]]]) -> pl.DataFrame:
    records = [
        {
            "date": day,
            "product_id": row["productId"],
            "sub_type": row["subTypeName"],
            "low": row["lowPrice"],
            "mid": row["midPrice"],
            "high": row["highPrice"],
            "market": row["marketPrice"],
        }
        for rows in rows_by_group.values()
        for row in rows
    ]
    return pl.DataFrame(records, schema=PRICE_SCHEMA)


def _rarity(product: dict[str, Any]) -> str | None:
    for item in product.get("extendedData") or []:
        if item["name"] == "Rarity":
            # .get(): a malformed entry without "value" classifies as sealed
            # rather than crashing the whole day's ingest.
            value: str | None = item.get("value")
            return value
    return None


def _released_on(product: dict[str, Any]) -> date | None:
    raw = (product.get("presaleInfo") or {}).get("releasedOn")
    if raw is None:
        return None
    return datetime.fromisoformat(raw).date()


def products_frame(products: list[dict[str, Any]]) -> pl.DataFrame:
    records = []
    for p in products:
        rarity = _rarity(p)
        records.append(
            {
                "product_id": p["productId"],
                "group_id": p["groupId"],
                "name": p["name"],
                "rarity": rarity,
                "kind": classify_kind(rarity),
                "released_on": _released_on(p),
            }
        )
    return pl.DataFrame(records, schema=PRODUCT_SCHEMA)
