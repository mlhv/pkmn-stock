# Statistical Rigor Pack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn point-estimate results into defensible statistical claims: stationary-block-bootstrap confidence intervals per strategy, deflated Sharpe across the strategy zoo, and White's Reality Check for the joint "did anything really beat buy-and-hold" question, surfaced via a new `pkmn evaluate` command and a CI band in every walkforward report.

**Architecture:** One new pure-math module (`research/stats.py`, numpy + scipy, fully seeded) shared by two surfaces: the existing walkforward report/artifact writers gain an optional CI parameter, and a new `pkmn evaluate` CLI command runs the cross-strategy analysis over existing `data/results/wf-*` artifacts and records itself in the experiment registry. No engine or C++ changes.

**Tech Stack:** numpy, scipy (`scipy.stats.norm/skew/kurtosis` only), polars, typer.

**Spec:** `docs/superpowers/specs/2026-07-18-rigor-pack-design.md`. Read it before starting any task.

## Global Constraints

- **Determinism:** all randomness flows through a seeded `np.random.Generator` (default seed 42); same inputs + seed = byte-identical outputs. Tests pin exact values.
- **Convention consistency:** daily returns = `eq[1:]/eq[:-1] - 1` on the date-sorted curve; Sharpe = `mean/std(ddof=1) * sqrt(365)` with `0.0` on zero std — exactly `engine/metrics.py`'s definitions. Import `TRADING_DAYS_PER_YEAR` from `pkmn_quant.engine.metrics`; never hardcode 365 in new code.
- **Not-computable is NaN, never a fake number:** degenerate inputs (zero-variance returns, non-positive DSR denominator) return `float("nan")`; report layers print "n/a (reason)". No divide-by-zero crashes.
- **Honesty rules:** every report surface showing the new metrics carries the mark-smoothing caveat extension (CIs and DSR inherit the same Sharpe inflation). Findings doc gets measured numbers only.
- **Deflated Sharpe lives ONLY in `pkmn evaluate`** (trial set = the strategy zoo). Walkforward reports gain the bootstrap CI only — per-run DSR is explicitly out of scope (spec section 3).
- scipy becomes an explicit main dependency (`scipy>=1.14`) with a `scipy.*` mypy `ignore_missing_imports` override (mirroring the existing `sklearn.*` override). `pyproject.toml` and `uv.lock` are committed together.
- All four gates before every commit: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`. Current baseline: 349 passed + 1 skipped; no existing test may change.
- No engine/, cpp/, or strategies/ changes anywhere in this plan.
- Workflow: STOP after each completed task, explain at intern level, wait for explicit green light (CLAUDE.md).
- Branch: `feat/rigor-pack` (already created; spec committed).

## File Map

Created:
- `src/pkmn_quant/research/stats.py` — bootstrap engine, CIs, DSR, Reality Check
- `tests/research/test_stats.py` — unit + analytic-sanity tests
- `tests/test_cli_evaluate.py` — end-to-end `pkmn evaluate` tests

Modified:
- `pyproject.toml` + `uv.lock` — scipy dependency + mypy override
- `src/pkmn_quant/research/report.py` — optional CI section in `render_markdown`
- `src/pkmn_quant/research/artifacts.py` — optional `rigor` block in `write_walkforward_json`
- `src/pkmn_quant/cli.py` — walkforward computes/passes the CI; new `evaluate` command
- `tests/test_cli_walkforward.py` — CI-in-report test (append)
- `docs/research-findings-2026-07.md`, `CLAUDE.md` — Task 5

---

### Task 1: Bootstrap engine + confidence intervals

**Files:**
- Create: `src/pkmn_quant/research/stats.py`, `tests/research/test_stats.py`
- Modify: `pyproject.toml` (+ `uv.lock`)

**Interfaces (produces; Tasks 2-4 rely on these exact signatures):**
- `stationary_bootstrap_indices(n: int, n_boot: int, mean_block: float, rng: np.random.Generator) -> NDArray[np.intp]` — shape `(n_boot, n)`, values in `[0, n)`.
- `daily_returns_from_curve(curve: pl.DataFrame) -> NDArray[np.float64]` — from a `date`/`equity` frame, metrics.py convention.
- `BootstrapCI` frozen dataclass: fields `point, lo, hi: float`, `level: float`, `n_boot: int`, `mean_block: float`, `seed: int`.
- `bootstrap_ci(daily_returns: NDArray[np.float64], metric: Literal["total_return", "sharpe"], *, n_boot: int = 10_000, mean_block: float = 10.0, seed: int = 42, level: float = 0.95) -> BootstrapCI`.

- [ ] **Step 1: scipy dependency + mypy override**

In `pyproject.toml`: add `"scipy>=1.14",` to the main `[project] dependencies` list (alphabetical position), and append after the existing sklearn override block:

```toml
[[tool.mypy.overrides]]
module = "scipy.*"
ignore_missing_imports = true
```

Run: `uv sync` (locks scipy explicitly; it was already present transitively).

- [ ] **Step 2: Write the failing tests**

`tests/research/test_stats.py`:

```python
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
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/research/test_stats.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pkmn_quant.research.stats'`.

