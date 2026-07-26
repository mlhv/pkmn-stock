# Parameterized Single-Strategy Backtest (Plan 2b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A reusable capability that runs a backtest of any registered strategy with user-chosen hyperparameters, starting cash, and initial holdings over a window — writing the existing artifact + registry record shape — exposed through a widened `pkmn backtest` CLI.

**Architecture:** Make strategy hyperparameters declarative (`ParamSpec`) so one list drives param validation, the CLI/web form (later), and a *derived* optuna search space. Add a single `run_single_backtest` capability (native engine only) that both the CLI and the future web endpoint call. Refactor the run registry so artifact directories are keyed on `run_id` (collision-free for repeated triggered runs).

**Tech Stack:** Python 3.12, typer CLI, optuna (seeded TPESampler), polars, the native `NativeBacktest` engine + `SeededHolding` (from Plan 2a), pytest, `uv`.

## Global Constraints

- All four gates pass before every commit: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`. (mypy/pytest need the `api` group: if `fastapi`/`pydantic` import errors appear, run `uv sync --group api` first.)
- **Native engine only.** This plan never constructs the Python `engine.backtest.Backtest`. `pkmn backtest` loses `--engine`. Do NOT delete `engine/backtest.py`/`engine/portfolio.py` — they are still the parity oracle (`tests/test_native_parity.py`), retired in the *next* plan. `test_native_parity.py` must stay green and untouched.
- **Reproducibility is non-negotiable.** Deriving the optuna space from `ParamSpec`s must produce bit-identical suggestions to the current hand-written `_*_space` functions (same names, bounds, `log`, and suggestion order). This is proven by a regression test before the old functions are removed.
- Frozen dataclasses for value objects. Follow existing idioms in `research/` and `cli.py`.
- Golden regression discipline: `tests/test_cli_backtest.py` pins exact engine numbers; they must not change (a deliberate shift requires a hand-derivation note in the same commit — but nothing here should shift them).

## ParamSpec reference (exact values — use verbatim)

Each strategy's `params` tuple is the tunable subset from its current `_*_space`, **in the same order**, with `default` = the strategy constructor's default, and `(low, high, log)` from the current space:

- **sealed-accumulation:** `min_drawdown`(float, 0.25, 0.10, 0.50), `take_profit`(float, 1.5, 1.2, 2.5), `min_age_days`(int, 60, 30, 180)
- **dip-buyer:** `dip_threshold`(float, 0.30, 0.10, 0.50), `hold_days`(int, 30, 7, 90), `take_profit`(float, 1.25, 1.05, 1.6)
- **xs-momentum:** `lookback_days`(int, 60, 14, 120), `top_n`(int, 10, 5, 25), `rebalance_days`(int, 30, 7, 60)
- **cost-aware-reversion:** `dip_window_days`(int, 30, 14, 90), `dip_threshold`(float, 0.25, 0.15, 0.50), `min_edge`(float, 0.05, 0.02, 0.15), `take_profit`(float, 1.25, 1.1, 1.6), `max_hold_days`(int, 120, 30, 180)
- **ml-ranker:** `horizon_days`(int, 30, 14, 60), `rebalance_days`(int, 30, 21, 90), `top_n`(int, 8, 3, 15), `train_days`(int, 365, 120, 540), `max_iter`(int, 100, 50, 300, **log=True**), `learning_rate`(float, 0.1, 0.03, 0.3, **log=True**), `min_samples_leaf`(int, 20, 10, 50)
- **ml-ranker-v2:** `horizon_days`(int, 30, 14, 60), `rebalance_days`(int, 30, 21, 90), `top_n`(int, 8, 3, 15), `train_days`(int, 365, 120, 540), `min_price`(float, 3.0, 1.0, 10.0), `min_samples_leaf`(int, 20, 10, 50)

---

### Task 1: Declarative `ParamSpec` + derived optuna space

**Files:**
- Modify: `src/pkmn_quant/research/registry.py`
- Create: `tests/research/test_space_reproducibility.py`
- Modify: `tests/research/test_registry.py`
- Modify: `tests/live/test_signals.py` (one `RegistryEntry(...)` construction)

**Interfaces:**
- Consumes: `optuna`, `Strategy`, the six strategy classes (unchanged imports).
- Produces:
  - `@dataclass(frozen=True) class ParamSpec: name: str; kind: Literal["int","float"]; default: float|int; low: float|int; high: float|int; log: bool = False`
  - `_space_from_params(params: tuple[ParamSpec, ...]) -> Callable[[optuna.Trial], Params]`
  - `RegistryEntry` now has fields `factory: Callable[[Params], Strategy]` and `params: tuple[ParamSpec, ...]`, and a `space` **property** returning the derived callable (so `entry.space(trial)` and `SearchSpec(space=entry.space)` both keep working). The six hand-written `_*_space` functions are removed.
  - `REGISTRY` entries carry the ParamSpec tuples above.

- [ ] **Step 1: Write the reproducibility regression test (frozen oracle)**

Create `tests/research/test_space_reproducibility.py`. It carries **verbatim copies** of the six current `_*_space` function bodies as the frozen oracle, and asserts the derived space reproduces them bit-for-bit under a seeded study. Copy each current space body from `registry.py` exactly (they are the pre-refactor source of truth):

```python
"""Bit-for-bit guard: the ParamSpec-derived optuna space must reproduce the
original hand-written spaces exactly, so past walk-forward runs remain
reproducible. The _OLD_* functions below are verbatim copies of the
pre-refactor registry._*_space bodies — the frozen oracle."""

