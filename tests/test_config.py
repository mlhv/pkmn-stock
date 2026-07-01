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
