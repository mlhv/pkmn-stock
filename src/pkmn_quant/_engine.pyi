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
    callback: Callable[[int, list[tuple[int, int, float, int]], float], list[tuple[int, int]]]
    | None,
) -> tuple[list[int], list[float], list[tuple[int, int, int, float, float, float]]]: ...