import optuna
import pytest

from pkmn_quant.research.registry import REGISTRY, Params

optuna.logging.set_verbosity(optuna.logging.WARNING)


def _OLD_sealed(trial: optuna.Trial) -> Params:
    return {
        "min_drawdown": trial.suggest_float("min_drawdown", 0.10, 0.50),
        "take_profit": trial.suggest_float("take_profit", 1.2, 2.5),
        "min_age_days": trial.suggest_int("min_age_days", 30, 180),
    }


def _OLD_dip(trial: optuna.Trial) -> Params:
    return {
        "dip_threshold": trial.suggest_float("dip_threshold", 0.10, 0.50),
        "hold_days": trial.suggest_int("hold_days", 7, 90),
        "take_profit": trial.suggest_float("take_profit", 1.05, 1.6),
    }


def _OLD_momentum(trial: optuna.Trial) -> Params:
    return {
        "lookback_days": trial.suggest_int("lookback_days", 14, 120),
        "top_n": trial.suggest_int("top_n", 5, 25),
        "rebalance_days": trial.suggest_int("rebalance_days", 7, 60),
    }


def _OLD_reversion(trial: optuna.Trial) -> Params:
    return {
        "dip_window_days": trial.suggest_int("dip_window_days", 14, 90),
        "dip_threshold": trial.suggest_float("dip_threshold", 0.15, 0.50),
        "min_edge": trial.suggest_float("min_edge", 0.02, 0.15),
        "take_profit": trial.suggest_float("take_profit", 1.1, 1.6),
        "max_hold_days": trial.suggest_int("max_hold_days", 30, 180),
    }


def _OLD_ml_ranker(trial: optuna.Trial) -> Params:
    return {
        "horizon_days": trial.suggest_int("horizon_days", 14, 60),
        "rebalance_days": trial.suggest_int("rebalance_days", 21, 90),
        "top_n": trial.suggest_int("top_n", 3, 15),
        "train_days": trial.suggest_int("train_days", 120, 540),
        "max_iter": trial.suggest_int("max_iter", 50, 300, log=True),
        "learning_rate": trial.suggest_float("learning_rate", 0.03, 0.3, log=True),
        "min_samples_leaf": trial.suggest_int("min_samples_leaf", 10, 50),
    }


def _OLD_ml_ranker_v2(trial: optuna.Trial) -> Params:
    return {
        "horizon_days": trial.suggest_int("horizon_days", 14, 60),
        "rebalance_days": trial.suggest_int("rebalance_days", 21, 90),
        "top_n": trial.suggest_int("top_n", 3, 15),
        "train_days": trial.suggest_int("train_days", 120, 540),
        "min_price": trial.suggest_float("min_price", 1.0, 10.0),
        "min_samples_leaf": trial.suggest_int("min_samples_leaf", 10, 50),
    }


_OLD = {
    "sealed-accumulation": _OLD_sealed,
    "dip-buyer": _OLD_dip,
    "xs-momentum": _OLD_momentum,
    "cost-aware-reversion": _OLD_reversion,
    "ml-ranker": _OLD_ml_ranker,
    "ml-ranker-v2": _OLD_ml_ranker_v2,
}


def _trajectory(space, n=20, seed=7):
    """The exact sequence of param dicts a seeded TPESampler suggests."""
    study = optuna.create_study(sampler=optuna.samplers.TPESampler(seed=seed))
    out = []
    for _ in range(n):
        trial = study.ask()
        out.append(space(trial))
        study.tell(trial, 0.0)  # constant objective: isolates the space
    return out


@pytest.mark.parametrize("name", sorted(_OLD))
def test_derived_space_matches_old_bit_for_bit(name: str) -> None:
    old = _trajectory(_OLD[name])
    new = _trajectory(REGISTRY[name].space)
    assert new == old
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/research/test_space_reproducibility.py -v`
Expected: FAIL — `REGISTRY[name].space` is still the old attribute form / not yet param-derived, OR (after you start editing) an AttributeError. It must be RED before the refactor.

- [ ] **Step 3: Add `ParamSpec` and `_space_from_params` to registry.py**

At the top of `registry.py`, add the import `from typing import Literal` and after the `Params` type alias add:

```python
@dataclass(frozen=True)
class ParamSpec:
    name: str
    kind: Literal["int", "float"]
    default: float | int
    low: float | int
    high: float | int
    log: bool = False


def _space_from_params(params: tuple[ParamSpec, ...]) -> Callable[[optuna.Trial], Params]:
    """Derive a flat optuna space from declarative specs. Suggestion order is
    the tuple order — parity-relevant, the seeded sampler is order-sensitive."""

    def space(trial: optuna.Trial) -> Params:
        out: Params = {}
        for p in params:
            if p.kind == "int":
                out[p.name] = trial.suggest_int(p.name, int(p.low), int(p.high), log=p.log)
            else:
                out[p.name] = trial.suggest_float(p.name, float(p.low), float(p.high), log=p.log)
        return out

    return space
```

- [ ] **Step 4: Replace `RegistryEntry` with the params-driven form**

Replace the `RegistryEntry` dataclass with:

```python
@dataclass(frozen=True)
class RegistryEntry:
    factory: Callable[[Params], Strategy]
    params: tuple[ParamSpec, ...]

    @property
    def space(self) -> Callable[[optuna.Trial], Params]:
        return _space_from_params(self.params)