- [ ] **Step 4: Write stats.py (Task 1 portion)**

`src/pkmn_quant/research/stats.py`:

```python
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
```

- [ ] **Step 5: Run tests, then full gates**

```bash
uv run pytest tests/research/test_stats.py -v
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Expected: new tests PASS; suite grows by 6, nothing else moves.

- [ ] **Step 6: Commit**

```bash
git add src/pkmn_quant/research/stats.py tests/research/test_stats.py pyproject.toml uv.lock
git commit -m "feat: stationary block bootstrap + confidence intervals (research/stats.py)"
```

---

### Task 2: Deflated Sharpe + White's Reality Check

**Files:**
- Modify: `src/pkmn_quant/research/stats.py` (append), `tests/research/test_stats.py` (append)

**Interfaces:**
- Consumes: `stationary_bootstrap_indices`, `_daily_sharpe` (Task 1).
- Produces:
  - `deflated_sharpe(daily_returns: NDArray[np.float64], trial_daily_sharpes: Sequence[float]) -> float` — probability in [0, 1] that the true Sharpe exceeds 0 after selection among the trials; `nan` when not computable. `trial_daily_sharpes` are NON-annualized and include the candidate's own.
  - `whites_reality_check(excess_returns: NDArray[np.float64], *, n_boot: int = 10_000, mean_block: float = 10.0, seed: int = 42) -> float` — p-value; `excess_returns` has shape `(n_strategies, n_days)`, each row a strategy's daily return minus the benchmark's, on aligned days.

- [ ] **Step 1: Append the failing tests**

Append to `tests/research/test_stats.py`:

```python
def test_deflated_sharpe_rewards_real_edge_and_punishes_none() -> None:
    rng = np.random.default_rng(3)
    zoo = [rng.normal(0.0, 0.01, 600) for _ in range(5)]
    edge = rng.normal(0.003, 0.01, 600)
    sharpes = [_ds(s) for s in zoo] + [_ds(edge)]
    dsr_edge = deflated_sharpe(edge, sharpes)
    dsr_none = deflated_sharpe(zoo[0], sharpes)
    assert dsr_edge > 0.95
    # A no-edge candidate must score clearly below the real edge, and not
    # look confident. (Not asserted < 0.5: a lucky no-edge draw can sit
    # near coin-flip; the discrimination claim is the ordering.)
    assert dsr_none < dsr_edge
    assert dsr_none < 0.9


def _ds(returns: np.ndarray) -> float:
    std = float(returns.std(ddof=1))
    return 0.0 if std == 0.0 else float(returns.mean()) / std


def test_deflated_sharpe_edge_cases() -> None:
    rng = np.random.default_rng(4)
    r = rng.normal(0.001, 0.01, 100)
    with pytest.raises(ValueError, match="trials"):
        deflated_sharpe(r, [0.1])
    assert np.isnan(deflated_sharpe(np.zeros(100), [0.0, 0.1]))


def test_deflated_sharpe_deterministic_pin() -> None:
    rng = np.random.default_rng(5)
    r = rng.normal(0.001, 0.01, 300)
    sharpes = [0.0, 0.02, 0.05, _ds(r)]
    a = deflated_sharpe(r, sharpes)
    assert a == deflated_sharpe(r, sharpes)
    assert 0.0 <= a <= 1.0


def test_reality_check_null_vs_edge() -> None:
    rng = np.random.default_rng(6)
    null_zoo = rng.normal(0.0, 0.01, size=(4, 600))
    p_null = whites_reality_check(null_zoo, n_boot=2000, seed=42)
    # Under the null p is roughly uniform; > 0.05 is the seed-stable claim
    # (the sharp discrimination is the p_edge side below).
    assert p_null > 0.05
    edge_zoo = null_zoo.copy()
    edge_zoo[2] = rng.normal(0.005, 0.01, 600)
    p_edge = whites_reality_check(edge_zoo, n_boot=2000, seed=42)
    assert p_edge < 0.01
    assert p_null == whites_reality_check(null_zoo, n_boot=2000, seed=42)  # deterministic


