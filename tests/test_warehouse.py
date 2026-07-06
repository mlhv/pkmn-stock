from datetime import date
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA
from pkmn_quant.data.warehouse import Warehouse


@pytest.fixture
def warehouse(tmp_path: Path) -> Warehouse:
    return Warehouse(Paths(root=tmp_path))


def price_row(day: date, product_id: int, market: float) -> dict[str, Any]:
    return {
        "date": day,
        "product_id": product_id,
        "sub_type": "Normal",
        "low": 1.0,
        "mid": 2.0,
        "high": 3.0,
        "market": market,
    }


def test_write_and_load_day(warehouse: Warehouse) -> None:
    day = date(2025, 6, 1)
    df = pl.DataFrame([price_row(day, 1, 10.0)], schema=PRICE_SCHEMA)
    assert not warehouse.has_day(day)
    warehouse.write_prices(day, df)
    assert warehouse.has_day(day)
    assert warehouse.load_day(day).equals(df)


def test_stored_days_sorted(warehouse: Warehouse) -> None:
    d1, d2 = date(2025, 6, 1), date(2025, 6, 2)
    df = pl.DataFrame([price_row(d2, 1, 10.0)], schema=PRICE_SCHEMA)
    warehouse.write_prices(d2, df)
    warehouse.write_prices(d1, df.with_columns(pl.lit(d1).alias("date")))
    assert warehouse.stored_days() == [d1, d2]


def test_empty_quarantine_not_written(warehouse: Warehouse) -> None:
    day = date(2025, 6, 1)
    empty = pl.DataFrame([], schema={**PRICE_SCHEMA, "reason": pl.Utf8})
    warehouse.write_quarantine(day, empty)
    assert not (warehouse.paths.quarantine / f"date={day.isoformat()}").exists()


def test_load_prices_concats_all_days(warehouse: Warehouse) -> None:
    d1, d2 = date(2025, 6, 1), date(2025, 6, 2)
    warehouse.write_prices(d1, pl.DataFrame([price_row(d1, 1, 10.0)], schema=PRICE_SCHEMA))
    warehouse.write_prices(d2, pl.DataFrame([price_row(d2, 1, 11.0)], schema=PRICE_SCHEMA))
    df = warehouse.load_prices()
    assert df.height == 2
    assert sorted(df["date"].to_list()) == [d1, d2]


def test_load_prices_empty_warehouse_returns_typed_empty_frame(warehouse: Warehouse) -> None:
    df = warehouse.load_prices()
    assert df.height == 0
    assert df.schema == PRICE_SCHEMA


def test_query_before_ingest_raises_clear_error(warehouse: Warehouse) -> None:
    with pytest.raises(FileNotFoundError, match="pkmn ingest"):
        warehouse.query("SELECT 1")


def test_stored_days_ignores_stray_dirs(warehouse: Warehouse) -> None:
    day = date(2025, 6, 1)
    warehouse.write_prices(day, pl.DataFrame([price_row(day, 1, 10.0)], schema=PRICE_SCHEMA))
    (warehouse.paths.prices / "date=tmp").mkdir()
    assert warehouse.stored_days() == [day]


def test_stored_days_ignores_empty_partition_dir(warehouse: Warehouse) -> None:
    """A crash between mkdir and the atomic tmp-rename in write_prices leaves a
    partition dir with no data.parquet; stored_days must not report that day."""
    day = date(2025, 6, 1)
    warehouse.write_prices(day, pl.DataFrame([price_row(day, 1, 10.0)], schema=PRICE_SCHEMA))
    (warehouse.paths.prices / "date=2025-06-02").mkdir()
    assert warehouse.stored_days() == [day]


def test_duckdb_query_over_prices_and_products(warehouse: Warehouse) -> None:
    day = date(2025, 6, 1)
    prices = pl.DataFrame([price_row(day, 1, 10.0), price_row(day, 2, 99.0)], schema=PRICE_SCHEMA)
    warehouse.write_prices(day, prices)
    products = pl.DataFrame(
        {
            "product_id": [1, 2],
            "group_id": [24541, 24541],
            "name": ["Card A", "Booster Box"],
            "rarity": ["Common", None],
            "kind": ["single", "sealed"],
            "released_on": [day, day],
        }
    )
    warehouse.write_products(products)
    out = warehouse.query(
        "SELECT p.kind, COUNT(*) AS n FROM prices pr "
        "JOIN products p USING (product_id) GROUP BY p.kind ORDER BY p.kind"
    )
    assert out["kind"].to_list() == ["sealed", "single"]
    assert out["n"].to_list() == [1, 1]


def test_stored_days_ignores_non_date_dirs(warehouse: Warehouse) -> None:
    day = date(2025, 6, 1)
    warehouse.write_prices(day, pl.DataFrame([price_row(day, 1, 10.0)], schema=PRICE_SCHEMA))
    (warehouse.paths.prices / "not-a-partition").mkdir()
    assert warehouse.stored_days() == [day]
