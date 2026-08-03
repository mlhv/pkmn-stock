from datetime import date
from pathlib import Path

from pkmn_quant.config import EARLIEST_ARCHIVE_DATE, POKEMON_CATEGORY_ID, Paths


def test_constants() -> None:
    assert POKEMON_CATEGORY_ID == 3
    assert date(2024, 2, 8) == EARLIEST_ARCHIVE_DATE


def test_paths_layout() -> None:
    paths = Paths(root=Path("/tmp/proj"))
    assert paths.raw_archives == Path("/tmp/proj/data/raw/archives")
    assert paths.warehouse == Path("/tmp/proj/data/warehouse")
    assert paths.prices == Path("/tmp/proj/data/warehouse/prices")
    assert paths.quarantine == Path("/tmp/proj/data/warehouse/quarantine")
    assert paths.products == Path("/tmp/proj/data/warehouse/products.parquet")


def test_paths_shadow_warehouse() -> None:
    """A non-default warehouse name relocates the warehouse and everything
    under it, while archives stay rooted at the project — that split is what
    lets a shadow rebuild reuse the cached archives."""
    paths = Paths(root=Path("/tmp/proj"), warehouse_name="warehouse-full")
    assert paths.warehouse == Path("/tmp/proj/data/warehouse-full")
    assert paths.prices == Path("/tmp/proj/data/warehouse-full/prices")
    assert paths.quarantine == Path("/tmp/proj/data/warehouse-full/quarantine")
    assert paths.products == Path("/tmp/proj/data/warehouse-full/products.parquet")
    assert paths.raw_archives == Path("/tmp/proj/data/raw/archives")


def test_paths_default_warehouse_name_is_unchanged() -> None:
    """Every existing caller constructs Paths(root=...) positionally or by
    keyword with no warehouse_name; the default must keep them identical."""
    assert Paths(root=Path("/tmp/proj")).warehouse == Path("/tmp/proj/data/warehouse")
    assert Paths(root=Path("/tmp/proj")) == Paths(
        root=Path("/tmp/proj"), warehouse_name="warehouse"
    )
