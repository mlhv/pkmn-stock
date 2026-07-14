"""Read-only market data view the engine iterates day by day."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TypedDict

import polars as pl

from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.portfolio import Asset
from pkmn_quant.engine.quotes import Quote


class _Cursor(TypedDict):
    idx: int
    watermark: date | None
    marks: dict[Asset, float]


@dataclass(frozen=True)
class MarketData:
    """Price history for [start, end], indexed for per-day access.

    Data lives in two structures: a one-time date partition for prices_on
    (O(1) lookup per day) and an incremental change-point cursor for marks_on
    (O(new change-points) per monotone step, O(total change-points) total
    over the event loop vs O(days x assets) for the old group_by approach).

    Returned dicts from prices_on/marks_on are caller-owned copies — mutating
    them cannot corrupt this view.

    marks (carry-forward) vs prices (actual prints): an asset missing on
    day D is MARKED at its most recent prior price, but execution must not
    FILL at stale prices, so prices_on has no carry-forward.

    warm-up: when from_warehouse is called with warmup_days > 0, the loaded
    frame and marks rows cover [start - warmup_days, end] so that
    history_until and marks_on see pre-start data.  However, _days (the
    iteration index used by the event loop) contains ONLY days in [start, end],
    so no trading occurs during the warm-up period.
    """

    frame: pl.DataFrame  # actual prints in [warmup_start, end]
    _days: tuple[date, ...]  # trading days in [start, end] only
    _marks_rows: list[tuple[date, Asset, float]]  # change-points, date-sorted
    _frame_by_day: dict[date, pl.DataFrame]  # frame partitioned once by date
    # marks_on cursor: mutated in place (frozen blocks rebinding, not dict
    # content mutation). idx = next _marks_rows row to apply; marks = running
    # carry-forward state as of watermark. Monotone queries (the event loop)
    # advance in O(new change-points); an earlier query resets and replays.
    _cursor: _Cursor
    # date -> (date, product_id, sub_type, mid, low) frame; built eagerly in
    # from_warehouse (~0.08s, measured and accepted). quotes_on's per-day/
    # per-asset lookup is what's lazy/order-gated — the hot prices_on/marks_on
    # paths (Plan 8 perf) never pay for that lookup unless there are orders.
    _quotes_by_day: dict[date, pl.DataFrame] = field(default_factory=dict)

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
        # in frame (and marks rows) for look-back access but are NOT iterated.
        all_dates = frame["date"].unique().to_list()
        days = tuple(sorted(d for d in all_dates if d >= start))
        # Compact marks source: one row per (asset, price-change-point), sorted for
        # efficient forward-fill lookup. Rows where market repeats are dropped so
        # the list stays small; the cursor advances to the correct most-recent price
        # for any query date.  Built from the full frame so warm-up carry-forward
        # marks are available on the first trading day.
        marks_compact = (
            frame.select("date", "product_id", "sub_type", "market")
            .lazy()
            .sort(["product_id", "sub_type", "date"])
            .with_columns(pl.col("market").shift(1).over(["product_id", "sub_type"]).alias("_prev"))
            .filter(pl.col("_prev").is_null() | (pl.col("market") != pl.col("_prev")))
            .drop("_prev")
            .collect()
        )
        # Date-only sort is sufficient: (asset, date) pairs are unique (one
        # warehouse row per date/product/sub_type), and the cursor is
        # last-write-wins per asset, so within-day order cannot matter.
        marks_rows: list[tuple[date, Asset, float]] = [
            (d, Asset(product_id=int(pid), sub_type=str(st)), float(m))
            for d, pid, st, m in marks_compact.sort("date")
            .select("date", "product_id", "sub_type", "market")
            .iter_rows()
        ]
        frame_by_day_raw = (
            frame.select("date", "product_id", "sub_type", "market").partition_by(
                "date", as_dict=True, include_key=True
            )
            if frame.height
            else {}
        )
        frame_by_day: dict[date, pl.DataFrame] = {k[0]: v for k, v in frame_by_day_raw.items()}
        quotes_by_day_raw = (
            frame.select("date", "product_id", "sub_type", "mid", "low").partition_by(
                "date", as_dict=True, include_key=True
            )
            if frame.height
            else {}
        )
        quotes_by_day: dict[date, pl.DataFrame] = {k[0]: v for k, v in quotes_by_day_raw.items()}
        cursor: _Cursor = {"idx": 0, "watermark": None, "marks": {}}
        return cls(
            frame=frame,
            _days=days,
            _marks_rows=marks_rows,
            _frame_by_day=frame_by_day,
            _cursor=cursor,
            _quotes_by_day=quotes_by_day,
        )

    def prices_on(self, day: date) -> dict[Asset, float]:
        """Prices that actually printed on `day` (no carry-forward)."""
        part = self._frame_by_day.get(day)
        if part is None:
            return {}
        return {
            Asset(product_id=int(pid), sub_type=str(st)): float(m)
            for _, pid, st, m in part.iter_rows()
        }

    def quotes_on(self, day: date, assets: Collection[Asset]) -> dict[Asset, Quote]:
        """mid/low actually printed on `day`, for the requested assets only.

        No carry-forward (same rule as prices_on): a stale quote must not
        price today's impact. Assets that did not print get no entry.
        """
        part = self._quotes_by_day.get(day)
        if part is None or not assets:
            return {}
        wanted = set(assets)
        out: dict[Asset, Quote] = {}
        for _, pid, st, mid, low in part.iter_rows():
            asset = Asset(product_id=int(pid), sub_type=str(st))
            if asset in wanted:
                out[asset] = Quote(
                    mid=float(mid) if mid is not None else None,
                    low=float(low) if low is not None else None,
                )
                if len(out) == len(wanted):
                    break
        return out

    def marks_on(self, day: date) -> dict[Asset, float]:
        """Mark-to-market prices on `day`, carrying forward missing assets."""
        cur = self._cursor
        watermark = cur["watermark"]
        if watermark is not None and day < watermark:  # rare: replay from scratch
            cur["idx"], cur["marks"] = 0, {}
        idx = cur["idx"]
        marks: dict[Asset, float] = cur["marks"]
        rows = self._marks_rows
        while idx < len(rows) and rows[idx][0] <= day:
            marks[rows[idx][1]] = rows[idx][2]
            idx += 1
        cur["idx"], cur["watermark"] = idx, day
        return dict(marks)  # caller owns the copy

    def history_until(self, day: date) -> pl.DataFrame:
        """All price rows with date <= day. The engine's anti-look-ahead wall."""
        return self.frame.filter(pl.col("date") <= day)
