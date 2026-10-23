from collections.abc import Callable

import numpy as np
from numpy.typing import NDArray

__version__: str

def run_backtest(
    *,
    trading_days: NDArray[np.int32],
    row_day: NDArray[np.int32],
    row_asset: NDArray[np.int32],
    row_market: NDArray[np.float64],
    row_mid: NDArray[np.float64],
    row_low: NDArray[np.float64],
    ev_day: NDArray[np.int32],
    ev_asset: NDArray[np.int32],
    ev_price: NDArray[np.float64],
    prod_id: NDArray[np.int64],
    prod_kind: NDArray[np.int8],
    prod_released: NDArray[np.int32],
    strategy_name: str,
    params: dict[str, float],
    universe_kind: int,
    fee_rate: float,
    shipping_per_line: float,
    tier_thresholds: NDArray[np.float64],
    tier_qtys: NDArray[np.int64],
    fallback_max_qty: int,
    impact_enabled: bool,
    initial_cash: float,
    holding_asset: NDArray[np.int32],
    holding_qty: NDArray[np.int64],
    holding_cost: NDArray[np.float64],
    holding_opened: NDArray[np.int32],
    execution: str,
    callback: Callable[[int, list[tuple[int, int, float, int]], float], list[tuple[int, int]]]
    | None,
) -> tuple[list[int], list[float], list[tuple[int, int, int, float, float, float]]]: ...
def book_sell_impact(
    market: float,
    low: float,
    qty: int,
    tier_thresholds: NDArray[np.float64],
    tier_qtys: NDArray[np.int64],
    fallback_max_qty: int,
) -> float: ...
def encode_wire(
    inst_product_id: NDArray[np.int64],
    inst_sub_type: list[str],
    row_day: NDArray[np.int32],
    row_instrument: NDArray[np.uint32],
    row_market: NDArray[np.uint32],
    row_low: NDArray[np.uint32],
    row_mid: NDArray[np.uint32],
    row_high: NDArray[np.uint32],
    path: str,
) -> int: ...
def wire_ladder_digests(
    path: str,
    tier_thresholds: NDArray[np.float64],
    tier_qtys: NDArray[np.int64],
    fallback_max_qty: int,
) -> tuple[NDArray[np.int32], NDArray[np.int64], list[str], NDArray[np.uint64], dict[str, int]]: ...
def ladder_digests(
    row_day: NDArray[np.int32],
    row_asset: NDArray[np.int32],
    row_market: NDArray[np.float64],
    row_mid: NDArray[np.float64],
    row_low: NDArray[np.float64],
    tier_thresholds: NDArray[np.float64],
    tier_qtys: NDArray[np.int64],
    fallback_max_qty: int,
) -> NDArray[np.uint64]: ...
