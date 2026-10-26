"""Project-wide constants and filesystem layout."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

POKEMON_CATEGORY_ID = 3
TCGCSV_BASE_URL = "https://tcgcsv.com"
# tcgcsv daily price archives exist from this date onward.
EARLIEST_ARCHIVE_DATE = date(2024, 2, 8)


@dataclass(frozen=True)
class Paths:
    """Filesystem layout rooted at the project directory.

    `warehouse_name` exists so a full-set rebuild can write to a shadow
    warehouse (`data/warehouse-full/`) while reading the SAME cached
    archives — `raw_archives` stays derived from `root`, so nothing is
    re-downloaded and the live warehouse is never written.
    """

    root: Path
    warehouse_name: str = "warehouse"

    @property
    def raw_archives(self) -> Path:
        return self.root / "data" / "raw" / "archives"

    @property
    def raw_live(self) -> Path:
        return self.root / "data" / "raw" / "live"

    @property
    def warehouse(self) -> Path:
        return self.root / "data" / self.warehouse_name

    @property
    def prices(self) -> Path:
        return self.warehouse / "prices"

    @property
    def quarantine(self) -> Path:
        return self.warehouse / "quarantine"

    @property
    def products(self) -> Path:
        return self.warehouse / "products.parquet"