def test_reality_check_rejects_empty() -> None:
    with pytest.raises(ValueError, match="strategy"):
        whites_reality_check(np.empty((0, 100)))
```

Add `deflated_sharpe, whites_reality_check` to the file's import from `pkmn_quant.research.stats`.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/research/test_stats.py -k "deflated or reality" -v`
Expected: FAIL — ImportError on the two new names.

- [ ] **Step 3: Append implementations to stats.py**

```python
_EULER_GAMMA = 0.5772156649015329


def deflated_sharpe(
    daily_returns: NDArray[np.float64], trial_daily_sharpes: "Sequence[float]"
) -> float:
    """Bailey & Lopez de Prado deflated Sharpe ratio.

    Probability that the candidate's true Sharpe exceeds zero, after
    correcting for having selected it among ``len(trial_daily_sharpes)``
    trials (which must include the candidate) with the observed variance
    of trial Sharpes, and for the return distribution's skew/kurtosis.
    All Sharpes here are per-day (non-annualized); the formula requires it.
    Returns nan when not computable (zero-variance returns, degenerate
    denominator) — surfaces print "n/a" rather than a fake number.
    """
    from scipy import stats as sps

    n_trials = len(trial_daily_sharpes)
    if n_trials < 2:
        raise ValueError(f"need >= 2 trials for deflation, got {n_trials}")
    r = np.asarray(daily_returns, dtype=np.float64)
    n = r.size
    if n < 3:
        return float("nan")
    std = float(r.std(ddof=1))
    if std == 0.0:
        return float("nan")
    sr = float(r.mean()) / std
    var_trials = float(np.var(np.asarray(trial_daily_sharpes, dtype=np.float64), ddof=1))
    if var_trials == 0.0:
        sr0 = 0.0
    else:
        sr0 = float(
            np.sqrt(var_trials)
            * (
                (1.0 - _EULER_GAMMA) * sps.norm.ppf(1.0 - 1.0 / n_trials)
                + _EULER_GAMMA * sps.norm.ppf(1.0 - 1.0 / (n_trials * np.e))
            )
        )
    skew = float(sps.skew(r))
    kurt = float(sps.kurtosis(r, fisher=False))
    denom_sq = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr**2
    if denom_sq <= 0.0:
        return float("nan")
    z = (sr - sr0) * np.sqrt(n - 1.0) / np.sqrt(denom_sq)
    return float(sps.norm.cdf(z))


def whites_reality_check(
    excess_returns: NDArray[np.float64],
    *,
    n_boot: int = 10_000,
    mean_block: float = 10.0,
    seed: int = 42,
) -> float:
    """White (2000) Reality Check p-value over a strategy zoo.

    ``excess_returns[k]`` is strategy k's daily return minus the
    benchmark's on aligned days. One joint index resample per bootstrap
    draw (same days for every strategy) preserves cross-strategy
    correlation. Statistic: sqrt(n) * max_k mean(excess_k); bootstrap
    distribution is recentered per strategy. p-value = fraction of
    bootstrap maxima >= observed maximum.
    """
    x = np.asarray(excess_returns, dtype=np.float64)
    if x.ndim != 2 or x.shape[0] < 1:
        raise ValueError(f"need a (n_strategy, n_days) matrix with >= 1 row, got {x.shape}")
    _, n = x.shape
    rng = np.random.default_rng(seed)
    obs_means = x.mean(axis=1)
    obs_stat = float(np.sqrt(n) * obs_means.max())
    exceed = 0
    chunk = 1000
    done = 0
    while done < n_boot:
        m = min(chunk, n_boot - done)
        idx = stationary_bootstrap_indices(n, m, mean_block, rng)
        boot_means = x[:, idx].mean(axis=2)  # (n_strategy, m)
        recentered = np.sqrt(n) * (boot_means - obs_means[:, None])
        exceed += int((recentered.max(axis=0) >= obs_stat).sum())
        done += m
    return exceed / n_boot
```

Add `from collections.abc import Sequence` to the module's imports (and drop the quoted annotation).

- [ ] **Step 4: Run tests + gates**

```bash
uv run pytest tests/research/test_stats.py -v
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy
```

- [ ] **Step 5: Commit**

