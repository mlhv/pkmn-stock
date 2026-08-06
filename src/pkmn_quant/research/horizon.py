"""Horizon-matched round-trip evaluation against the sealed index.

Every result elsewhere in this project compares a strategy's stitched
return against one 28-month buy-and-hold. That measures exposure time, not
skill, for a strategy meant to enter and exit within 1-3 months. This module
instead pairs each buy with the sell(s) that close it (FIFO) and compares
that trip's own return against holding the sealed index over the trip's own
entry and exit dates — the index series is just the benchmark run's
``equity.parquet``, read as-is.

Pure Python: no engine changes, no C++, no parity concerns. It reads run
artifacts (a fills frame and an index/equity frame) after the fact.
"""

from __future__ import annotations

import statistics
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

import polars as pl

_BUCKET_LT_1M = "<1m"
_BUCKET_1_3M = "1-3m"
_BUCKET_3_6M = "3-6m"
_BUCKET_GT_6M = ">6m"
_BUCKET_ORDER = (_BUCKET_LT_1M, _BUCKET_1_3M, _BUCKET_3_6M, _BUCKET_GT_6M)


def _bucket_for(holding_days: int) -> str:
    if holding_days < 30:
        return _BUCKET_LT_1M
    if holding_days < 90:
        return _BUCKET_1_3M
    if holding_days < 180:
        return _BUCKET_3_6M
    return _BUCKET_GT_6M


@dataclass(frozen=True)
class RoundTrip:
    product_id: int
    sub_type: str
    entry: date
    exit: date
    holding_days: int
    quantity: int
    strategy_return: float
    index_return: float
    edge: float
    still_open: bool


@dataclass(frozen=True)
class HorizonReport:
    trips: list[RoundTrip]
    median_edge: float
    mean_edge: float
    win_rate: float
    holding_buckets: dict[str, int]
    edge_by_bucket: dict[str, float]


@dataclass
class _Lot:
    entry: date
    quantity: int
    basis_per_unit: float


def _pair_key(row: Mapping[str, Any]) -> tuple[int, str]:
    return int(row["product_id"]), str(row["sub_type"])


def pair_round_trips(
    fills: pl.DataFrame,
) -> list[tuple[int, str, date, date, int, int, float, float, bool]]:
    """FIFO-pair buys and sells per ``(product_id, sub_type)``.

    Returns one tuple per closed round trip: ``(product_id, sub_type, entry,
    exit, holding_days, quantity, strategy_return_numerator_basis,
    strategy_return_numerator_proceeds, still_open)`` — actually returns the
    per-unit basis and proceeds so callers can compute strategy_return
    without re-deriving cost apportionment. Open lots at the end of the
    frame are NOT included here; the caller closes them against
    ``final_marks``.
    """
    ordered = fills.sort("day", maintain_order=True)
    open_lots: dict[tuple[int, str], deque[_Lot]] = {}
    trips: list[tuple[int, str, date, date, int, int, float, float, bool]] = []

    for row in ordered.iter_rows(named=True):
        key = _pair_key(row)
        quantity = int(row["quantity"])
        price = float(row["price"])
        fees = float(row["fees"])
        impact = float(row["impact"])
        day: date = row["day"]

        if quantity > 0:
            basis_per_unit = price + (fees + impact) / quantity
            open_lots.setdefault(key, deque()).append(_Lot(day, quantity, basis_per_unit))
            continue

        if quantity == 0:
            continue

        sell_qty = -quantity
        proceeds_per_unit = price - (fees + impact) / sell_qty
        lots = open_lots.get(key)
        remaining = sell_qty
        while remaining > 0:
            if not lots:
                raise ValueError(
                    f"no matching buy for sell of {sell_qty} "
                    f"product_id={key[0]} sub_type={key[1]!r} on {day}"
                )
            lot = lots[0]
            take = min(lot.quantity, remaining)
            trips.append(
                (
                    key[0],
                    key[1],
                    lot.entry,
                    day,
                    (day - lot.entry).days,
                    take,
                    lot.basis_per_unit,
                    proceeds_per_unit,
                    False,
                )
            )
            lot.quantity -= take
            remaining -= take
            if lot.quantity == 0:
                lots.popleft()

    for key, lots in open_lots.items():
        for lot in lots:
            if lot.quantity > 0:
                trips.append(
                    (
                        key[0],
                        key[1],
                        lot.entry,
                        lot.entry,
                        0,
                        lot.quantity,
                        lot.basis_per_unit,
                        0.0,
                        True,
                    )
                )

    return trips


def _index_at(index_sorted: pl.DataFrame, d: date) -> float:
    dates = index_sorted["date"]
    if dates[0] > d:
        raise ValueError(f"index has no data on or before {d} (first index date is {dates[0]})")
    # Rightmost date <= d.
    match = index_sorted.filter(pl.col("date") <= d)
    return float(match["equity"][-1])


def horizon_report(
    fills: pl.DataFrame,
    index: pl.DataFrame,
    final_marks: dict[tuple[int, str], float],
    last_day: date,
) -> HorizonReport:
    """Pair round trips from ``fills`` and score each against the sealed
    index over that trip's own entry/exit window.

    ``index`` is a ``date``/``equity`` frame — the benchmark run's
    ``equity.parquet`` as-is. ``final_marks`` supplies the exit price for
    any lot still open at ``last_day``, keyed ``(product_id, sub_type)``;
    a still-open lot with no entry there raises rather than fabricating a
    0% return.
    """
    index_sorted = index.sort("date")
    raw_trips = pair_round_trips(fills)

    trips: list[RoundTrip] = []
    for (
        product_id,
        sub_type,
        entry,
        exit_date,
        holding_days,
        quantity,
        basis_per_unit,
        proceeds_per_unit,
        still_open,
    ) in raw_trips:
        if still_open:
            key = (product_id, sub_type)
            if key not in final_marks:
                raise ValueError(
                    f"no final mark for still-open position product_id={product_id} "
                    f"sub_type={sub_type!r}"
                )
            proceeds_per_unit = float(final_marks[key])
            exit_date = last_day
            holding_days = (last_day - entry).days

        strategy_return = proceeds_per_unit / basis_per_unit - 1.0
        index_return = _index_at(index_sorted, exit_date) / _index_at(index_sorted, entry) - 1.0
        edge = strategy_return - index_return

        trips.append(
            RoundTrip(
                product_id=product_id,
                sub_type=sub_type,
                entry=entry,
                exit=exit_date,
                holding_days=holding_days,
                quantity=quantity,
                strategy_return=strategy_return,
                index_return=index_return,
                edge=edge,
                still_open=still_open,
            )
        )

    edges = [t.edge for t in trips]
    median_edge = statistics.median(edges) if edges else 0.0
    mean_edge = statistics.mean(edges) if edges else 0.0
    win_rate = sum(1 for e in edges if e > 0) / len(edges) if edges else 0.0

    holding_buckets: dict[str, int] = {}
    bucket_edges: dict[str, list[float]] = {}
    for t in trips:
        bucket = _bucket_for(t.holding_days)
        holding_buckets[bucket] = holding_buckets.get(bucket, 0) + 1
        bucket_edges.setdefault(bucket, []).append(t.edge)

    edge_by_bucket = {
        bucket: statistics.median(bucket_edges[bucket])
        for bucket in _BUCKET_ORDER
        if bucket in bucket_edges
    }

    return HorizonReport(
        trips=trips,
        median_edge=median_edge,
        mean_edge=mean_edge,
        win_rate=win_rate,
        holding_buckets=holding_buckets,
        edge_by_bucket=edge_by_bucket,
    )