```

- [ ] **Step 5: Delete the six `_*_space` functions and rewrite `REGISTRY`**

Remove all six `_*_space` functions. Keep the six `_*_factory` functions unchanged. Rewrite `REGISTRY` so each entry passes its ParamSpec tuple (values from the ParamSpec reference table above), e.g.:

```python
REGISTRY: dict[str, RegistryEntry] = {
    "sealed-accumulation": RegistryEntry(
        factory=_sealed_factory,
        params=(
            ParamSpec("min_drawdown", "float", 0.25, 0.10, 0.50),
            ParamSpec("take_profit", "float", 1.5, 1.2, 2.5),
            ParamSpec("min_age_days", "int", 60, 30, 180),
        ),
    ),
    "dip-buyer": RegistryEntry(
        factory=_dip_factory,
        params=(
            ParamSpec("dip_threshold", "float", 0.30, 0.10, 0.50),
            ParamSpec("hold_days", "int", 30, 7, 90),
            ParamSpec("take_profit", "float", 1.25, 1.05, 1.6),
        ),
    ),
    "xs-momentum": RegistryEntry(
        factory=_momentum_factory,
        params=(
            ParamSpec("lookback_days", "int", 60, 14, 120),
            ParamSpec("top_n", "int", 10, 5, 25),
            ParamSpec("rebalance_days", "int", 30, 7, 60),
        ),
    ),
    "cost-aware-reversion": RegistryEntry(
        factory=_reversion_factory,
        params=(
            ParamSpec("dip_window_days", "int", 30, 14, 90),
            ParamSpec("dip_threshold", "float", 0.25, 0.15, 0.50),
            ParamSpec("min_edge", "float", 0.05, 0.02, 0.15),
            ParamSpec("take_profit", "float", 1.25, 1.1, 1.6),
            ParamSpec("max_hold_days", "int", 120, 30, 180),
        ),
    ),
    "ml-ranker": RegistryEntry(
        factory=_ml_ranker_factory,
        params=(
            ParamSpec("horizon_days", "int", 30, 14, 60),
            ParamSpec("rebalance_days", "int", 30, 21, 90),
            ParamSpec("top_n", "int", 8, 3, 15),
            ParamSpec("train_days", "int", 365, 120, 540),
            ParamSpec("max_iter", "int", 100, 50, 300, log=True),
            ParamSpec("learning_rate", "float", 0.1, 0.03, 0.3, log=True),
            ParamSpec("min_samples_leaf", "int", 20, 10, 50),
        ),
    ),
    "ml-ranker-v2": RegistryEntry(
        factory=_ml_ranker_v2_factory,
        params=(
            ParamSpec("horizon_days", "int", 30, 14, 60),
            ParamSpec("rebalance_days", "int", 30, 21, 90),
            ParamSpec("top_n", "int", 8, 3, 15),
            ParamSpec("train_days", "int", 365, 120, 540),
            ParamSpec("min_price", "float", 3.0, 1.0, 10.0),
            ParamSpec("min_samples_leaf", "int", 20, 10, 50),
        ),
    ),
}
```

- [ ] **Step 6: Fix the one test that constructs `RegistryEntry` with `space=`**

In `tests/live/test_signals.py` (~line 353), the monkeypatch builds `RegistryEntry(factory=lambda p: Recorder(), space=old.space)`. `space` is now a property, not a field. Change it to reuse the original params:

```python
        RegistryEntry(factory=lambda p: Recorder(), params=old.params),
```

- [ ] **Step 7: Run the reproducibility test — expect PASS**

Run: `uv run pytest tests/research/test_space_reproducibility.py -v`
Expected: PASS — all six strategies' derived spaces match the frozen oracle bit-for-bit.

- [ ] **Step 8: Run the registry + signals + walkforward-touching tests**

Run: `uv run pytest tests/research/test_registry.py tests/live/test_signals.py tests/strategies/test_ml_ranker_v2.py -v`
Expected: PASS — `entry.space(trial)` (property) and `entry.factory(params)` still work everywhere.

- [ ] **Step 9: Run the four gates**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`
Expected: all PASS.

- [ ] **Step 10: Commit**

```bash
git add src/pkmn_quant/research/registry.py tests/research/test_space_reproducibility.py tests/research/test_registry.py tests/live/test_signals.py
git commit -m "refactor(research): declarative ParamSpec; derive optuna space (bit-for-bit reproducible)"
```

---

### Task 2: `run_id`-before-artifacts refactor in the run registry

**Files:**
- Modify: `src/pkmn_quant/research/runs.py`
- Modify: `tests/research/test_runs.py`

**Interfaces:**
- Consumes: existing `record_run`.
- Produces:
  - `new_run_id() -> str` — the id format currently generated inside `record_run` (`"%Y%m%dT%H%M%SZ" + "-" + secrets.token_hex(3)`), extracted so a caller can know the id before writing artifacts.
  - `record_run(..., run_id: str | None = None)` — uses the supplied id if given, else `new_run_id()`. All existing callers (which pass no `run_id`) are unchanged.

- [ ] **Step 1: Write the failing test**

Add to `tests/research/test_runs.py`:

```python
def test_new_run_id_format() -> None:
    from pkmn_quant.research.runs import new_run_id

    rid = new_run_id()
    # "<YYYYMMDD>T<HHMMSS>Z-<6 hex>"
    assert rid[8] == "T" and rid[15] == "Z" and rid[16] == "-"
    assert len(rid) == 23


def test_record_run_uses_supplied_run_id(tmp_path: Path) -> None:
    from pkmn_quant.research.runs import load_runs, record_run

    wh = _warehouse(tmp_path)  # existing helper in this test module
    rid = record_run(
        root=tmp_path,
        command="backtest",
        strategy="buy-and-hold-sealed",
        config={"x": 1},
        results={"total_return": 0.0},
        artifact_path=tmp_path / "data" / "results" / "given-id",
        warehouse=wh,
        run_id="20250101T000000Z-abcdef",
    )
    assert rid == "20250101T000000Z-abcdef"
    assert load_runs(tmp_path)[-1].run_id == "20250101T000000Z-abcdef"
```

