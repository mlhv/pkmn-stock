"""Bootstrap-based rigor statistics: CIs, deflated Sharpe, Reality Check.

All randomness is seeded; same inputs + seed give byte-identical numbers.
Daily-return and Sharpe conventions match engine/metrics.py exactly, so
these figures stay consistent with every existing report. Caveat carried
by every surface that shows them: mark smoothing inflates Sharpe, and the
CIs/DSR built on these returns inherit that inflation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import polars as pl
from numpy.typing import NDArray

from pkmn_quant.engine.metrics import TRADING_DAYS_PER_YEAR


def stationary_bootstrap_indices(
    n: int, n_boot: int, mean_block: float, rng: np.random.Generator
) -> NDArray[np.intp]:
    """Politis-Romano stationary bootstrap index matrix, shape (n_boot, n).

    Each row resamples 0..n-1 in blocks of geometric random length
    (mean ``mean_block``) with wraparound, preserving autocorrelation.
    Vectorized: a restart mask starts new blocks, and each position takes
    its block's uniform start plus its offset within the block, mod n.
    """
    if n < 2:
        raise ValueError(f"need at least 2 observations, got {n}")
    if mean_block < 1.0:
        raise ValueError(f"mean_block must be >= 1, got {mean_block}")
    p = 1.0 / mean_block
    restart = rng.random((n_boot, n)) < p
    restart[:, 0] = True
    starts = rng.integers(0, n, size=(n_boot, n))
    pos = np.arange(n)
    seg_start_pos = np.maximum.accumulate(np.where(restart, pos, -1), axis=1)
    offset = pos - seg_start_pos
    seg_start_val = np.take_along_axis(starts, seg_start_pos, axis=1)
    return np.asarray((seg_start_val + offset) % n, dtype=np.intp)


def daily_returns_from_curve(curve: pl.DataFrame) -> NDArray[np.float64]:
    """metrics.py's daily-return convention on a date/equity frame."""
    eq = curve.sort("date")["equity"].to_numpy().astype(np.float64)
    if eq.size < 2:
        return np.empty(0, dtype=np.float64)
    return eq[1:] / eq[:-1] - 1.0


def _daily_sharpe(returns: NDArray[np.float64]) -> float:
    """Non-annualized Sharpe; 0.0 on zero std (metrics.py convention)."""
    std = float(returns.std(ddof=1)) if returns.size > 1 else 0.0
    return 0.0 if std == 0.0 else float(returns.mean()) / std


@dataclass(frozen=True)
class BootstrapCI:
    point: float
    lo: float
    hi: float
    level: float
    n_boot: int
    mean_block: float
    seed: int


def bootstrap_ci(
    daily_returns: NDArray[np.float64],
    metric: Literal["total_return", "sharpe"],
    *,
    n_boot: int = 10_000,
    mean_block: float = 10.0,
    seed: int = 42,
    level: float = 0.95,
) -> BootstrapCI:
    """Percentile CI for a return-series metric via stationary bootstrap."""
    r = np.asarray(daily_returns, dtype=np.float64)
    if r.size < 2:
        raise ValueError(f"need at least 2 daily returns, got {r.size}")
    rng = np.random.default_rng(seed)
    idx = stationary_bootstrap_indices(r.size, n_boot, mean_block, rng)
    samples = r[idx]  # (n_boot, n)
    ann = float(np.sqrt(TRADING_DAYS_PER_YEAR))
    if metric == "total_return":
        point = float(np.prod(1.0 + r) - 1.0)
        stats = np.prod(1.0 + samples, axis=1) - 1.0
    else:
        point = _daily_sharpe(r) * ann
        stds = samples.std(axis=1, ddof=1)
        means = samples.mean(axis=1)
        stats = np.where(stds == 0.0, 0.0, means / np.where(stds == 0.0, 1.0, stds)) * ann
    alpha = (1.0 - level) / 2.0
    lo, hi = np.quantile(stats, [alpha, 1.0 - alpha])
    return BootstrapCI(
        point=point,
        lo=float(lo),
        hi=float(hi),
        level=level,
        n_boot=n_boot,
        mean_block=mean_block,
        seed=seed,
    )