```bash
git add src/pkmn_quant/research/stats.py tests/research/test_stats.py
git commit -m "feat: deflated Sharpe ratio + White's Reality Check"
```

---

### Task 3: Bootstrap CI in walkforward report + artifact

**Files:**
- Modify: `src/pkmn_quant/research/report.py`, `src/pkmn_quant/research/artifacts.py`, `src/pkmn_quant/cli.py` (walkforward command), `tests/test_cli_walkforward.py` (append)

**Interfaces:**
- Consumes: `BootstrapCI`, `bootstrap_ci`, `daily_returns_from_curve` (Task 1).
- Produces: `render_markdown(result, strategy_name, ci: BootstrapCI | None = None)`; `write_walkforward_json(run_dir, result, strategy_name, ci: BootstrapCI | None = None)` writing a top-level `"rigor"` key only when `ci is not None` (old-shape files unchanged; `load_walkforward_json` ignores unknown top-level keys — verify, it reads keys explicitly).

- [ ] **Step 1: Append the failing test**

Append to `tests/test_cli_walkforward.py`:

```python
def test_walkforward_report_carries_bootstrap_ci(tmp_path: Path) -> None:
    """report.md gains the CI band + caveat; walkforward.json gains rigor."""
    import json

    seed_forty_days(tmp_path)
    result = CliRunner().invoke(
        app,
        [
            "walkforward", "--strategy", "sealed-accumulation",
            "--start", "2025-01-01", "--end", "2025-02-09",
            "--is-days", "10", "--oos-days", "10", "--trials", "2",
            "--cash", "1000", "--root", str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    run_dir = next((tmp_path / "data" / "results").iterdir())
    report = (run_dir / "report.md").read_text()
    assert "95% CI" in report
    assert "inherit" in report  # the mark-smoothing caveat extension
    rigor = json.loads((run_dir / "walkforward.json").read_text())["rigor"]
    ci = rigor["stitched_total_return_ci"]
    assert ci["lo"] <= ci["point"] <= ci["hi"]
    assert ci["seed"] == 42
```

Run: `uv run pytest tests/test_cli_walkforward.py::test_walkforward_report_carries_bootstrap_ci -v`
Expected: FAIL — `KeyError: 'rigor'` (or missing "95% CI" in report).

- [ ] **Step 2: report.py**

Change `render_markdown`'s signature and append a section (import `BootstrapCI` from `pkmn_quant.research.stats` under `TYPE_CHECKING` if runtime import creates a cycle — it does not; plain import is fine):

```python
def render_markdown(
    result: WalkForwardResult, strategy_name: str, ci: BootstrapCI | None = None
) -> str:
```

After the existing Summary loop, before the final join:

```python
    if ci is not None:
        lines += [
            "",
            "## Rigor",
            "",
            f"- stitched OOS total return {ci.point:.2%}, "
            f"{ci.level:.0%} CI [{ci.lo:.2%}, {ci.hi:.2%}]",
            f"  (stationary block bootstrap: n_boot={ci.n_boot}, "
            f"mean block {ci.mean_block:g}d, seed {ci.seed})",
            "- CIs inherit the mark-smoothing Sharpe inflation noted above;",
            "  treat the band as optimistic, not gospel.",
        ]
```

- [ ] **Step 3: artifacts.py**

`write_walkforward_json` gains the same optional parameter; before `json.dumps`-ing the payload dict, add:

```python
    if ci is not None:
        payload["rigor"] = {
            "stitched_total_return_ci": {
                "point": ci.point,
                "lo": ci.lo,
                "hi": ci.hi,
                "level": ci.level,
                "n_boot": ci.n_boot,
                "mean_block": ci.mean_block,
                "seed": ci.seed,
            }
        }
```

(Adapt the local name if the payload dict is named differently; add the `BootstrapCI` import. Confirm `load_walkforward_json` reads named keys only, so the extra key is ignored on load.)

- [ ] **Step 4: cli.py walkforward wiring**

In the walkforward command, after `result = run_walkforward(...)` and before writing artifacts:

```python
    from pkmn_quant.research.stats import bootstrap_ci, daily_returns_from_curve

    daily = daily_returns_from_curve(result.stitched_curve)
    stitched_ci = bootstrap_ci(daily, "total_return") if daily.size >= 2 else None
```

Then pass `ci=stitched_ci` to both `render_markdown(...)` and `write_walkforward_json(...)`.

- [ ] **Step 5: Run everything**

