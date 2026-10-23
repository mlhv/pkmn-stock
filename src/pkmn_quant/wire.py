"""Warehouse -> Pokemon quote wire file (PKW1, docs/pokemon-wire-protocol.md).

The wire carries one daily quote per instrument in whole cents; the receiver
builds the modelled resting orders (synthetic, built from the day's tcgcsv
quote) with the backtester's own ladder builder. That only reproduces the
backtester's books if every warehouse price survives the trip to cents and
back (``cents / 100.0``) as the same double, so the encoder refuses any price
that is not a whole number of cents, or that would collide with the null
sentinel, instead of writing a silently rounded file.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import polars as pl
from numpy.typing import NDArray

from pkmn_quant import _engine
from pkmn_quant.data.warehouse import Warehouse

NULL_CENTS = 0xFFFFFFFF
_PRICE_COLUMNS = ("market", "low", "mid", "high")


def to_cents(values: pl.Series, *, what: str, frame: pl.DataFrame) -> NDArray[np.uint32]:
    """Prices in dollars -> u32 cents; null -> ``NULL_CENTS``.

    ``frame`` is row-aligned with ``values`` and supplies the identifiers for
    the error. Raises ValueError, naming product_id, sub_type, date and the
    column, for the first value that is not a whole number of cents
    (``round(v*100)/100 != v``), is negative or not finite, or is at or above
    the sentinel (``v*100 >= 0xFFFFFFFF``).
    """
    null = values.is_null().to_numpy()
    v = values.cast(pl.Float64).fill_null(0.0).to_numpy().astype(np.float64)
    with np.errstate(invalid="ignore", over="ignore"):
        scaled = v * 100.0
        cents = np.round(scaled)
        bad = ~null & (
            ~np.isfinite(v) | (v < 0) | (cents / 100.0 != v) | (scaled >= float(NULL_CENTS))
        )
    if bad.any():
        i = int(np.flatnonzero(bad)[0])
        row = frame.row(i, named=True)
        raise ValueError(
            f"product_id={row['product_id']} sub_type={row['sub_type']} date={row['date']} "
            f"column {what}: {float(v[i])!r} cannot go on the wire (prices must be whole cents, "
            f">= 0 and below the null sentinel $42,949,672.95); {int(bad.sum())} such "
            f"value(s) in this column"
        )
    out = np.where(null, float(NULL_CENTS), cents).astype(np.uint32)
    return out


def encode_window(warehouse: Warehouse, start: date, end: date, out: Path) -> int:
    """Encode every stored price row in ``[start, end]`` to ``out``.

    The filter is ``MarketData.from_frame``'s with warmup 0, so the rows are
    exactly the ones ``PreparedMarket.prepare(warehouse, start, end)`` holds.
    Instruments are numbered by ``(product_id, sub_type)`` sorted; rows go out
    by ``(date, instrument)``. Returns the messages written. Every price is
    checked before the file is opened, so a refused window writes nothing.
    Raises OSError if ``out`` cannot be written (its directory must exist).
    """
    prices = warehouse.load_prices()
    frame = prices.filter((pl.col("date") >= start) & (pl.col("date") <= end))
    if frame["sub_type"].null_count():
        raise ValueError("a price row has a null sub_type; the wire needs one per instrument")
    instruments = (
        frame.select("product_id", "sub_type")
        .unique()
        .sort(["product_id", "sub_type"])
        .with_row_index("instrument")
    )
    rows = frame.join(instruments, on=["product_id", "sub_type"], how="left").sort(
        ["date", "instrument"]
    )
    cents = {c: to_cents(rows[c], what=c, frame=rows) for c in _PRICE_COLUMNS}
    return int(
        _engine.encode_wire(
            instruments["product_id"].cast(pl.Int64).to_numpy().astype(np.int64),
            [str(s) for s in instruments["sub_type"].to_list()],
            rows["date"].cast(pl.Int32).to_numpy().astype(np.int32),
            rows["instrument"].cast(pl.UInt32).to_numpy().astype(np.uint32),
            cents["market"],
            cents["low"],
            cents["mid"],
            cents["high"],
            str(out),
        )
    )
