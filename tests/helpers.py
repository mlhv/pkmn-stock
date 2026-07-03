"""Shared test fixtures."""

from datetime import date


def price_row(day: date, product_id: int, market: float) -> dict[str, object]:
    return {
        "date": day,
        "product_id": product_id,
        "sub_type": "Normal",
        "low": 1.0,
        "mid": 2.0,
        "high": 3.0,
        "market": market,
    }