```bash
uv run pytest tests/test_cli_walkforward.py -q
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Expected: all PASS; no existing assertion moves (the CI is additive; engine numbers untouched).

- [ ] **Step 6: Commit**

```bash
git add src/pkmn_quant/research/report.py src/pkmn_quant/research/artifacts.py src/pkmn_quant/cli.py tests/test_cli_walkforward.py
git commit -m "feat: bootstrap CI band in walkforward report + artifact rigor block"
```

---

### Task 4: `pkmn evaluate` — the cross-strategy command

**Files:**
- Modify: `src/pkmn_quant/cli.py` (new command)
- Create: `tests/test_cli_evaluate.py`

**Interfaces:**
- Consumes: everything from Tasks 1-2; `record_run` (existing, `command="evaluate"`).
- Produces: `pkmn evaluate [--root PATH] [--benchmark PATH] [--n-boot 10000] [--block 10.0] [--seed 42]`; artifact dir `data/results/evaluate-<YYYY-MM-DD>/` with `report.md` + `evaluate.json`; a registry record.

- [ ] **Step 1: Write the failing tests**

`tests/test_cli_evaluate.py`:

```python
"""pkmn evaluate: cross-strategy rigor over synthetic walkforward artifacts."""

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl
from typer.testing import CliRunner

from pkmn_quant.cli import app
from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA
from pkmn_quant.data.warehouse import Warehouse
from tests.helpers import price_row

START = date(2025, 1, 1)


def _write_curve(run_dir: Path, name: str, equity: list[float], as_wf: bool = True) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    days = [START + timedelta(days=i) for i in range(len(equity))]
    frame = pl.DataFrame({"date": days, "equity": equity})
    if as_wf:
        frame.write_parquet(run_dir / "stitched_equity.parquet")
        (run_dir / "walkforward.json").write_text(
            json.dumps({"strategy": name, "folds": [], "summary": {}})
        )
    else:
        frame.write_parquet(run_dir / "equity.parquet")


def seed_everything(root: Path, n_days: int = 200) -> None:
    # a real (tiny) warehouse so record_run's data fingerprint works
    w = Warehouse(Paths(root=root))
    w.write_prices(START, pl.DataFrame([price_row(START, 1, 10.0)], schema=PRICE_SCHEMA))
    rng = np.random.default_rng(0)
    results = root / "data" / "results"
    bench = list(100.0 * np.cumprod(1.0 + rng.normal(0.002, 0.01, n_days)))
    _write_curve(results / "buy-and-hold-sealed-x", "buy-and-hold", bench, as_wf=False)
    for name, drift in [("alpha", 0.0), ("beta", -0.001)]:
        eq = list(100.0 * np.cumprod(1.0 + rng.normal(drift, 0.01, n_days)))
        _write_curve(results / f"wf-{name}-x", name, eq)


def run_eval(root: Path, *extra: str) -> object:
    return CliRunner().invoke(app, ["evaluate", "--root", str(root), "--n-boot", "500", *extra])


def test_evaluate_end_to_end(tmp_path: Path) -> None:
    seed_everything(tmp_path)
    result = run_eval(tmp_path)
    assert result.exit_code == 0, result.output
    out = next((tmp_path / "data" / "results").glob("evaluate-*"))
    report = (out / "report.md").read_text()
    assert "Reality Check" in report and "deflated" in report.lower()
    assert "inherit" in report  # caveat present
    payload = json.loads((out / "evaluate.json").read_text())
    assert set(payload["strategies"]) == {"alpha", "beta"}
    for s in payload["strategies"].values():
        assert s["ci"]["lo"] <= s["ci"]["point"] <= s["ci"]["hi"]
        assert 0.0 <= s["dsr"] <= 1.0
    assert 0.0 <= payload["reality_check_p"] <= 1.0
    assert payload["params"] == {"n_boot": 500, "mean_block": 10.0, "seed": 42}


def test_evaluate_is_deterministic(tmp_path: Path) -> None:
    seed_everything(tmp_path)
    assert run_eval(tmp_path).exit_code == 0
    first = json.loads(
        (next((tmp_path / "data" / "results").glob("evaluate-*")) / "evaluate.json").read_text()
    )
    assert run_eval(tmp_path).exit_code == 0  # overwrites same-day dir
    second = json.loads(
        (next((tmp_path / "data" / "results").glob("evaluate-*")) / "evaluate.json").read_text()
    )
    assert first == second


