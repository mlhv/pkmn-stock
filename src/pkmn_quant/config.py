"""Project-wide constants and filesystem layout."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

POKEMON_CATEGORY_ID = 3
TCGCSV_BASE_URL = "https://tcgcsv.com"
# tcgcsv daily price archives exist from this date onward.
EARLIEST_ARCHIVE_DATE = date(2024, 2, 8)
# Sets released on/after this date form the tradeable universe.
MIN_SET_RELEASE = date(2024, 1, 1)


@dataclass(frozen=True)
class Paths:
    """Filesystem layout rooted at the project directory."""

    root: Path

    @property
    def raw_archives(self) -> Path:
        return self.root / "data" / "raw" / "archives"

    @property
    def warehouse(self) -> Path:
        return self.root / "data" / "warehouse"

    @property
    def prices(self) -> Path:
        return self.warehouse / "prices"

    @property
    def quarantine(self) -> Path:
        return self.warehouse / "quarantine"

    @property
    def products(self) -> Path:
        return self.warehouse / "products.parquet"
