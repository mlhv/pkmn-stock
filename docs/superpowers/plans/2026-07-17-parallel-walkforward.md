# Plan 11: Parallel Walk-Forward Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run walk-forward folds concurrently on a thread pool (C++ engine releases the GIL), hoist the per-trial data preparation to once-per-fold-window, and flip the default engine to cpp — with results bit-identical to today's serial runs.

**Architecture:** `run_walkforward` loads the warehouse once and submits one *fold worker* per fold to a `ThreadPoolExecutor`; each worker runs its seeded optuna study sequentially against two cached `PreparedMarket` windows (IS + OOS) and returns a `FoldResult`. The nanobind binding wraps the native-strategy engine call in `nb::gil_scoped_release`; the callback-bridge path keeps the GIL. Fold independence (fresh seeded sampler per fold) makes parallel results provably identical to serial.

**Tech Stack:** `concurrent.futures.ThreadPoolExecutor`, nanobind `gil_scoped_release`, existing C++20 core (no engine-core changes), polars/numpy.

**Spec:** `docs/superpowers/specs/2026-07-17-parallel-walkforward-design.md`. Read it before starting any task.

## Global Constraints

- **Bit-identical results**: a parallel run must equal its serial run exactly — stitched curve, per-fold params, all summaries, `==` never approx. `--workers 1 --engine python` must reproduce pre-Plan-11 behavior exactly.
- **Fold-level parallelism only.** Never parallelize trials within a fold (the seeded TPE sampler's trajectory must stay sequential).
- **The Python engine stays byte-for-byte untouched** (it is the reference): no changes to `engine/backtest.py`, `engine/data.py` beyond the parity-inert `from_frame` refactor, no shared/mutable state introduced on the python path.
- **GIL-release invariant**: no Python object is touched inside the released region; the released region is exactly one C++ `run_backtest` call on C++-only data. Bridge path (callback is not None) never releases.
- **Data-prep hoist is cpp-path only.** `PreparedMarket` arrays are immutable; its `MarketData` (bridge-only) is shared only across *sequential* runs within one fold worker thread, never across threads.
- Workers semantics: `0` = auto = `min(n_folds, os.cpu_count() or 1)`; `1` = plain serial loop bypassing the executor; negative → error (library `ValueError`, CLI clean `typer.BadParameter`).
- Registry: `workers` recorded in a new top-level `runtime` field on the run record — **never inside `config`** (identical results ⇒ identical config hash regardless of worker count). `engine` stays inside `config` as in Plan 10.
- Default engine flips `python` → `cpp` on BOTH `backtest` and `walkforward`; `--engine python` remains fully supported and CI-covered.
- All four gates before every commit: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`. After any `cpp/` change: `uv sync --reinstall-package pkmn-quant` before pytest, and `cmake --build cpp/build -j && ctest --test-dir cpp/build --output-on-failure`.
- Never `-ffast-math` / fp-contract anywhere (unchanged Plan 10 rule).
- Workflow: STOP after each completed task, explain at intern level, wait for explicit green light (CLAUDE.md).
- Branch: `feat/parallel-walkforward` (already created; spec committed).
- Current baselines: pytest `331 passed, 1 skipped`; ctest 24/24.

## File Map

Created:
- `src/pkmn_quant/engine/prepared.py` — `PreparedMarket` (frozen dataclass + `prepare()` classmethod)
- `cpp/tests/test_concurrency.cpp` — Catch2 std::thread smoke over the core
- `tests/engine/test_from_frame.py` — `MarketData.from_frame` ≡ `from_warehouse`
- `tests/research/test_walkforward_parallel.py` — serial≡parallel equivalence suite
- `scripts/bench_walkforward.py` — real-data wall-clock table + built-in serial≡parallel check

Modified:
- `src/pkmn_quant/engine/data.py` — `from_frame` classmethod; `from_warehouse` delegates
- `src/pkmn_quant/engine/native.py` — `NativeBacktest.prepared` field; prep code moves to `prepared.py`
- `cpp/bindings/module.cpp` — `gil_scoped_release` on the native path
- `cpp/CMakeLists.txt` — add `tests/test_concurrency.cpp`
- `src/pkmn_quant/research/walkforward.py` — fold workers + `workers` param + shared load
- `src/pkmn_quant/research/runs.py` — `runtime` field on records
- `src/pkmn_quant/cli.py` — `--workers`, both `--engine` defaults → `"cpp"`
- `tests/test_cli_walkforward.py`, `tests/test_native_parity.py` — new CLI/hoist tests
- `docs/research-findings-2026-07.md`, `README.md`, `CLAUDE.md` — Task 6

---

### Task 1: `MarketData.from_frame` — parity-inert refactor

Lets callers supply an already-loaded price frame so the walkforward can load parquet once instead of ~300 times. `from_warehouse` becomes load-then-delegate; every downstream byte identical.

**Files:**
- Modify: `src/pkmn_quant/engine/data.py` (the `from_warehouse` classmethod)
- Test: `tests/engine/test_from_frame.py` (create; `tests/engine/` already exists — check with `ls tests/engine/` and create the dir if it does not)

**Interfaces:**
- Produces: `MarketData.from_frame(frame: pl.DataFrame, start: date, end: date, warmup_days: int = 0) -> MarketData` — `frame` is the FULL price frame (`warehouse.load_prices()` shape); the method applies the same `[start - warmup_days, end]` filter `from_warehouse` applies today. `from_warehouse(warehouse, start, end, warmup_days=0)` keeps its exact signature and behavior.

- [ ] **Step 1: Write the failing test**

`tests/engine/test_from_frame.py`:

```python
"""from_frame(load_prices(), ...) must be indistinguishable from from_warehouse(...)."""

from datetime import date
from pathlib import Path

import polars as pl

from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA
from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.data import MarketData
from tests.helpers import price_row

D = [date(2025, 6, 1), date(2025, 6, 2), date(2025, 6, 4)]  # gap on 06-03


def seed(root: Path) -> Warehouse:
    w = Warehouse(Paths(root=root))
    for i, day in enumerate(D):
        rows = [price_row(day, 1, 10.0 + i), price_row(day, 2, 50.0 - i)]
        w.write_prices(day, pl.DataFrame(rows, schema=PRICE_SCHEMA))
    return w


def test_from_frame_matches_from_warehouse(tmp_path: Path) -> None:
    w = seed(tmp_path)
    a = MarketData.from_warehouse(w, D[0], D[-1], warmup_days=0)
    b = MarketData.from_frame(w.load_prices(), D[0], D[-1], warmup_days=0)
    assert a.days == b.days
    assert a.frame.equals(b.frame)
    assert a.mark_events() == b.mark_events()
    for day in a.days:
        assert a.prices_on(day) == b.prices_on(day)
        assert a.marks_on(day) == b.marks_on(day)


def test_from_frame_applies_warmup_filter(tmp_path: Path) -> None:
    w = seed(tmp_path)
    a = MarketData.from_warehouse(w, D[1], D[-1], warmup_days=1)
    b = MarketData.from_frame(w.load_prices(), D[1], D[-1], warmup_days=1)
    assert a.days == b.days  # trading days exclude the warm-up day
    assert a.frame.equals(b.frame)  # frame includes it
    assert a.mark_events() == b.mark_events()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/engine/test_from_frame.py -v`
Expected: FAIL — `AttributeError: ... has no attribute 'from_frame'` (or AttributeError on the classmethod lookup).

- [ ] **Step 3: Refactor from_warehouse into from_frame + delegate**

In `src/pkmn_quant/engine/data.py`, rename the body of `from_warehouse` to `from_frame` and add a thin `from_warehouse`. The ONLY body change is the first statement (`warehouse.load_prices()` → the `frame` parameter); keep every other line, comment, and docstring paragraph byte-identical:

```python
    @classmethod
    def from_frame(
        cls,
        prices: pl.DataFrame,
        start: date,
        end: date,
        warmup_days: int = 0,
    ) -> MarketData:
        """Build the view from an already-loaded full price frame.

        ``prices`` is the ``warehouse.load_prices()`` frame (or an equal
        one); the same ``[start - warmup_days, end]`` filter is applied
        here, so ``from_frame(load_prices(), ...)`` is byte-identical to
        ``from_warehouse(...)``. Public so the walk-forward layer can load
        parquet once and slice per fold instead of re-reading per run.

        See from_warehouse for the warm-up semantics.
        """
        load_from = start - timedelta(days=warmup_days) if warmup_days > 0 else start
        frame = prices.filter((pl.col("date") >= load_from) & (pl.col("date") <= end))
        # ... [REST OF THE CURRENT from_warehouse BODY, UNCHANGED: the
        # all_dates/days computation, marks_compact expression, marks_rows,
        # frame_by_day, quotes_by_day, cursor, and the cls(...) return] ...

    @classmethod
    def from_warehouse(
        cls,
        warehouse: Warehouse,
        start: date,
        end: date,
        warmup_days: int = 0,
    ) -> MarketData:
        """Load prices from ``start - warmup_days`` through ``end``.

        [KEEP THE CURRENT DOCSTRING UNCHANGED]
        """
        return cls.from_frame(warehouse.load_prices(), start, end, warmup_days=warmup_days)
```

(The `# ... [REST ...] ...` marker above means: move the existing lines verbatim — do not retype them from this plan; they are already correct in the file.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/engine/test_from_frame.py tests/test_native_parity.py -q`
Expected: all PASS (the parity suite proves the refactor changed nothing downstream).

- [ ] **Step 5: Full gates, commit**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy
git add src/pkmn_quant/engine/data.py tests/engine/test_from_frame.py
git commit -m "refactor: MarketData.from_frame — load once, slice per window (parity-inert)"
```

---

### Task 2: `PreparedMarket` — the once-per-window data cache

Moves the flatten/intern code out of `NativeBacktest.run()` into a frozen, reusable object. One `PreparedMarket` serves every backtest over the same `(start, end, warmup_days)` window.

**Files:**
- Create: `src/pkmn_quant/engine/prepared.py`
- Modify: `src/pkmn_quant/engine/native.py` (run() consumes a PreparedMarket; prep code moves out)
- Test: `tests/test_native_parity.py` (append)

**Interfaces:**
- Consumes: `MarketData.from_frame` (Task 1).
- Produces:
  - `PreparedMarket` frozen dataclass with fields `start: date`, `end: date`, `warmup_days: int`, `market: MarketData`, `products: pl.DataFrame`, `asset_list: list[Asset]`, `asset_index: dict[Asset, int]`, and the numpy arrays `trading_days, row_day, row_asset, row_market, row_mid, row_low, ev_day, ev_asset, ev_price, prod_id, prod_kind, prod_released`.
  - `PreparedMarket.prepare(warehouse: Warehouse, start: date, end: date, warmup_days: int = 0, *, frame: pl.DataFrame | None = None, products: pl.DataFrame | None = None) -> PreparedMarket` — `frame`/`products` let Task 4 pass the once-loaded shared copies; `None` loads from the warehouse.
  - `NativeBacktest` gains field `prepared: PreparedMarket | None = None`; a mismatched window raises `ValueError`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_native_parity.py`:

```python
def test_prepared_market_reuse_is_bit_identical(tmp_path: Path) -> None:
    """One PreparedMarket across repeated native runs == fresh loads."""
    from pkmn_quant.engine.prepared import PreparedMarket

    seed_rich(tmp_path)
    wh = Warehouse(Paths(root=tmp_path))
    cm = CostModel(impact_enabled=True)
    end = START + timedelta(days=39)
    spec = NativeStrategySpec(
        "dip-buyer",
        {"dip_window_days": 5.0, "dip_threshold": 0.10, "hold_days": 7.0, "take_profit": 1.05},
    )
    fresh = NativeBacktest(
        warehouse=wh, strategy=spec, cost_model=cm,
        start=START, end=end, initial_cash=1000.0,
    ).run()
    prepared = PreparedMarket.prepare(wh, START, end)
    first = NativeBacktest(
        warehouse=wh, strategy=spec, cost_model=cm,
        start=START, end=end, initial_cash=1000.0, prepared=prepared,
    ).run()
    second = NativeBacktest(  # same PreparedMarket again: reuse must not drift
        warehouse=wh, strategy=spec, cost_model=cm,
        start=START, end=end, initial_cash=1000.0, prepared=prepared,
    ).run()
    assert len(fresh.fills) > 0
    assert_results_equal(fresh, first)
    assert_results_equal(fresh, second)


def test_prepared_market_bridge_reuse_is_bit_identical(tmp_path: Path) -> None:
    """The callback bridge sharing one PreparedMarket (sequentially) == fresh."""
    from pkmn_quant.engine.prepared import PreparedMarket
    from pkmn_quant.strategies.dip_buyer import DipBuyer

    seed_rich(tmp_path)
    wh = Warehouse(Paths(root=tmp_path))
    cm = CostModel(impact_enabled=True)
    end = START + timedelta(days=39)

    def make() -> DipBuyer:
        return DipBuyer(
            dip_window_days=5, dip_threshold=0.10, hold_days=7,
            take_profit=1.05, max_positions=5, budget_frac=0.4, min_price=3.0,
        )

    fresh = NativeBacktest(
        warehouse=wh, strategy=make(), cost_model=cm,
        start=START, end=end, initial_cash=1000.0,
    ).run()
    prepared = PreparedMarket.prepare(wh, START, end)
    first = NativeBacktest(
        warehouse=wh, strategy=make(), cost_model=cm,
        start=START, end=end, initial_cash=1000.0, prepared=prepared,
    ).run()
    # second run rewinds the shared marks cursor (day < watermark) — must replay
    second = NativeBacktest(
        warehouse=wh, strategy=make(), cost_model=cm,
        start=START, end=end, initial_cash=1000.0, prepared=prepared,
    ).run()
    assert len(fresh.fills) > 0
    assert_results_equal(fresh, first)
    assert_results_equal(fresh, second)


def test_prepared_market_window_mismatch_raises(tmp_path: Path) -> None:
    from pkmn_quant.engine.prepared import PreparedMarket

    seed_rich(tmp_path)
    wh = Warehouse(Paths(root=tmp_path))
    prepared = PreparedMarket.prepare(wh, START, START + timedelta(days=20))
    with pytest.raises(ValueError, match="window"):
        NativeBacktest(
            warehouse=wh,
            strategy=NativeStrategySpec("buy-and-hold", {}),
            cost_model=CostModel(),
            start=START,
            end=START + timedelta(days=39),  # different end than prepared
            initial_cash=100.0,
            prepared=prepared,
        ).run()


def test_prepare_accepts_preloaded_frame(tmp_path: Path) -> None:
    """frame=/products= (the walkforward shared-load path) == warehouse loads."""
    from pkmn_quant.engine.prepared import PreparedMarket

    seed_rich(tmp_path)
    wh = Warehouse(Paths(root=tmp_path))
    end = START + timedelta(days=39)
    a = PreparedMarket.prepare(wh, START, end)
    b = PreparedMarket.prepare(
        wh, START, end, frame=wh.load_prices(), products=wh.load_products()
    )
    assert a.asset_list == b.asset_list
    assert (a.row_day == b.row_day).all()
    assert (a.row_market == b.row_market).all()
    assert (a.ev_price == b.ev_price).all()
    assert (a.prod_kind == b.prod_kind).all()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_native_parity.py -k prepared -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pkmn_quant.engine.prepared'`.

- [ ] **Step 3: Write prepared.py**

`src/pkmn_quant/engine/prepared.py` — the code below is the flatten/intern block MOVED from `native.py` `run()` (native.py:94-150 as of HEAD; move, don't fork — Step 4 deletes it there):

```python
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

    [MOVE the existing docstring from native.py:_kind_code verbatim —
    it documents the uncataloged-assets semantics.]
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

        # [MOVED VERBATIM from native.py run(): the frame sort, assets_df,
        # asset_list/asset_index, joined + row_* arrays, mark events ev_*,
        # prod_info + prod_id/prod_kind/prod_released, trading_days —
        # exactly as they are in native.py today, with `self.` references
        # replaced by the local names above and `products` replaced by
        # `products_df`.]

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
```

(Bracketed MOVE markers = relocate the existing, already-reviewed lines; retyping them from scratch risks drift. `_to_day`, `_NULL_DAY`, `_KIND_CODES`, `_kind_code` move here as the canonical home.)

- [ ] **Step 4: Rewire native.py**

In `src/pkmn_quant/engine/native.py`:
- Add field `prepared: PreparedMarket | None = None` to `NativeBacktest` (import `PreparedMarket` from `pkmn_quant.engine.prepared`; also re-export `_KIND_CODES`/`_kind_code` FROM prepared to avoid two copies — native.py keeps using them for the buy-and-hold kind validation via `from pkmn_quant.engine.prepared import _KIND_CODES`).
- `run()` starts:

```python
    def run(self) -> Result:
        p = self.prepared
        if p is None:
            p = PreparedMarket.prepare(
                self.warehouse, self.start, self.end, warmup_days=self.warmup_days
            )
        elif (p.start, p.end, p.warmup_days) != (self.start, self.end, self.warmup_days):
            raise ValueError(
                "PreparedMarket window mismatch: prepared for "
                f"({p.start}, {p.end}, warmup={p.warmup_days}), run wants "
                f"({self.start}, {self.end}, warmup={self.warmup_days})"
            )
```

- Delete the moved prep block from `run()`; every later reference becomes `p.asset_list`, `p.asset_index`, `p.market`, `p.products`, `p.row_day`, etc. The `_engine.run_backtest(...)` call site passes `p.*` arrays; the bridge callback closes over `p.market` / `p.products` / `p.asset_list` / `p.asset_index`. `_from_day` stays in native.py (used for output conversion).
- Keep `_EPOCH`/`_from_day` in native.py; delete native.py's now-unused `_to_day`, `_NULL_DAY`, `_kind_code` definitions (they live in prepared.py).

- [ ] **Step 5: Run the new tests + the whole parity suite, then gates**

```bash
uv run pytest tests/test_native_parity.py -q     # every existing differential test still exact
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Expected: all green — the refactor path (prepared=None) must leave every Plan 10 test untouched.

- [ ] **Step 6: Commit**

```bash
git add src/pkmn_quant/engine/prepared.py src/pkmn_quant/engine/native.py tests/test_native_parity.py
git commit -m "feat: PreparedMarket — once-per-window data prep, reusable across runs"
```

---

### Task 3: GIL release in the binding + concurrency smoke tests

The two-line change that makes threads real, plus the tests that prove the core is safe under them.

**Files:**
- Modify: `cpp/bindings/module.cpp` (the `run_backtest` call site), `cpp/CMakeLists.txt`
- Create: `cpp/tests/test_concurrency.cpp`
- Test: `cpp/tests/test_concurrency.cpp`, plus a Python thread smoke appended to `tests/test_native_parity.py`

**Interfaces:**
- Consumes: existing `run_backtest_py` structure in module.cpp (inputs already copied to `std::vector`s before the engine call — the precondition for releasing).
- Produces: no API change. Semantics: the native-strategy path (`callback.is_none()`) runs the C++ engine with the GIL released; the bridge path is untouched.

- [ ] **Step 1: Write the failing Catch2 test**

`cpp/tests/test_concurrency.cpp`:

```cpp
// Core thread-safety with no Python involved: two engines on separate
// MarketViews in std::threads must reproduce their serial results exactly.
#include <catch2/catch_test_macros.hpp>

#include <thread>
#include <vector>

#include "pkmn_engine/backtest.hpp"
#include "pkmn_engine/strategies/buy_and_hold.hpp"

using namespace pkmn;

namespace {
// Same shape as the golden fixture (test_backtest_golden.cpp), offset per id
// so the two threads run genuinely different data.
MarketView make_view(double base) {
    std::vector<Day> days{100, 101, 102};
    std::vector<PriceRow> rows{{100, 0, base, base * 1.3, 1.0},
                               {101, 0, base * 1.2, base * 1.6, 1.0},
                               {102, 0, base * 1.5, base * 1.8, 1.0}};
    std::vector<MarkEvent> events{
        {100, 0, base}, {101, 0, base * 1.2}, {102, 0, base * 1.5}};
    return MarketView(1, days, rows, events);
}

BacktestResult run_one(double base) {
    auto mkt = make_view(base);
    ProductTable prods{{1}, {0}, {100}};
    CostModel cm;
    cm.impact_enabled = true;
    BuyAndHold strat(0);
    return run_backtest(mkt, prods, strat, cm, 100.0);
}
}  // namespace

TEST_CASE("run_backtest is thread-safe across independent instances") {
    BacktestResult serial_a = run_one(10.0);
    BacktestResult serial_b = run_one(20.0);

    BacktestResult threaded_a, threaded_b;
    std::thread ta([&] { threaded_a = run_one(10.0); });
    std::thread tb([&] { threaded_b = run_one(20.0); });
    ta.join();
    tb.join();

    REQUIRE(threaded_a.equity == serial_a.equity);
    REQUIRE(threaded_b.equity == serial_b.equity);
    REQUIRE(threaded_a.fills.size() == serial_a.fills.size());
    REQUIRE(threaded_b.fills.size() == serial_b.fills.size());
}
```

Add `tests/test_concurrency.cpp` to `engine_tests` in `cpp/CMakeLists.txt`. If the linker complains about threads on any platform, add `find_package(Threads REQUIRED)` and link `Threads::Threads` to `engine_tests`.

Run: `cmake --build cpp/build -j` → the new file must compile; then `ctest --test-dir cpp/build --output-on-failure` → new test passes (the core has no shared state; this pins it).

- [ ] **Step 2: Write the failing-by-absence Python thread smoke**

Append to `tests/test_native_parity.py` (it passes even before the GIL change — the GIL serializes but does not corrupt; its role is regression-pinning the threaded path forever):

```python
def test_native_runs_are_thread_safe(tmp_path: Path) -> None:
    """Two concurrent NativeBacktest runs == their serial results, exactly."""
    from concurrent.futures import ThreadPoolExecutor

    seed_rich(tmp_path)
    wh = Warehouse(Paths(root=tmp_path))
    cm = CostModel(impact_enabled=True)
    spec = NativeStrategySpec(
        "dip-buyer",
        {"dip_window_days": 5.0, "dip_threshold": 0.10, "hold_days": 7.0, "take_profit": 1.05},
    )

    def run(end_offset: int) -> Result:
        return NativeBacktest(
            warehouse=wh, strategy=spec, cost_model=cm,
            start=START, end=START + timedelta(days=end_offset), initial_cash=1000.0,
        ).run()

    serial = [run(30), run(39)]
    with ThreadPoolExecutor(max_workers=2) as ex:
        threaded = list(ex.map(run, [30, 39]))
    for s, t in zip(serial, threaded, strict=True):
        assert_results_equal(s, t)
```

- [ ] **Step 3: Release the GIL on the native path**

In `cpp/bindings/module.cpp`, replace the single engine invocation

```cpp
    BacktestResult res = run_backtest(market, products, *strategy, cm, initial_cash);
```

with:

```cpp
    // Native path: the engine touches no Python objects (all inputs were
    // copied to std::vectors above), so drop the GIL and let other fold
    // workers run concurrently. The bridge path re-enters Python every bar
    // and must keep the GIL.
    BacktestResult res;
    if (callback.is_none()) {
        nb::gil_scoped_release release;
        res = run_backtest(market, products, *strategy, cm, initial_cash);
    } else {
        res = run_backtest(market, products, *strategy, cm, initial_cash);
    }
```

- [ ] **Step 4: Rebuild, run everything**

```bash
cmake --build cpp/build -j && ctest --test-dir cpp/build --output-on-failure
uv sync --reinstall-package pkmn-quant
uv run pytest tests/test_native_parity.py -q
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Expected: ctest 25/25; the full parity suite exact as ever (the release changes scheduling, never values).

- [ ] **Step 5: Commit**

```bash
git add cpp/ tests/test_native_parity.py
git commit -m "feat(cpp): release the GIL during native engine runs + concurrency smoke tests"
```

---

### Task 4: Fold workers — parallel `run_walkforward`

The core deliverable: one warehouse load, one fold worker per fold on a thread pool, results collected in fold order, `workers` parameter with auto/serial semantics.

**Files:**
- Modify: `src/pkmn_quant/research/walkforward.py` (`run_walkforward` restructured around `_fold_worker`)
- Test: `tests/research/test_walkforward_parallel.py` (create)

**Interfaces:**
- Consumes: `PreparedMarket.prepare(..., frame=, products=)` (Task 2); GIL-released native runs (Task 3).
- Produces: `run_walkforward(..., workers: int = 1)` — `0` = auto (`min(n_folds, os.cpu_count() or 1)`), `1` = plain serial loop, `>1` = thread pool of that size, `<0` = `ValueError`. Same `WalkForwardResult`, bit-identical at any worker count. Existing keyword callers unaffected (new arg is last with a default preserving serial behavior).

- [ ] **Step 1: Write the failing tests**

`tests/research/test_walkforward_parallel.py`:

```python
"""Serial == parallel, bit-for-bit — the Plan 11 acceptance property.

seed_rich (60 days) with is=20/oos=10 yields 4 folds; a trivial fixed-params
optimizer keeps runs fast while still exercising evaluate + IS + OOS per fold.
"""

from datetime import timedelta
from pathlib import Path

import pytest

from pkmn_quant.config import Paths
from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.costs import CostModel
from pkmn_quant.engine.strategy import Strategy
from pkmn_quant.research.walkforward import Params, WalkForwardResult, run_walkforward
from pkmn_quant.strategies.dip_buyer import DipBuyer
from tests.test_native_parity import START, seed_rich

FIXED: Params = {
    "dip_window_days": 5, "dip_threshold": 0.10, "hold_days": 7, "take_profit": 1.05,
}


def factory(p: Params) -> Strategy:
    return DipBuyer(
        dip_window_days=int(p["dip_window_days"]),
        dip_threshold=float(p["dip_threshold"]),
        hold_days=int(p["hold_days"]),
        take_profit=float(p["take_profit"]),
    )


def optimizer(fold: object, evaluate: object) -> Params:
    evaluate(dict(FIXED))  # type: ignore[operator]  # exercise the IS evaluate path
    return dict(FIXED)


def run_wf(root: Path, workers: int, strategy_name: str) -> WalkForwardResult:
    return run_walkforward(
        warehouse=Warehouse(Paths(root=root)),
        strategy_factory=factory,
        optimizer=optimizer,
        cost_model=CostModel(impact_enabled=True),
        start=START,
        end=START + timedelta(days=59),
        is_days=20,
        oos_days=10,
        initial_cash=1000.0,
        warmup_days=10,
        engine="cpp",
        strategy_name=strategy_name,
        workers=workers,
    )


def assert_wf_equal(a: WalkForwardResult, b: WalkForwardResult) -> None:
    assert a.stitched_curve["date"].to_list() == b.stitched_curve["date"].to_list()
    assert a.stitched_curve["equity"].to_list() == b.stitched_curve["equity"].to_list()
    assert a.summary == b.summary
    assert len(a.folds) == len(b.folds)
    for fa, fb in zip(a.folds, b.folds, strict=True):
        assert fa.fold == fb.fold
        assert fa.params == fb.params
        assert fa.is_summary == fb.is_summary
        assert fa.oos_summary == fb.oos_summary
        assert fa.oos_curve["equity"].to_list() == fb.oos_curve["equity"].to_list()


def test_parallel_matches_serial_native(tmp_path: Path) -> None:
    seed_rich(tmp_path, n_days=60)
    serial = run_wf(tmp_path, workers=1, strategy_name="dip-buyer")
    parallel = run_wf(tmp_path, workers=4, strategy_name="dip-buyer")
    assert len(serial.folds) == 4
    assert serial.stitched_curve.height > 0
    assert_wf_equal(serial, parallel)


def test_parallel_matches_serial_bridge(tmp_path: Path) -> None:
    """A non-native strategy_name forces the callback bridge in every fold."""
    seed_rich(tmp_path, n_days=60)
    serial = run_wf(tmp_path, workers=1, strategy_name="bridge-test")
    parallel = run_wf(tmp_path, workers=4, strategy_name="bridge-test")
    assert_wf_equal(serial, parallel)


def test_parallel_is_deterministic_across_repetitions(tmp_path: Path) -> None:
    seed_rich(tmp_path, n_days=60)
    runs = [run_wf(tmp_path, workers=4, strategy_name="dip-buyer") for _ in range(3)]
    assert_wf_equal(runs[0], runs[1])
    assert_wf_equal(runs[0], runs[2])


def test_auto_workers_matches_serial(tmp_path: Path) -> None:
    seed_rich(tmp_path, n_days=60)
    serial = run_wf(tmp_path, workers=1, strategy_name="dip-buyer")
    auto = run_wf(tmp_path, workers=0, strategy_name="dip-buyer")
    assert_wf_equal(serial, auto)


def test_negative_workers_raises(tmp_path: Path) -> None:
    seed_rich(tmp_path, n_days=60)
    with pytest.raises(ValueError, match="workers"):
        run_wf(tmp_path, workers=-1, strategy_name="dip-buyer")


def test_fold_worker_exception_propagates(tmp_path: Path) -> None:
    seed_rich(tmp_path, n_days=60)

    def broken_factory(p: Params) -> Strategy:
        raise RuntimeError("boom in fold worker")

    with pytest.raises(RuntimeError, match="boom in fold worker"):
        run_walkforward(
            warehouse=Warehouse(Paths(root=tmp_path)),
            strategy_factory=broken_factory,
            optimizer=optimizer,
            cost_model=CostModel(),
            start=START,
            end=START + timedelta(days=59),
            is_days=20,
            oos_days=10,
            initial_cash=1000.0,
            engine="cpp",
            strategy_name="bridge-test",  # forces factory use -> raises
            workers=4,
        )
```

Run: `uv run pytest tests/research/test_walkforward_parallel.py -v`
Expected: FAIL — `TypeError: run_walkforward() got an unexpected keyword argument 'workers'`.

- [ ] **Step 2: Restructure run_walkforward**

In `src/pkmn_quant/research/walkforward.py`: add `import os` and `from concurrent.futures import ThreadPoolExecutor` at the top; add `workers: int = 1` after `strategy_name` in the signature; replace everything from the current `if engine == "cpp": from pkmn_quant.engine.native import ...` block through the end of the fold loop with:

```python
    if workers < 0:
        raise ValueError(f"workers must be >= 0, got {workers}")

    if engine == "cpp":
        from pkmn_quant.engine.native import (
            NATIVE_STRATEGY_NAMES,
            NativeBacktest,
            NativeStrategySpec,
        )
        from pkmn_quant.engine.prepared import PreparedMarket

        # Load once; fold workers slice windows from these shared,
        # immutable frames instead of re-reading parquet per backtest.
        frame_full = warehouse.load_prices()
        products_full = warehouse.load_products()

    folds = make_folds(start, end, is_days=is_days, oos_days=oos_days)

    def _fold_worker(fold: Fold) -> FoldResult:
        """One fold end-to-end. Owns everything it touches (its optuna
        study via `optimizer`, its PreparedMarket windows, per-backtest
        engine instances) — workers share nothing mutable."""
        if engine == "cpp":
            prepared_is = PreparedMarket.prepare(
                warehouse, fold.is_start, fold.is_end, warmup_days=warmup_days,
                frame=frame_full, products=products_full,
            )
            prepared_oos = PreparedMarket.prepare(
                warehouse, fold.oos_start, fold.oos_end, warmup_days=warmup_days,
                frame=frame_full, products=products_full,
            )
        else:
            prepared_is = prepared_oos = None

        def _run(params: Params, window_start: date, window_end: date, prepared: object) -> Result:
            if engine == "cpp":
                native = (
                    NativeStrategySpec(strategy_name, {k: float(v) for k, v in params.items()})
                    if strategy_name in NATIVE_STRATEGY_NAMES
                    else strategy_factory(params)  # bridge: e.g. ml-ranker
                )
                return NativeBacktest(
                    warehouse=warehouse,
                    strategy=native,
                    cost_model=cost_model,
                    start=window_start,
                    end=window_end,
                    initial_cash=initial_cash,
                    warmup_days=warmup_days,
                    prepared=prepared,  # type: ignore[arg-type]
                ).run()
            return Backtest(
                warehouse=warehouse,
                strategy=strategy_factory(params),
                cost_model=cost_model,
                start=window_start,
                end=window_end,
                initial_cash=initial_cash,
                warmup_days=warmup_days,
            ).run()

        def evaluate(params: Params) -> float:
            result = _run(params, fold.is_start, fold.is_end, prepared_is)
            return float(result.summary[objective_metric])

        best = optimizer(fold, evaluate)
        is_result = _run(best, fold.is_start, fold.is_end, prepared_is)
        oos_result = _run(best, fold.oos_start, fold.oos_end, prepared_oos)
        return FoldResult(
            fold=fold,
            params=best,
            is_summary=is_result.summary,
            oos_summary=oos_result.summary,
            oos_curve=oos_result.equity_curve,
        )

    n_workers = min(len(folds), os.cpu_count() or 1) if workers == 0 else workers
    if n_workers <= 1 or len(folds) <= 1:
        # Plain serial loop: the pre-Plan-11 reference path, executor-free.
        fold_results = [_fold_worker(fold) for fold in folds]
    else:
        with ThreadPoolExecutor(max_workers=n_workers) as executor:
            futures = [executor.submit(_fold_worker, fold) for fold in folds]
            try:
                # Collect in FOLD order (not completion order): stitching
                # depends on chronology, and .result() re-raises the first
                # in-order failure.
                fold_results = [future.result() for future in futures]
            except BaseException:
                for future in futures:
                    future.cancel()  # not-yet-started folds never run
                raise
```

Keep the trailing `_stitch`/`_summarize_folds`/`return` unchanged. Update the docstring: add a `workers` paragraph (0 auto / 1 serial / N threads; results identical at any count — fold studies are independent and seeded; bridged strategies are correct but roughly serial under threads because the callback holds the GIL). If mypy complains about `prepared: object` in `_run`'s signature, type it `PreparedMarket | None` under `TYPE_CHECKING` import instead.

- [ ] **Step 3: Run the new suite + all existing walkforward tests**

```bash
uv run pytest tests/research/test_walkforward_parallel.py -v
uv run pytest tests/research/ tests/test_native_parity.py tests/test_cli_walkforward.py -q
```

Expected: all PASS. If serial≠parallel anywhere, do NOT touch assertions: diff the first divergent fold's params (an optimizer-state leak) vs equity (an engine/data-sharing leak) and fix the leak.

- [ ] **Step 4: Full gates, commit**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy
git add src/pkmn_quant/research/walkforward.py tests/research/test_walkforward_parallel.py
git commit -m "feat: fold-parallel run_walkforward — workers param, shared load, bit-identical"
```

---

### Task 5: CLI `--workers`, default-engine flips, registry `runtime` field

**Files:**
- Modify: `src/pkmn_quant/cli.py` (backtest + walkforward commands), `src/pkmn_quant/research/runs.py`
- Test: `tests/test_cli_walkforward.py` (append), `tests/test_cli_backtest.py` (append)

**Interfaces:**
- Consumes: `run_walkforward(..., workers=)` (Task 4).
- Produces: `pkmn walkforward --workers N` (default 0 = auto); both `--engine` options default `"cpp"`; `record_run(..., runtime: dict[str, Any] | None = None)` writing a top-level `runtime` key; `RunRecord.runtime: dict[str, Any] | None = None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli_walkforward.py`:

```python
def test_walkforward_negative_workers_clean_error(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "walkforward", "--strategy", "sealed-accumulation",
            "--start", "2025-06-01", "--end", "2025-06-03",
            "--workers", "-2", "--root", str(tmp_path),
        ],
    )
    assert result.exit_code != 0
    assert "workers" in result.output
    assert "Traceback" not in result.output
```

Append to `tests/test_cli_backtest.py`:

```python
def test_default_engine_is_cpp_and_recorded(tmp_path: Path) -> None:
    """No --engine flag => cpp, recorded in the run config (Plan 11 flip)."""
    from pkmn_quant.research.runs import load_runs

    seed(tmp_path)
    result = run_cli(tmp_path)  # no --engine argument
    assert result.exit_code == 0, result.output
    runs = load_runs(tmp_path)
    assert len(runs) == 1
    assert runs[0].config["engine"] == "cpp"
```

Run: `uv run pytest tests/test_cli_walkforward.py tests/test_cli_backtest.py -q`
Expected: FAIL — `No such option: --workers`; the default-engine test fails with `config["engine"] == "python"`.

- [ ] **Step 2: runs.py — the runtime field**

In `src/pkmn_quant/research/runs.py`:
- `RunRecord` gains a final field: `runtime: dict[str, Any] | None = None` (a default, so pre-Plan-11 registry lines still load — `load_runs` builds `RunRecord(**known)`).
- `record_run` gains a final keyword parameter `runtime: dict[str, Any] | None = None`, and the `record` dict gains `"runtime": runtime` ONLY when `runtime is not None` (old-shape records stay old-shape):

```python
        if runtime is not None:
            record["runtime"] = runtime
```

placed after the `"artifact_path"` line, before writing. Docstring note: `runtime` holds operational metadata that provably cannot affect results (e.g. worker count) and is therefore excluded from config_hash.

- [ ] **Step 3: cli.py — flips + workers**

- `backtest()`: change the `engine` option default `"python"` → `"cpp"` and its help to `"Backtest engine: cpp (native, default) or python (reference)."`.
- `walkforward()`: same default/help change for `engine`; add after it:

```python
    workers: int = typer.Option(
        0,
        help="Fold-level parallelism: 0 = auto (min(folds, cores)), 1 = serial, N = N threads.",
    ),
```

- In `walkforward()`'s validation block (with the strategy/objective_metric/engine checks), add:

```python
    if workers < 0:
        raise typer.BadParameter(f"workers must be >= 0, got {workers}")
```

- Pass `workers=workers` to the `run_walkforward(...)` call, and extend its `record_run(...)` call with `runtime={"workers": workers}`. The backtest command's `record_run` gets no runtime (nothing operational to record there).

- [ ] **Step 4: Run everything**

```bash
uv run pytest tests/test_cli_walkforward.py tests/test_cli_backtest.py tests/test_native_parity.py -q
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Expected: all PASS — including the dual-engine goldens (the flip cannot move pinned numbers; if a golden fails here, STOP: that is a parity break, not a test to update).

- [ ] **Step 5: Commit**

```bash
git add src/pkmn_quant/cli.py src/pkmn_quant/research/runs.py tests/test_cli_walkforward.py tests/test_cli_backtest.py
git commit -m "feat: --workers on walkforward, cpp default engine, runtime metadata in runs registry"
```

---

### Task 6: Real-data benchmark + docs

**Files:**
- Create: `scripts/bench_walkforward.py`
- Modify: `docs/research-findings-2026-07.md`, `README.md`, `CLAUDE.md`
- Test: manual script run against local `data/` (gitignored)

**Interfaces:**
- Consumes: everything.
- Produces: `uv run python scripts/bench_walkforward.py` — one real-data walkforward per engine config (python serial / cpp serial / cpp auto-workers), markdown wall-clock table on stdout, and a built-in exact-equality check between the cpp serial and cpp parallel results (exit 1 on mismatch — this doubles as the plan's real-data acceptance).

- [ ] **Step 1: Write scripts/bench_walkforward.py**

```python
"""Walk-forward wall-clock: python serial vs cpp serial vs cpp parallel.

One run per config (a walkforward is minutes, not microseconds — best-of-N
would triple an already-long benchmark; treat small deltas as noise).
Includes the Plan 11 acceptance check: cpp serial and cpp parallel results
must be exactly equal. Run from the repo root (needs data/).
"""

from __future__ import annotations

import sys
import time
from datetime import date
from pathlib import Path

from pkmn_quant.config import Paths
from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.costs import CostModel
from pkmn_quant.research.registry import REGISTRY
from pkmn_quant.research.search import SearchSpec, optimize_params
from pkmn_quant.research.walkforward import Fold, Params, WalkForwardResult, run_walkforward

START, END = date(2024, 3, 1), date(2026, 6, 30)
STRATEGY = "sealed-accumulation"
TRIALS = 15
SEED = 42


def run_one(engine: str, workers: int) -> tuple[float, WalkForwardResult]:
    entry = REGISTRY[STRATEGY]

    def optimizer(fold: Fold, evaluate: object) -> Params:
        spec = SearchSpec(space=entry.space, n_trials=TRIALS, seed=SEED)
        return optimize_params(spec, evaluate)  # type: ignore[arg-type]

    t0 = time.perf_counter()
    result = run_walkforward(
        warehouse=Warehouse(Paths(root=Path("."))),
        strategy_factory=entry.factory,
        optimizer=optimizer,
        cost_model=CostModel(impact_enabled=True),
        start=START,
        end=END,
        is_days=180,
        oos_days=60,
        initial_cash=10_000.0,
        warmup_days=120,
        engine=engine,
        strategy_name=STRATEGY if engine == "cpp" else None,
        workers=workers,
    )
    return time.perf_counter() - t0, result


def main() -> int:
    t_cpp_par, r_cpp_par = run_one("cpp", 0)
    t_cpp_ser, r_cpp_ser = run_one("cpp", 1)
    t_py, _ = run_one("python", 1)

    print(f"| config | wall-clock (s) | speedup vs python |")
    print(f"|---|---|---|")
    print(f"| python, serial | {t_py:.1f} | 1.0x |")
    print(f"| cpp, serial | {t_cpp_ser:.1f} | {t_py / t_cpp_ser:.1f}x |")
    print(f"| cpp, workers=auto | {t_cpp_par:.1f} | {t_py / t_cpp_par:.1f}x |")

    ok = (
        r_cpp_ser.stitched_curve["equity"].to_list()
        == r_cpp_par.stitched_curve["equity"].to_list()
        and r_cpp_ser.summary == r_cpp_par.summary
        and [f.params for f in r_cpp_ser.folds] == [f.params for f in r_cpp_par.folds]
    )
    print(f"\nserial == parallel (bit-for-bit): {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
```

(If ruff flags the f-strings without placeholders in the header rows, drop the `f` prefixes on those two lines.)

- [ ] **Step 2: Run it, capture the table**

Run: `uv run python scripts/bench_walkforward.py | tee /tmp/bench_wf.md` — the python-serial leg is the long one (this is today's status quo cost, likely tens of minutes; use a generous/background invocation). Expected: `serial == parallel (bit-for-bit): PASS` and exit 0. A FAIL is a stop-the-line event: diverging params point at optimizer-state sharing, diverging equity at data sharing — report BLOCKED with both serial and parallel values.

- [ ] **Step 3: Docs**

- `docs/research-findings-2026-07.md`: new "Plan 11 (2026-07-17): fold-parallel walk-forward" section — the measured table verbatim, the PASS line, one honest paragraph: results unchanged by construction (fold studies independent + seeded; equivalence enforced in CI and in the bench), what changed is wall-clock; ceiling is min(folds, cores); bridged strategies (ml-ranker) stay GIL-bound and gain little; the GIL is now actually released on the native path (updating the Plan 10 wording that said "can be released in Plan 11").
- `README.md`: walkforward section gains `--workers` and the new defaults (engine cpp, parallel auto); mention `--engine python --workers 1` as the reference behavior.
- `CLAUDE.md`: Plan 11 status bullet (what shipped, test counts, measured table headline); Commands: add `uv run pkmn walkforward ... --workers 1  # serial reference run`; update the Plan 10 GIL sentence to "the native path now releases the GIL (Plan 11)"; gotcha: never share one PreparedMarket across threads with bridged strategies.

- [ ] **Step 4: Full gates, commit**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy
git add scripts/bench_walkforward.py docs/ README.md CLAUDE.md
git commit -m "docs: Plan 11 findings — measured fold-parallel speedup, serial==parallel PASS"
```

---

## Self-review notes (already applied)

- Spec coverage: fold worker architecture → Task 4; two-level hoist → Tasks 1-2; GIL contract + Catch2 smoke → Task 3; CLI/defaults/registry → Task 5; equivalence suite → Tasks 2-4 tests; benchmark + findings + real-data acceptance → Task 6 (the bench's built-in PASS check is the full-data equivalence run). Error handling: worker exception propagation (Task 4 test), workers validation both layers (Tasks 4-5).
- Type consistency: `PreparedMarket.prepare(warehouse, start, end, warmup_days=0, *, frame=None, products=None)` used identically in Tasks 2 and 4; `run_walkforward(..., workers: int = 1)` (library default 1 = old behavior; the CLI passes its own default 0=auto); `runtime` dict shape `{"workers": int}` in Task 5 only.
- Deliberate choices an executor should not "fix": the library default is `workers=1` (backward-compatible) while the CLI default is `0` (auto) — both are intended; MOVE markers in Tasks 1-2 mean relocate existing reviewed code, not retype it; the bench runs each config once (a walkforward is minutes long — best-of-3 would be hostile), unlike bench_engines' best-of-3.
- Known judgment calls: exact placement of `workers` CLI validation follows the existing pre-validation block pattern; if `test_walkforward_parallel.py` imports from `tests.test_native_parity` trip anything (it's a test-module import, used already by nothing else), inline the seed_rich import path stays as written — tests import from tests.helpers elsewhere, and pytest's rootdir setup makes `from tests.test_native_parity import ...` resolvable (`tests/__init__.py` exists; verify with `ls tests/__init__.py`, create empty if missing).