def test_evaluate_records_run(tmp_path: Path) -> None:
    from pkmn_quant.research.runs import load_runs

    seed_everything(tmp_path)
    assert run_eval(tmp_path).exit_code == 0
    (record,) = load_runs(tmp_path)
    assert record.command == "evaluate"
    assert "reality_check_p" in record.results
    assert record.config["n_boot"] == 500


def test_evaluate_needs_two_strategies(tmp_path: Path) -> None:
    seed_everything(tmp_path)
    import shutil

    shutil.rmtree(tmp_path / "data" / "results" / "wf-beta-x")
    result = run_eval(tmp_path)
    assert result.exit_code != 0
    assert "2 strategies" in result.output
    assert "Traceback" not in result.output


def test_evaluate_clean_error_without_artifacts(tmp_path: Path) -> None:
    (tmp_path / "data" / "results").mkdir(parents=True)
    result = run_eval(tmp_path)
    assert result.exit_code != 0
    assert "no walk-forward artifacts" in result.output
    assert "Traceback" not in result.output


def test_evaluate_thin_overlap_errors(tmp_path: Path) -> None:
    seed_everything(tmp_path, n_days=30)  # < 60 common days
    result = run_eval(tmp_path)
    assert result.exit_code != 0
    assert "overlap" in result.output