If `test_runs.py` has no `_warehouse` helper, use the same warehouse-construction the other tests in that file use (read the file first and match its fixture style).

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/research/test_runs.py -k "new_run_id or supplied_run_id" -v`
Expected: FAIL — `new_run_id` does not exist; `record_run` has no `run_id` param.

- [ ] **Step 3: Extract `new_run_id` and thread the optional param**

In `runs.py`, add above `record_run`:

```python
def new_run_id() -> str:
    """Timestamp + random suffix; unique per run, sortable by time."""
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)
```

Change `record_run`'s signature to add `run_id: str | None = None` (place it after `runtime`), and inside the `try`, replace the id line:

```python
        now = datetime.now(UTC)
        rid = run_id if run_id is not None else new_run_id()
```

then use `rid` for the record's `"run_id"` and the return value. (Keep `now` for `recorded_at`.)

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/research/test_runs.py -v`
Expected: PASS — new tests green, all existing run-registry tests unchanged.

- [ ] **Step 5: Run the four gates**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/pkmn_quant/research/runs.py tests/research/test_runs.py
git commit -m "feat(research): new_run_id() + record_run(run_id=) so artifact dirs can be keyed on the id"
```

---

### Task 3: The `run_single_backtest` capability

**Files:**
- Create: `src/pkmn_quant/research/backtest_run.py`
- Create: `tests/research/test_backtest_run.py`

**Interfaces:**
- Consumes: `REGISTRY`/`ParamSpec` (Task 1), `new_run_id`/`record_run` (Task 2), `NativeBacktest`/`NativeStrategySpec`/`SeededHolding` (Plan 2a, `engine.native`), `PreparedMarket` (`engine.prepared`), `CostModel`, `Warehouse`, `Asset`.
- Produces:
  - `@dataclass(frozen=True) class BacktestRunResult: run_id: str | None; result: Result; artifact_dir: Path`
  - `ParamValue = str | float | int` (a param may arrive as a string from the CLI or a number from the web).
  - `resolve_params(strategy_name: str, params: dict[str, ParamValue]) -> Params` — validates against the strategy's ParamSpecs (unknown key / out-of-bounds → `ValueError`), coerces per `kind` (accepts str **or** number), fills omitted with defaults. `buy-and-hold` accepts only an empty dict (else `ValueError`). (mypy checks `src/` only, so test dict literals are unconstrained; keep the `src/` types consistent to dodge dict invariance.)
  - `run_single_backtest(root, strategy_name, params, cash, holdings, start, end, *, impact=True, warmup_days=0, kind="sealed") -> BacktestRunResult`

- [ ] **Step 1: Write the failing tests**

Create `tests/research/test_backtest_run.py`. Reuse the synthetic-warehouse helper pattern from `tests/test_native_seeding.py` (a small 1-asset warehouse) plus a multi-asset one where needed. Full test code:

```python
"""Capability tests for run_single_backtest (Plan 2b). Native engine only."""

from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA
from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.native import SeededHolding
from pkmn_quant.engine.portfolio import Asset
from pkmn_quant.research.backtest_run import (
    BacktestRunResult,
    resolve_params,
    run_single_backtest,
)

START = date(2025, 1, 1)
PRODUCTS = pl.DataFrame(
    {
        "product_id": [1],
        "group_id": [1],
        "name": ["Box A"],
        "rarity": [None],
        "kind": ["sealed"],
        "released_on": [date(2024, 11, 1)],
    }
)


def _seed(root: Path, days: int = 6) -> None:
    w = Warehouse(Paths(root=root))
    for i in range(days):
        day = START + timedelta(days=i)
        market = 10.0 + i
        w.write_prices(
            day,
            pl.DataFrame(
                [
                    {
                        "date": day,
                        "product_id": 1,
                        "sub_type": "Normal",
                        "low": round(market * 0.9, 2),
                        "mid": round(market * 1.15, 2),
                        "high": round(market * 3.0, 2),
                        "market": market,
                    }
                ],
                schema=PRICE_SCHEMA,
            ),
        )
    w.write_products(PRODUCTS)


def _run(root: Path, strategy: str, params=None, holdings=None, cash=1000.0):
    return run_single_backtest(
        root=root,
        strategy_name=strategy,
        params=params or {},
        cash=cash,
        holdings=holdings or [],
        start=START,
        end=START + timedelta(days=5),
    )


# --- resolve_params -------------------------------------------------------

def test_resolve_params_fills_defaults() -> None:
    p = resolve_params("sealed-accumulation", {})
    assert p == {"min_drawdown": 0.25, "take_profit": 1.5, "min_age_days": 60}


def test_resolve_params_applies_override_and_coerces() -> None:
    p = resolve_params("sealed-accumulation", {"min_age_days": 90.0, "take_profit": 2.0})
    assert p["min_age_days"] == 90 and isinstance(p["min_age_days"], int)
    assert p["take_profit"] == 2.0


def test_resolve_params_rejects_unknown_key() -> None:
    with pytest.raises(ValueError, match="unknown"):
        resolve_params("sealed-accumulation", {"nope": 1})


