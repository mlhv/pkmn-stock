"""stats.py: seeded determinism, structural properties, analytic sanity.

Every test seeds its RNG; exact-value pins guard against silent numerical
drift the same way engine goldens do.
"""

import numpy as np
import polars as pl
import pytest

from pkmn_quant.research.stats import (
    BootstrapCI,
    bootstrap_ci,
    daily_returns_from_curve,
    stationary_bootstrap_indices,
)


def test_indices_shape_range_and_block_structure() -> None:
    rng = np.random.default_rng(7)
    idx = stationary_bootstrap_indices(50, 200, 8.0, rng)
    assert idx.shape == (200, 50)
    assert idx.min() >= 0 and idx.max() < 50
    # Within a block, indices advance consecutively mod n: each step is
    # either +1 (mod 50) from its neighbor or a fresh uniform restart.
    steps = (idx[:, 1:] - idx[:, :-1]) % 50
    frac_consecutive = float((steps == 1).mean())
    # p_restart = 1/8, so ~7/8 of steps continue the block. Wide tolerance:
    assert 0.80 < frac_consecutive < 0.95


def test_indices_deterministic_for_seed() -> None:
    a = stationary_bootstrap_indices(30, 50, 5.0, np.random.default_rng(42))
    b = stationary_bootstrap_indices(30, 50, 5.0, np.random.default_rng(42))
    assert (a == b).all()


def test_daily_returns_matches_metrics_convention() -> None:
    curve = pl.DataFrame(
        {"date": ["2025-01-03", "2025-01-01", "2025-01-02"], "equity": [102.0, 100.0, 101.0]}
    ).with_columns(pl.col("date").str.to_date())
    r = daily_returns_from_curve(curve)  # must sort by date first
    np.testing.assert_allclose(r, [0.01, 102.0 / 101.0 - 1.0])


def test_bootstrap_ci_brackets_point_and_is_deterministic() -> None:
    rng = np.random.default_rng(1)
    returns = rng.normal(0.001, 0.01, 400)
    ci = bootstrap_ci(returns, "total_return", n_boot=2000, seed=42)
    assert isinstance(ci, BootstrapCI)
    assert ci.lo <= ci.point <= ci.hi
    assert ci.lo < ci.hi
    again = bootstrap_ci(returns, "total_return", n_boot=2000, seed=42)
    assert (ci.lo, ci.point, ci.hi) == (again.lo, again.point, again.hi)


def test_bootstrap_ci_sharpe_zero_variance_is_zero_band() -> None:
    ci = bootstrap_ci(np.zeros(100), "sharpe", n_boot=200, seed=42)
    assert ci.point == 0.0 and ci.lo == 0.0 and ci.hi == 0.0


def test_bootstrap_ci_rejects_short_series() -> None:
    with pytest.raises(ValueError, match="at least"):
        bootstrap_ci(np.array([0.01]), "total_return")


def test_bootstrap_ci_coverage_near_nominal() -> None:
    """Spec acceptance: on iid normal returns the 95% CI covers the
    population total return close to nominally. 200 seeded repetitions;
    the band [0.85, 0.995] is wide enough to be seed-stable."""
    mu, n = 0.001, 300
    true_total = (1.0 + mu) ** n - 1.0
    hits = 0
    for rep in range(200):
        r = np.random.default_rng(1000 + rep).normal(mu, 0.01, n)
        ci = bootstrap_ci(r, "total_return", n_boot=400, seed=rep)
        hits += int(ci.lo <= true_total <= ci.hi)
    assert 0.85 <= hits / 200 <= 0.995