```

Run: `uv run pytest tests/test_cli_evaluate.py -v`
Expected: FAIL — `No such command 'evaluate'`.

- [ ] **Step 2: The command**

Add to `src/pkmn_quant/cli.py` (following the file's existing command style — local imports inside the function body, `--root` option matching the other commands' pattern):

```python
@app.command()
def evaluate(
    root: Path = typer.Option(Path("."), help="Project root containing data/results/."),
    benchmark: Path | None = typer.Option(
        None,
        help="Benchmark artifact dir containing equity.parquet; "
        "default: auto-locate data/results/buy-and-hold-sealed-*.",
    ),
    n_boot: int = typer.Option(10_000, help="Bootstrap resamples."),
    block: float = typer.Option(10.0, help="Mean bootstrap block length, days."),
    seed: int = typer.Option(42, help="Bootstrap seed (results are deterministic)."),
) -> None:
    """Cross-strategy rigor: bootstrap CIs, deflated Sharpe, Reality Check.

    Reads every data/results/wf-*/ walk-forward artifact plus the
    buy-and-hold benchmark curve, aligns them on common dates, and writes
    an evaluate-<date>/ artifact with report.md + evaluate.json. The
    Reality Check answers "did ANY strategy beat the benchmark, given how
    many we tried"; deflated Sharpe corrects each Sharpe for that same
    selection. Recorded in the experiment registry like every other run.
    """
    import json as jsonlib
    from datetime import date as date_type

    import numpy as np
    import polars as pl

    from pkmn_quant.config import Paths
    from pkmn_quant.data.warehouse import Warehouse
    from pkmn_quant.engine.metrics import TRADING_DAYS_PER_YEAR
    from pkmn_quant.research.runs import record_run
    from pkmn_quant.research.stats import (
        bootstrap_ci,
        daily_returns_from_curve,
        deflated_sharpe,
        whites_reality_check,
    )

    results_dir = root / "data" / "results"

    def fail(msg: str) -> None:
        typer.echo(f"error: {msg}", err=True)
        raise typer.Exit(1)

    # -- discover strategy artifacts (longest curve wins per strategy) --
    curves: dict[str, pl.DataFrame] = {}
    for d in sorted(results_dir.glob("wf-*")):
        meta, parquet = d / "walkforward.json", d / "stitched_equity.parquet"
        if not (meta.exists() and parquet.exists()):
            continue
        name = str(jsonlib.loads(meta.read_text())["strategy"])
        frame = pl.read_parquet(parquet)
        if frame.height < 2:
            typer.echo(f"skipping {d.name}: curve too short", err=True)
            continue
        if name in curves and curves[name].height >= frame.height:
            typer.echo(f"skipping {d.name}: shorter than another {name} artifact", err=True)
            continue
        curves[name] = frame
    if not curves:
        fail(f"no walk-forward artifacts found under {results_dir}/wf-*")
    if len(curves) < 2:
        fail(f"Reality Check needs >= 2 strategies, found {len(curves)}")

    # -- benchmark --
    if benchmark is None:
        candidates = sorted(results_dir.glob("buy-and-hold-sealed-*"))
        candidates = [c for c in candidates if (c / "equity.parquet").exists()]
        if not candidates:
            fail("no buy-and-hold-sealed-* benchmark artifact; pass --benchmark")
        benchmark = max(candidates, key=lambda c: pl.read_parquet(c / "equity.parquet").height)
    bench_frame = pl.read_parquet(benchmark / "equity.parquet")

    # -- align on common dates --
    common = set(bench_frame["date"].to_list())
    for frame in curves.values():
        common &= set(frame["date"].to_list())
    if len(common) < 60:
        fail(f"date overlap across artifacts is {len(common)} days; need >= 60")
    keep = pl.Series("date", sorted(common))

    def aligned_returns(frame: pl.DataFrame) -> "np.ndarray":
        return daily_returns_from_curve(frame.filter(pl.col("date").is_in(keep)))

    bench_r = aligned_returns(bench_frame)
    strat_r = {name: aligned_returns(frame) for name, frame in sorted(curves.items())}

    # -- metrics --
    ann = float(np.sqrt(TRADING_DAYS_PER_YEAR))
    daily_sharpes = {
        name: (0.0 if r.std(ddof=1) == 0.0 else float(r.mean()) / float(r.std(ddof=1)))
        for name, r in strat_r.items()
    }
    zoo = list(daily_sharpes.values())
    per_strategy: dict[str, dict[str, object]] = {}
    for name, r in strat_r.items():
        ci = bootstrap_ci(r, "total_return", n_boot=n_boot, mean_block=block, seed=seed)
        dsr = deflated_sharpe(r, zoo)
        per_strategy[name] = {
            "total_return": ci.point,
            "ci": {"point": ci.point, "lo": ci.lo, "hi": ci.hi, "level": ci.level},
            "sharpe": daily_sharpes[name] * ann,
            "dsr": None if np.isnan(dsr) else dsr,
        }
    excess = np.vstack([strat_r[name] - bench_r for name in sorted(strat_r)])
    p_value = whites_reality_check(excess, n_boot=n_boot, mean_block=block, seed=seed)

    # -- artifact --
    out_dir = results_dir / f"evaluate-{date_type.today().isoformat()}"
    if out_dir.exists():
        typer.echo(f"warning: overwriting existing results in {out_dir}", err=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    dates = keep.to_list()
    payload = {
        "strategies": per_strategy,
        "reality_check_p": p_value,
        "benchmark": str(benchmark),
        "n_days": len(dates),
        "start": str(dates[0]),
        "end": str(dates[-1]),
        "params": {"n_boot": n_boot, "mean_block": block, "seed": seed},
    }
    (out_dir / "evaluate.json").write_text(jsonlib.dumps(payload, indent=2, default=str))

    lines = [
        "# Cross-strategy rigor report",
        "",
        f"{len(strat_r)} strategies vs benchmark `{benchmark.name}`, "
        f"{len(dates)} aligned days ({dates[0]} .. {dates[-1]}).",
        "",
        "Sharpe (and therefore the CIs and deflated Sharpe built on these",
        "returns) inherit the mark-smoothing inflation documented in every",
        "walkforward report; treat all bands as optimistic.",
        "",
        "| strategy | OOS total return | 95% CI | Sharpe (ann.) | deflated Sharpe |",
        "|---|---|---|---|---|",
    ]
    for name, s in per_strategy.items():
        ci_d = s["ci"]
        dsr_txt = "n/a (zero-variance returns)" if s["dsr"] is None else f"{s['dsr']:.3f}"
        lines.append(
            f"| {name} | {s['total_return']:.2%} "
            f"| [{ci_d['lo']:.2%}, {ci_d['hi']:.2%}] "  # type: ignore[index]
            f"| {s['sharpe']:.2f} | {dsr_txt} |"
        )
    lines += [
        "",
        f"**White's Reality Check** (best strategy vs benchmark, jointly over "
        f"{len(strat_r)} strategies): p = {p_value:.4f}",
        f"(stationary block bootstrap: n_boot={n_boot}, mean block {block:g}d, seed {seed})",
        "",
    ]
    (out_dir / "report.md").write_text("\n".join(lines))

    flat: dict[str, float] = {"reality_check_p": p_value}
    for name, s in per_strategy.items():
        flat[f"{name}_total_return"] = float(s["total_return"])  # type: ignore[arg-type]
        if s["dsr"] is not None:
            flat[f"{name}_dsr"] = float(s["dsr"])  # type: ignore[arg-type]
    run_id = record_run(
        root=root,
        command="evaluate",
        strategy=",".join(sorted(strat_r)),
        config={
            "command": "evaluate",
            "strategies": sorted(strat_r),
            "benchmark": str(benchmark),
            "n_boot": n_boot,
            "mean_block": block,
            "seed": seed,
            "start": str(dates[0]),
            "end": str(dates[-1]),
        },
        results=flat,
        artifact_path=out_dir,
        warehouse=Warehouse(Paths(root=root)),
    )
    if run_id is not None:
        typer.echo(f"run recorded: {run_id}")
    typer.echo(f"Reality Check p = {p_value:.4f}")
    typer.echo(f"results written to {out_dir}")
```

If mypy rejects the `dict[str, object]` juggling, replace `per_strategy`'s value type with a small frozen dataclass — behavior identical; the JSON/report shapes in the tests are the contract.

- [ ] **Step 3: Run tests + gates**

```bash
uv run pytest tests/test_cli_evaluate.py -v
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy
```

- [ ] **Step 4: Commit**

```bash
git add src/pkmn_quant/cli.py tests/test_cli_evaluate.py
git commit -m "feat: pkmn evaluate — cross-strategy CIs, deflated Sharpe, Reality Check"
```

---

### Task 5: Real-data run + findings + CLAUDE.md

**Files:**
- Modify: `docs/research-findings-2026-07.md`, `CLAUDE.md`
- Test: manual `pkmn evaluate` run against local `data/` (gitignored)

- [ ] **Step 1: Run it for real**

```bash
uv run pkmn evaluate | tee /tmp/evaluate_out.txt
```

Expected: discovers the five `wf-*` strategies (skipping the shorter duplicate ml-ranker artifact with a stderr note), auto-locates the `buy-and-hold-sealed-2024-08-28-2026-06-30` benchmark, prints the Reality Check p-value, writes `data/results/evaluate-<today>/`, records a registry run. Runtime: seconds (numpy). Read the generated `report.md` in full.

- [ ] **Step 2: Findings doc**

New section in `docs/research-findings-2026-07.md`: "Rigor pack (<today's date>): CIs, deflated Sharpe, Reality Check" containing: the report table verbatim; the Reality Check p-value with one paragraph of interpretation written from the actual numbers (expected shape, given every active strategy is negative OOS: wide CIs that mostly straddle or sit below zero, DSRs well under 0.5, and a large p-value confirming no strategy beats buy-and-hold after multiple-testing correction — but WRITE WHAT THE NUMBERS ACTUALLY SAY, not this prediction); the run's registry id; the standing caveat that mark smoothing inflates every Sharpe-derived figure.

- [ ] **Step 3: CLAUDE.md**

- Status: new bullet for the rigor pack (what shipped, test counts from the actual run, the measured Reality Check p-value).
- Commands: add `uv run pkmn evaluate                                     # cross-strategy rigor: CIs, DSR, Reality Check`.
- Layout, `research/` bullet: append `stats.py`: seeded bootstrap statistics (CIs, deflated Sharpe, Reality Check) feeding `pkmn evaluate` and walkforward reports.

- [ ] **Step 4: Gates, commit**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy
git add docs/research-findings-2026-07.md CLAUDE.md
git commit -m "docs: rigor-pack findings — measured CIs, DSR, Reality Check on real artifacts"
```

---

## Self-review notes (already applied)

- Spec coverage: stats core → Tasks 1-2; evaluate command incl. discovery/alignment/errors/registry → Task 4; walkforward CI (and the explicit no-per-run-DSR rule) → Task 3; honesty rules → caveat lines in Tasks 3-4 report text + Task 5 findings; testing section → each task's tests (determinism pins, analytic sanity, CLI end-to-end, clean errors); out-of-scope items untouched.
- Type consistency: `BootstrapCI` fields used identically in Tasks 1/3/4; `bootstrap_ci(returns, "total_return", n_boot=, mean_block=, seed=)` same everywhere; `whites_reality_check(excess, n_boot=, mean_block=, seed=)` matches Task 2's definition; `render_markdown`/`write_walkforward_json` keyword `ci=` consistent.
- Deliberate choices an executor should not "fix": trial set for DSR is the zoo's daily Sharpes including the candidate (spec choice); the RC uses one joint resample across strategies (cross-correlation preservation is the point); `n/a` printing for NaN DSR instead of dropping the row; the evaluate artifact overwrites same-day reruns (matching backtest/walkforward overwrite-warn behavior); duplicate-strategy artifacts resolved by longest curve with a stderr note.
- Known judgment calls: exact payload-dict variable name in artifacts.py and the walkforward command's local structure may differ slightly from the sketches — the tests define the contract; adapt names, keep shapes.
