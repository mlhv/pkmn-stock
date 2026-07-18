"""PreparedMarket: NativeBacktest's per-window inputs, built once, reused.

One walk-forward fold runs ~27 backtests over the same two windows; today
each re-loads parquet and re-flattens arrays. PreparedMarket hoists that:
numpy arrays are immutable and safe to share; ``market`` (used only by the
callback bridge) carries a mutable marks cursor that rewinds
deterministically, so it is safe across SEQUENTIAL runs in one thread —
never share one PreparedMarket across threads running bridged strategies.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import polars as pl
from numpy.typing import NDArray

from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.data import MarketData
from pkmn_quant.engine.portfolio import Asset

_EPOCH = date(1970, 1, 1)
_NULL_DAY = -(2**31)
_KIND_CODES = {"sealed": 0, "single": 1}


def _to_day(d: date) -> int:
    return (d - _EPOCH).days


def _kind_code(kind: str | None) -> int:
    """-1 ("other") for both an unrecognized kind string and None.

    None means a priced asset has no row at all in products.parquet -- a
    real condition in the warehouse (upstream tcgcsv catalog drift, not
    stale local data; see docs/research-findings-2026-07.md Plan 10). The
    Python engine never requires a catalog row: strategies build their
    universe by filtering/joining against ctx.products, so an uncataloged
    asset is simply absent from that join. Kind "other" reproduces that
    exactly here: it fails the `kind == 0`/`kind == 1` checks in the four
    kind-filtered strategies (buy-and-hold, sealed-accumulation, dip-buyer,
    xs-momentum) but cost-aware-reversion has no kind filter at all
    (cost_aware_reversion.py:76-97 scans every asset), so it stays a
    tradeable candidate there -- dropping the asset instead of tagging it
    "other" would silently break that parity.
    """
    if kind is None:
        return -1
    return _KIND_CODES.get(kind, -1)


@dataclass(frozen=True)
class PreparedMarket:
    start: date
    end: date
    warmup_days: int
    market: MarketData
    products: pl.DataFrame
    asset_list: list[Asset]
    asset_index: dict[Asset, int]
    trading_days: NDArray[np.int32]
    row_day: NDArray[np.int32]
    row_asset: NDArray[np.int32]
    row_market: NDArray[np.float64]
    row_mid: NDArray[np.float64]
    row_low: NDArray[np.float64]
    ev_day: NDArray[np.int32]
    ev_asset: NDArray[np.int32]
    ev_price: NDArray[np.float64]
    prod_id: NDArray[np.int64]
    prod_kind: NDArray[np.int8]
    prod_released: NDArray[np.int32]

    @classmethod
    def prepare(
        cls,
        warehouse: Warehouse,
        start: date,
        end: date,
        warmup_days: int = 0,
        *,
        frame: pl.DataFrame | None = None,
        products: pl.DataFrame | None = None,
    ) -> PreparedMarket:
        """Build once per window. ``frame``/``products`` accept the
        walkforward's shared, once-loaded copies; None loads from the
        warehouse (identical results — from_frame applies the same filter)."""
        market = (
            MarketData.from_frame(frame, start, end, warmup_days=warmup_days)
            if frame is not None
            else MarketData.from_warehouse(warehouse, start, end, warmup_days=warmup_days)
        )
        products_df = products if products is not None else warehouse.load_products()

        joined_frame = market.frame.sort("date")
        assets_df = (
            joined_frame.select("product_id", "sub_type")
            .unique()
            .sort(["product_id", "sub_type"])
            .with_row_index("asset_id")
        )
        asset_list = [
            Asset(product_id=int(pid), sub_type=str(st))
            for pid, st in assets_df.select("product_id", "sub_type").iter_rows()
        ]
        asset_index = {a: i for i, a in enumerate(asset_list)}

        joined = joined_frame.join(assets_df, on=["product_id", "sub_type"], how="left").sort(
            "date"
        )
        row_day = joined["date"].cast(pl.Int32).to_numpy().astype(np.int32)
        row_asset = joined["asset_id"].cast(pl.Int32).to_numpy().astype(np.int32)
        row_market = joined["market"].cast(pl.Float64).to_numpy().astype(np.float64)
        nan = float("nan")
        row_mid = joined["mid"].cast(pl.Float64).fill_null(nan).to_numpy().astype(np.float64)
        row_low = joined["low"].cast(pl.Float64).fill_null(nan).to_numpy().astype(np.float64)

        events = market.mark_events()
        ev_day = np.array([_to_day(d) for d, _, _ in events], dtype=np.int32)
        ev_asset = np.array([asset_index[a] for _, a, _ in events], dtype=np.int32)
        ev_price = np.array([p for _, _, p in events], dtype=np.float64)

        prod_info: dict[int, tuple[str, date | None]] = {
            int(r["product_id"]): (str(r["kind"]), r["released_on"])
            for r in products_df.iter_rows(named=True)
        }
        # .get(..., (None, None)): a priced asset absent from products.parquet
        # is not an error (see _kind_code docstring) -- it gets kind "other"
        # and no release date, same as the Python engine's implicit handling.
        prod_id = np.array([a.product_id for a in asset_list], dtype=np.int64)
        prod_kind = np.array(
            [_kind_code(prod_info.get(a.product_id, (None, None))[0]) for a in asset_list],
            dtype=np.int8,
        )
        prod_released = np.array(
            [
                _to_day(rel)
                if (rel := prod_info.get(a.product_id, (None, None))[1]) is not None
                else _NULL_DAY
                for a in asset_list
            ],
            dtype=np.int32,
        )

        trading_days = np.array([_to_day(d) for d in market.days], dtype=np.int32)

        return cls(
            start=start,
            end=end,
            warmup_days=warmup_days,
            market=market,
            products=products_df,
            asset_list=asset_list,
            asset_index=asset_index,
            trading_days=trading_days,
            row_day=row_day,
            row_asset=row_asset,
            row_market=row_market,
            row_mid=row_mid,
            row_low=row_low,
            ev_day=ev_day,
            ev_asset=ev_asset,
            ev_price=ev_price,
            prod_id=prod_id,
            prod_kind=prod_kind,
            prod_released=prod_released,
        )
