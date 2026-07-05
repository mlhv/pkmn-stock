"""Read-only market data view the engine iterates day by day."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import polars as pl

from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.portfolio import Asset


@dataclass(frozen=True)
class MarketData:
    """Price history for [start, end], indexed for per-day access.

    Data lives in two Polars frames (columnar, compact); the per-day dicts
    returned by prices_on/marks_on are built on demand (~ms) and owned by
    the caller - mutating them cannot corrupt this view.

    marks (carry-forward) vs prices (actual prints): an asset missing on
    day D is MARKED at its most recent prior price, but execution must not
    FILL at stale prices, so prices_on has no carry-forward.

    warm-up: when from_warehouse is called with warmup_days > 0, the loaded
    frame and marks_compact cover [start - warmup_days, end] so that
    history_until and marks_on see pre-start data.  However, _days (the
    iteration index used by the event loop) contains ONLY days in [start, end],
    so no trading occurs during the warm-up period.
    """

    frame: pl.DataFrame  # actual prints in [warmup_start, end]
    _days: tuple[date, ...]  # trading days in [start, end] only
    _marks_compact: pl.DataFrame  # change-point rows sorted by (product_id, sub_type, date)

    @property
    def days(self) -> list[date]:
        return list(self._days)

    @classmethod
    def from_warehouse(
        cls,
        warehouse: Warehouse,
        start: date,
        end: date,
        warmup_days: int = 0,
    ) -> MarketData:
        """Load prices from ``start - warmup_days`` through ``end``.

        ``days`` (the event-loop iteration index) is restricted to [start, end]
        so no trading occurs during the warm-up period.  ``history_until`` and
        ``marks_on`` naturally see the warm-up rows because the underlying frame
        covers the full [start - warmup_days, end] range.

        ``warmup_days=0`` (the default) preserves the original behaviour exactly.
        """
        load_from = start - timedelta(days=warmup_days) if warmup_days > 0 else start
        frame = warehouse.load_prices().filter(
            (pl.col("date") >= load_from) & (pl.col("date") <= end)
        )
        # Trading days: only those within [start, end].  Warm-up rows are present
        # in frame (and marks_compact) for look-back access but are NOT iterated.
        all_dates = frame["date"].unique().to_list()
        days = tuple(sorted(d for d in all_dates if d >= start))
        # Compact marks source: one row per (asset, price-change-point), sorted for
        # efficient forward-fill lookup. Rows where market repeats are dropped so
        # the frame stays small; group_by+last in marks_on still finds the correct
        # most-recent price for any query date.  Built from the full frame so
        # warm-up carry-forward marks are available on the first trading day.
        marks_compact = (
            frame.select("date", "product_id", "sub_type", "market")
            .lazy()
            .sort(["product_id", "sub_type", "date"])
            .with_columns(pl.col("market").shift(1).over(["product_id", "sub_type"]).alias("_prev"))
            .filter(pl.col("_prev").is_null() | (pl.col("market") != pl.col("_prev")))
            .drop("_prev")
            .collect()
        )
        return cls(frame=frame, _days=days, _marks_compact=marks_compact)

    def _day_dict(self, source: pl.DataFrame, day: date) -> dict[Asset, float]:
        rows = source.filter(pl.col("date") == day)
        return {
            Asset(product_id=int(r["product_id"]), sub_type=str(r["sub_type"])): float(r["market"])
            for r in rows.iter_rows(named=True)
        }

    def prices_on(self, day: date) -> dict[Asset, float]:
        """Prices that actually printed on `day` (no carry-forward)."""
        return self._day_dict(self.frame, day)

    def marks_on(self, day: date) -> dict[Asset, float]:
        """Mark-to-market prices on `day`, carrying forward missing assets."""
        rows = (
            self._marks_compact.filter(pl.col("date") <= day)
            .group_by(["product_id", "sub_type"])
            .agg(pl.col("market").sort_by(pl.col("date")).last())
        )
        return {
            Asset(product_id=int(r["product_id"]), sub_type=str(r["sub_type"])): float(r["market"])
            for r in rows.iter_rows(named=True)
        }

    def history_until(self, day: date) -> pl.DataFrame:
        """All price rows with date <= day. The engine's anti-look-ahead wall."""
        return self.frame.filter(pl.col("date") <= day)
