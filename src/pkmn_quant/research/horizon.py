"""Horizon-matched round-trip evaluation against the sealed index.

Every result elsewhere in this project compares a strategy's stitched
return against one 28-month buy-and-hold. That measures exposure time, not
skill, for a strategy meant to enter and exit within 1-3 months. This module
instead pairs each buy with the sell(s) that close it (FIFO) and compares
that trip's own return against holding the sealed index over the trip's own
entry and exit dates — the index series is just the benchmark run's
``equity.parquet``, read as-is.

Pure Python: no engine changes, no C++, no parity concerns. It reads run
artifacts (a fills frame and an index/equity frame) after the fact, plus the
run's CostModel so a still-open lot is scored net of a modelled exit rather
than gross against closed trips that already paid one.
"""

from __future__ import annotations

import statistics
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

import polars as pl

from pkmn_quant.engine.costs import CostModel

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

    Returns one tuple per trip::

        (product_id, sub_type, entry, exit, holding_days, quantity,
         basis_per_unit, proceeds_per_unit, still_open)

    ``basis_per_unit`` and ``proceeds_per_unit`` are per-unit rather than
    per-trip so callers get ``strategy_return`` without re-deriving the cost
    apportionment.

    Lots still open at the end of the frame ARE included, flagged
    ``still_open=True`` with ``exit == entry``, ``holding_days == 0`` and
    ``proceeds_per_unit == 0.0`` — placeholders the caller must replace,
    which ``horizon_report`` does from ``final_marks`` net of a modelled
    exit. Nothing here is a usable return on its own.
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
    costs: CostModel,
    final_lows: dict[tuple[int, str], float] | None = None,
    exit_impact: Callable[[float, float | None, int], float] | None = None,
) -> HorizonReport:
    """Pair round trips from ``fills`` and score each against the sealed
    index over that trip's own entry/exit window.

    ``index`` is a ``date``/``equity`` frame — the benchmark run's
    ``equity.parquet`` as-is. ``final_marks`` supplies the exit price for
    any lot still open at ``last_day``, keyed ``(product_id, sub_type)``;
    a still-open lot with no entry there raises rather than fabricating a
    0% return.

    ``costs`` prices that hypothetical exit, and is required rather than
    defaulted because getting it wrong is silent. A closed trip's proceeds
    are already net — the engine charged ~12.75% seller fees, shipping and
    impact, and they are in the fills frame — so scoring an open lot at the
    raw mark flattered it by roughly 13 points against every closed trip
    beside it. That is not a rounding concern: it lands on the two least-bad
    strategies hardest (10 of sealed-accumulation's 33 trips, 4 of
    cost-aware-reversion's 41 in-band), i.e. exactly where a spurious edge
    would matter. Pass the same CostModel the run used;
    ``CostModel(impact_enabled=run.config["impact"])`` reproduces it, since
    `run_single_backtest` builds it that way and never varies the other
    knobs.

    ``final_lows`` supplies ``last_day``'s cheapest-listing quote per asset so
    the modelled exit can carry sell-side impact too; omit it and only fees
    and shipping are charged.

    ``exit_impact(mark, low, qty)`` overrides how that exit's sell-side impact
    is priced; ``None`` keeps ``costs.sell_impact``. A book run passes the
    book-priced exit (``engine.native.book_sell_impact``) so its open lots are
    scored under the same execution mode as its closed ones.

    Two honest limits of the modelled exit, neither of which the old gross
    treatment avoided — it just also omitted the fees:

    * It ignores the per-day liquidity cap, so a large lot is charged as one
      same-day sale rather than the several days it would really take.
    * It is a mark, not a fill. The standing mark-smoothing caveat applies:
      a still-open trip's return is what the last print says, not what a
      buyer paid.
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
            mark = float(final_marks[key])
            # Model the exit the same way the engine charges a real one
            # (execution.py _fill_sell): fees on the proceeds, one shipping
            # charge on the line, and the walk down toward the cheapest
            # listing when that quote is available. Identical shape to the
            # closed-trip line above, so open and closed trips are on one
            # scale.
            exit_fees = quantity * mark * costs.fee_rate + costs.shipping_per_line
            low = None if final_lows is None else final_lows.get(key)
            exit_cost = (
                costs.sell_impact(mark, low, quantity)
                if exit_impact is None
                else exit_impact(mark, low, quantity)
            )
            proceeds_per_unit = mark - (exit_fees + exit_cost) / quantity
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