def test_resolve_params_rejects_out_of_bounds() -> None:
    with pytest.raises(ValueError, match="out of range"):
        resolve_params("sealed-accumulation", {"min_drawdown": 0.99})


def test_resolve_params_buy_and_hold_takes_none() -> None:
    assert resolve_params("buy-and-hold", {}) == {}
    with pytest.raises(ValueError, match="no tunable"):
        resolve_params("buy-and-hold", {"x": 1})


def test_resolve_params_unknown_strategy() -> None:
    with pytest.raises(ValueError, match="unknown strategy"):
        resolve_params("does-not-exist", {})


# --- run_single_backtest --------------------------------------------------

def test_runs_rule_strategy_writes_artifacts_and_record(tmp_path: Path) -> None:
    _seed(tmp_path)
    out = _run(tmp_path, "sealed-accumulation")
    assert isinstance(out, BacktestRunResult)
    assert out.run_id is not None
    assert out.artifact_dir == tmp_path / "data" / "results" / out.run_id
    assert (out.artifact_dir / "equity.parquet").exists()
    assert (out.artifact_dir / "fills.parquet").exists()
    from pkmn_quant.research.runs import load_runs

    rec = load_runs(tmp_path)[-1]
    assert rec.run_id == out.run_id
    assert rec.config["strategy"] == "sealed-accumulation"
    assert rec.config["params"]["min_drawdown"] == 0.25


def test_runs_buy_and_hold_default(tmp_path: Path) -> None:
    _seed(tmp_path)
    out = _run(tmp_path, "buy-and-hold")
    assert out.run_id is not None
    assert out.result.summary  # has metrics


def test_runs_ml_strategy_via_bridge(tmp_path: Path) -> None:
    # ml-ranker runs through the Python callback bridge (not a native port).
    # Over this short window it never accumulates enough history to train, so
    # it trades little/nothing — but the bridge path is exercised and the run
    # completes. Params stay within ParamSpec bounds (defaults here).
    _seed(tmp_path, days=6)
    out = _run(tmp_path, "ml-ranker")
    assert out.run_id is not None


def test_seeded_holding_flows_through(tmp_path: Path) -> None:
    _seed(tmp_path)
    asset = Asset(product_id=1, sub_type="Normal")
    holding = SeededHolding(asset=asset, quantity=3, avg_cost=9.0, opened_on=date(2024, 12, 1))
    out = _run(tmp_path, "buy-and-hold", holdings=[holding], cash=100.0)
    # day-0: no fill yet, equity = 100 + 3*market_day0(10.0) = 130
    assert out.result.equity_curve["equity"].to_list()[0] == 130.0
    from pkmn_quant.research.runs import load_runs

    rec_holdings = load_runs(tmp_path)[-1].config["holdings"]
    assert rec_holdings == [
        {"product_id": 1, "sub_type": "Normal", "quantity": 3, "avg_cost": 9.0, "opened_on": "2024-12-01"}
    ]


def test_holding_outside_universe_raises(tmp_path: Path) -> None:
    _seed(tmp_path)
    ghost = Asset(product_id=999, sub_type="Normal")
    with pytest.raises(ValueError, match="universe"):
        _run(tmp_path, "buy-and-hold", holdings=[SeededHolding(ghost, 1, 5.0, START)])


