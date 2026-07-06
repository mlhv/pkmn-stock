"""Parquet-backed price warehouse with DuckDB query access."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
import polars as pl

from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA


class Warehouse:
    """Date-partitioned Parquet storage: prices/date=YYYY-MM-DD/data.parquet."""

    def __init__(self, paths: Paths) -> None:
        self.paths = paths

    def _day_dir(self, day: date) -> Path:
        return self.paths.prices / f"date={day.isoformat()}"

    def has_day(self, day: date) -> bool:
        return (self._day_dir(day) / "data.parquet").exists()

    def write_prices(self, day: date, df: pl.DataFrame) -> None:
        day_dir = self._day_dir(day)
        day_dir.mkdir(parents=True, exist_ok=True)
        tmp = day_dir / "data.parquet.tmp"
        df.write_parquet(tmp)
        tmp.rename(day_dir / "data.parquet")

    def write_quarantine(self, day: date, df: pl.DataFrame) -> None:
        if df.height == 0:
            return
        day_dir = self.paths.quarantine / f"date={day.isoformat()}"
        day_dir.mkdir(parents=True, exist_ok=True)
        tmp = day_dir / "data.parquet.tmp"
        df.write_parquet(tmp)
        tmp.rename(day_dir / "data.parquet")

    def write_products(self, df: pl.DataFrame) -> None:
        self.paths.warehouse.mkdir(parents=True, exist_ok=True)
        tmp = self.paths.products.with_name(self.paths.products.name + ".tmp")
        df.write_parquet(tmp)
        tmp.rename(self.paths.products)

    def load_products(self) -> pl.DataFrame:
        return pl.read_parquet(self.paths.products)

    def load_day(self, day: date) -> pl.DataFrame:
        return pl.read_parquet(self._day_dir(day) / "data.parquet")

    def load_prices(self) -> pl.DataFrame:
        """All stored price days as one frame (the `date` column is in the data)."""
        if not self.paths.prices.exists():
            return pl.DataFrame(schema=PRICE_SCHEMA)
        return pl.read_parquet(self.paths.prices / "**" / "*.parquet")

    def stored_days(self) -> list[date]:
        if not self.paths.prices.exists():
            return []
        days = []
        for p in self.paths.prices.iterdir():
            if not p.name.startswith("date="):
                continue
            if not (p / "data.parquet").exists():  # crashed write left an empty dir
                continue
            try:
                days.append(date.fromisoformat(p.name.removeprefix("date=")))
            except ValueError:  # stray artifacts like date=tmp
                continue
        return sorted(days)

    def query(self, sql: str) -> pl.DataFrame:
        """Run DuckDB SQL with `prices` and `products` views available.

        Raises FileNotFoundError if no prices have been ingested yet;
        run `pkmn ingest` first.
        """
        if not self.paths.prices.exists():
            raise FileNotFoundError(
                f"No ingested prices under {self.paths.prices}; run `pkmn ingest` first."
            )
        prices_glob = str(self.paths.prices / "**" / "*.parquet")
        with duckdb.connect() as con:
            con.execute(f"CREATE VIEW prices AS SELECT * FROM read_parquet('{prices_glob}')")
            if self.paths.products.exists():
                con.execute(
                    f"CREATE VIEW products AS SELECT * FROM read_parquet('{self.paths.products}')"
                )
            result = pl.from_arrow(con.sql(sql))
        if not isinstance(result, pl.DataFrame):  # narrows DataFrame | Series for mypy
            raise TypeError("Expected a DataFrame from DuckDB query")
        return result