def test_holding_without_day_one_mark_raises(tmp_path: Path) -> None:
    # Asset 2 first prints on day 3 — in-universe over the window, but no mark
    # on the start bar, so it cannot be a starting holding.
    _seed(tmp_path)
    w = Warehouse(Paths(root=tmp_path))
    late = START + timedelta(days=3)
    w.write_prices(
        late,
        pl.concat(
            [
                w.load_day(late),
                pl.DataFrame(
                    [
                        {
                            "date": late,
                            "product_id": 2,
                            "sub_type": "Normal",
                            "low": 9.0,
                            "mid": 11.5,
                            "high": 30.0,
                            "market": 10.0,
                        }
                    ],
                    schema=PRICE_SCHEMA,
                ),
            ]
        ),
    )
    with pytest.raises(ValueError, match="start"):
        _run(
            tmp_path,
            "buy-and-hold",
            holdings=[SeededHolding(Asset(2, "Normal"), 1, 5.0, START)],
        )
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/research/test_backtest_run.py -v`
Expected: FAIL — `pkmn_quant.research.backtest_run` does not exist.

- [ ] **Step 3: Implement the capability module**

Create `src/pkmn_quant/research/backtest_run.py`:

```python
"""Parameterized single-strategy backtest capability (Plan 2b).

One entry point the CLI and (later) the web trigger both call: build any
registered strategy from name + params, run it on the native engine with cash
+ initial holdings over a window, write artifacts + a registry record. Native
engine only — never the Python Backtest.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import polars as pl

from pkmn_quant.config import Paths
from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.backtest import Result
from pkmn_quant.engine.costs import CostModel
from pkmn_quant.engine.native import (
    NATIVE_STRATEGY_NAMES,
    NativeBacktest,
    NativeStrategySpec,
    SeededHolding,
)
from pkmn_quant.engine.prepared import PreparedMarket
from pkmn_quant.engine.strategy import Strategy
from pkmn_quant.research.registry import REGISTRY, Params
from pkmn_quant.research.runs import new_run_id, record_run

# A param may arrive as a string (CLI --param k=v) or a number (web JSON).
ParamValue = str | float | int


@dataclass(frozen=True)
class BacktestRunResult:
    run_id: str | None
    result: Result
    artifact_dir: Path


def resolve_params(strategy_name: str, params: dict[str, ParamValue]) -> Params:
    """Validate + coerce + default-fill user params against the strategy's
    ParamSpecs. Accepts str or numeric values. buy-and-hold takes none.
    Raises ValueError on any problem."""
    if strategy_name == "buy-and-hold":
        if params:
            raise ValueError("buy-and-hold has no tunable parameters")
        return {}
    entry = REGISTRY.get(strategy_name)
    if entry is None:
        raise ValueError(f"unknown strategy {strategy_name!r}; known: {sorted(REGISTRY)}")
    by_name = {p.name: p for p in entry.params}
    unknown = set(params) - set(by_name)
    if unknown:
        raise ValueError(f"unknown param(s) {sorted(unknown)} for {strategy_name}")
    out: Params = {}
    for spec in entry.params:
        raw = params.get(spec.name, spec.default)
        # int(float(raw)) handles str/float/int uniformly ("2.0" -> 2).
        val: float | int = int(float(raw)) if spec.kind == "int" else float(raw)
        if not (spec.low <= val <= spec.high):
            raise ValueError(
                f"{spec.name}={val} out of range [{spec.low}, {spec.high}] for {strategy_name}"
            )
        out[spec.name] = val
    return out


def _build_strategy(
    strategy_name: str, params: Params, kind: str
) -> NativeStrategySpec | Strategy:
    if strategy_name == "buy-and-hold":
        return NativeStrategySpec("buy-and-hold", {}, kind=kind)
    if strategy_name in NATIVE_STRATEGY_NAMES:
        return NativeStrategySpec(strategy_name, {k: float(v) for k, v in params.items()})
    # ml-ranker / ml-ranker-v2: Python Strategy via the callback bridge.
    return REGISTRY[strategy_name].factory(params)


def _validate_holdings(prepared: PreparedMarket, holdings: list[SeededHolding]) -> None:
    if not holdings:
        return
    # marks carried forward onto the first trading day of the window
    from datetime import date as _date, timedelta as _td

    day0 = _date(1970, 1, 1) + _td(days=int(prepared.trading_days[0]))
    marks = prepared.market.marks_on(day0)
    for h in holdings:
        if h.asset not in prepared.asset_index:
            raise ValueError(f"holding asset {h.asset} not in backtest universe")
        if h.asset not in marks:
            raise ValueError(
                f"holding asset {h.asset} has no price on/before window start {day0}"
            )


def _holdings_config(holdings: list[SeededHolding]) -> list[dict[str, object]]:
    rows = [
        {
            "product_id": h.asset.product_id,
            "sub_type": h.asset.sub_type,
            "quantity": h.quantity,
            "avg_cost": h.avg_cost,
            "opened_on": h.opened_on.isoformat(),
        }
        for h in holdings
    ]
    return sorted(rows, key=lambda r: (r["product_id"], r["sub_type"]))


def _write_artifacts(result: Result, run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    result.equity_curve.write_parquet(run_dir / "equity.parquet")
    pl.DataFrame(
        [
            {
                "day": f.day,
                "product_id": f.asset.product_id,
                "sub_type": f.asset.sub_type,
                "quantity": f.quantity,
                "price": f.price,
                "fees": f.fees,
                "impact": f.impact,
            }
            for f in result.fills
        ],
        schema={
            "day": pl.Date,
            "product_id": pl.Int64,
            "sub_type": pl.Utf8,
            "quantity": pl.Int64,
            "price": pl.Float64,
            "fees": pl.Float64,
            "impact": pl.Float64,
        },
    ).write_parquet(run_dir / "fills.parquet")


def run_single_backtest(
    root: Path,
    strategy_name: str,
    params: dict[str, ParamValue],
    cash: float,
    holdings: list[SeededHolding],
    start: date,
    end: date,
    *,
    impact: bool = True,
    warmup_days: int = 0,
    kind: str = "sealed",
) -> BacktestRunResult:
    resolved = resolve_params(strategy_name, params)
    wh = Warehouse(Paths(root=root))
    cm = CostModel(impact_enabled=impact)
    prepared = PreparedMarket.prepare(wh, start, end, warmup_days=warmup_days)
    _validate_holdings(prepared, holdings)
    strategy = _build_strategy(strategy_name, resolved, kind)

    result = NativeBacktest(
        warehouse=wh,
        strategy=strategy,
        cost_model=cm,
        start=start,
        end=end,
        initial_cash=cash,
        warmup_days=warmup_days,
        initial_holdings=holdings,
        prepared=prepared,
    ).run()

    run_id = new_run_id()
    run_dir = root / "data" / "results" / run_id
    _write_artifacts(result, run_dir)

    recorded = record_run(
        root=root,
        command="backtest",
        strategy=result.strategy_name,
        config={
            "command": "backtest",
            "strategy": strategy_name,
            "params": resolved,
            "holdings": _holdings_config(holdings),
            "cash": cash,
            "kind": kind if strategy_name == "buy-and-hold" else None,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "impact": impact,
            "warmup_days": warmup_days,
            "cost_model": cm.as_dict(),
        },
        results=result.summary,
        artifact_path=run_dir,
        warehouse=wh,
        run_id=run_id,
    )
    return BacktestRunResult(run_id=recorded, result=result, artifact_dir=run_dir)
```

Note: before implementing `_validate_holdings`, read `engine/prepared.py` and confirm the real `PreparedMarket` API — `trading_days` (int32 numpy array of epoch-days), `asset_index` (dict[Asset,int]), and `market.marks_on(date) -> dict[Asset, float]`. If a cleaner accessor for the first trading day or day-0 marks exists, prefer it; do not weaken the two checks (universe membership + day-0 mark).

- [ ] **Step 4: Run the capability tests**

Run: `uv run pytest tests/research/test_backtest_run.py -v`
Expected: PASS — all cases green. If `marks_on`/`asset_index`/`trading_days` differ from assumptions, adjust `_validate_holdings` to the real `PreparedMarket` API (do not weaken the two checks).

- [ ] **Step 5: Run the four gates**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/pkmn_quant/research/backtest_run.py tests/research/test_backtest_run.py
git commit -m "feat(research): run_single_backtest capability (any strategy + params + holdings, native engine)"
```

---

### Task 4: Widen the `pkmn backtest` CLI

**Files:**
- Modify: `src/pkmn_quant/cli.py` (the `backtest` command, ~lines 250-362)
- Modify: `tests/test_cli_backtest.py`

**Interfaces:**
- Consumes: `run_single_backtest`, `SeededHolding`, `Asset`.
- Produces: `pkmn backtest --strategy <name> [--param k=v ...] [--holdings <file.csv>] [--kind sealed|single] --start --end --cash [--impact/--no-impact] --root` — delegates to `run_single_backtest`; `--engine` removed.

- [ ] **Step 1: Write/adjust the failing tests**

In `tests/test_cli_backtest.py`:
- Remove the `@pytest.mark.parametrize("engine", ["python", "cpp"])` decorators on `test_backtest_golden_numbers` and `test_backtest_golden_numbers_with_impact`, drop the `engine` parameter and the `"--engine", engine` args from those tests (they now always run native). The golden numbers and the `next(iter(out_dir.iterdir()))` dir discovery stay unchanged.
- Update `test_default_engine_is_cpp_and_recorded` → rename to `test_backtest_recorded` and assert the run is recorded (a registry record exists with `command == "backtest"`); drop any `config["engine"]` assertion.
- Add:

```python
def test_backtest_strategy_and_param_override(tmp_path: Path) -> None:
    seed(tmp_path)
    result = run_cli(
        tmp_path, "--strategy", "sealed-accumulation", "--param", "take_profit=2.0"
    )
    assert result.exit_code == 0, result.output
    from pkmn_quant.research.runs import load_runs

    rec = load_runs(tmp_path)[-1]
    assert rec.config["strategy"] == "sealed-accumulation"
    assert rec.config["params"]["take_profit"] == 2.0


def test_backtest_bad_param_clean_error(tmp_path: Path) -> None:
    seed(tmp_path)
    result = run_cli(tmp_path, "--strategy", "sealed-accumulation", "--param", "nope=1")
    assert result.exit_code != 0
    assert "Traceback" not in result.output


def test_backtest_holdings_file(tmp_path: Path) -> None:
    seed(tmp_path)
    hfile = tmp_path / "holdings.csv"
    hfile.write_text(
        "product_id,sub_type,quantity,avg_cost,opened_on\n1,Normal,2,9.0,2025-05-01\n"
    )
    result = run_cli(tmp_path, "--holdings", str(hfile))
    assert result.exit_code == 0, result.output


def test_backtest_engine_flag_removed(tmp_path: Path) -> None:
    seed(tmp_path)
    result = run_cli(tmp_path, "--engine", "python")
    assert result.exit_code != 0  # unknown option
```

Note: `seed()` writes product 1 as `sub_type` per `price_row` — confirm the helper's sub_type (read `tests/helpers.py`) and match it in the holdings CSV (use whatever `price_row` emits; if it is `"Normal"`, the above is correct).

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cli_backtest.py -v`
Expected: FAIL — new options not present; `--engine` still accepted; parametrized tests error on the removed param.

- [ ] **Step 3: Rewrite the `backtest` command**

Replace the `backtest` command body in `cli.py` with a thin delegator. Parse `--param k=v` into a dict (values stay strings; `resolve_params` coerces), parse the optional holdings CSV into `list[SeededHolding]`, call `run_single_backtest`, print the summary:

```python
@app.command()
def backtest(
    start: str = typer.Option(..., help="Backtest start date (YYYY-MM-DD)."),
    end: str = typer.Option(..., help="Backtest end date (YYYY-MM-DD)."),
    strategy: str = typer.Option("buy-and-hold", help="Strategy name (see registry) or buy-and-hold."),
    param: list[str] = typer.Option(
        [], "--param", help="Hyperparameter override k=v (repeatable)."
    ),
    holdings: Path | None = typer.Option(
        None, help="CSV of starting holdings: product_id,sub_type,quantity,avg_cost,opened_on."
    ),
    cash: float = typer.Option(10_000.0, help="Initial cash."),
    kind: str = typer.Option("sealed", help="Universe for buy-and-hold: sealed|single."),
    impact: bool = typer.Option(
        True, "--impact/--no-impact", help="Walk-the-spread market impact on fills."
    ),
    root: Path = typer.Option(Path("."), help="Project root holding the data/ directory."),
) -> None:
    """Run a single backtest of any registered strategy with cash + holdings."""
    from pkmn_quant.engine.native import SeededHolding
    from pkmn_quant.engine.portfolio import Asset
    from pkmn_quant.research.backtest_run import run_single_backtest

    try:
        start_date = dt.date.fromisoformat(start)
        end_date = dt.date.fromisoformat(end)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if kind not in ("sealed", "single"):
        raise typer.BadParameter(f"unknown kind {kind!r}; choose sealed or single")

    params: dict[str, str | float | int] = {}
    for item in param:
        if "=" not in item:
            raise typer.BadParameter(f"--param must be k=v, got {item!r}")
        k, v = item.split("=", 1)
        params[k.strip()] = v.strip()  # resolve_params coerces per ParamSpec.kind

    seeded: list[SeededHolding] = []
    if holdings is not None:
        seeded = _read_holdings_csv(holdings)

    try:
        out = run_single_backtest(
            root=root,
            strategy_name=strategy,
            params=params,
            cash=cash,
            holdings=seeded,
            start=start_date,
            end=end_date,
            impact=impact,
            kind=kind,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    result = out.result
    if out.run_id is not None:
        typer.echo(f"run recorded: {out.run_id}")
    typer.echo(f"strategy: {result.strategy_name}  ({len(result.fills)} fills)")
    for key, value in result.summary.items():
        typer.echo(f"{key}: {value:.4f}")
    typer.echo(f"results written to {out.artifact_dir}")
```

And add a module-level helper in `cli.py` (near the other helpers):

```python
def _read_holdings_csv(path: Path) -> list["SeededHolding"]:
    import csv

    from pkmn_quant.engine.native import SeededHolding
    from pkmn_quant.engine.portfolio import Asset

    out: list[SeededHolding] = []
    with path.open() as fh:
        for r in csv.DictReader(fh):
            out.append(
                SeededHolding(
                    asset=Asset(product_id=int(r["product_id"]), sub_type=r["sub_type"]),
                    quantity=int(r["quantity"]),
                    avg_cost=float(r["avg_cost"]),
                    opened_on=dt.date.fromisoformat(r["opened_on"]),
                )
            )
    return out
```

The CLI `params` dict is typed `dict[str, str | float | int]` (same value type `resolve_params` accepts, so no dict-invariance error), and values arrive as strings — `resolve_params` already coerces via `int(float(raw))`/`float(raw)` (Task 3), so `"2.0"` and `2.0` both work.

- [ ] **Step 4: Run the CLI tests**

Run: `uv run pytest tests/test_cli_backtest.py -v`
Expected: PASS — goldens unchanged, new strategy/param/holdings/removed-engine cases green.

- [ ] **Step 5: Run the four gates**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/pkmn_quant/cli.py tests/test_cli_backtest.py
git commit -m "feat(cli): pkmn backtest --strategy/--param/--holdings; remove --engine (native only)"
```

---

### Task 5: Docs + full verification

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: everything from Tasks 1-4.
- Produces: a green full suite and a docs note describing the widened backtest command + the param-driven registry.

- [ ] **Step 1: Update CLAUDE.md**

In the `## Commands` block, replace the buy-and-hold-only `pkmn backtest` example line with the parameterized form, e.g.:

```
uv run pkmn backtest --strategy sealed-accumulation --start 2024-03-01 \
    --end 2026-06-30 --param take_profit=2.0 --holdings holdings.csv   # any strategy + cash/holdings
```

In the `research/` layout bullet, append a sentence to the `registry.py` description noting params are now declarative `ParamSpec`s (single source for validation, the derived optuna space, and the future web form), and add a sentence pointing to `research/backtest_run.py` (`run_single_backtest` — the parameterized single-strategy capability the CLI and future web trigger both call; native engine only). Note `pkmn backtest` is no longer buy-and-hold-only and `--engine` is gone.

- [ ] **Step 2: Full suite from clean + gates**

Run: `uv sync --group api && uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: full suite green (3 dashboard tests skip without the dashboard group); all gates pass.

- [ ] **Step 3: Sanity-check the C++ suite is unaffected**

Run: `ctest --test-dir cpp/build --output-on-failure` (build dir already configured; no cpp/ changes in this plan, so this just confirms no accidental coupling)
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: parameterized pkmn backtest + declarative registry params (Plan 2b)"
```

---

## Self-review notes

- **Spec coverage:** ParamSpec + derived-space DRY + reproducibility guard (Task 1) ✓; run_id-keyed dirs via runs.py refactor (Task 2) ✓; capability with strategy→native-path mapping, param validation, holdings validation (universe + day-one mark), artifacts + record (Task 3) ✓; widened CLI with `--engine` removed (Task 4) ✓; docs + verification (Task 5) ✓; Python engine not deleted, parity suite untouched ✓.
- **Type consistency:** `ParamSpec(name, kind, default, low, high, log)` identical across registry.py, the reference table, and REGISTRY construction. `run_single_backtest`/`resolve_params`/`BacktestRunResult` signatures identical between Task 3 definition and Task 4 call site. `record_run(run_id=)` (Task 2) matches the capability's call (Task 3). `entry.space` stays a callable attribute (now a property) for `search.py`/`cli.py`/tests.
- **Reproducibility:** the frozen-oracle test (Task 1) proves the derived space is bit-identical before the old functions are deleted; the CLI goldens (Task 4) prove engine numbers are unchanged.
- **CLI string coercion / mypy invariance:** `resolve_params` and `run_single_backtest` take `dict[str, ParamValue]` (`ParamValue = str | float | int`); the CLI declares its dict as the same value type so no dict-invariance error; int specs coerce via `int(float(raw))`. mypy checks `src/` only, so test dict literals are unconstrained.
- **Implementer must verify the `PreparedMarket` API** (`trading_days`, `asset_index`, `market.marks_on`) against `engine/prepared.py` before wiring `_validate_holdings`, adjusting to the real accessors without weakening the two holdings checks.
