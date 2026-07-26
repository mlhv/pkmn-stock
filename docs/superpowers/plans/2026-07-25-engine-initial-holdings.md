# Engine Initial-Holdings Seeding (Plan 2a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The native C++ engine can start a backtest from pre-existing positions (asset, quantity, cost basis, opened-on date), not just cash.

**Architecture:** Add a `SeedPosition` struct and a `Portfolio::seed()` method to the C++ engine, thread an `initial_holdings` vector through `run_backtest` (defaulted so existing call sites are untouched), expose it across the nanobind boundary as four parallel arrays, and add a `SeededHolding` dataclass + `initial_holdings` field to the Python `NativeBacktest` adapter, which validates universe membership and marshals holdings across. The Python `Backtest`/`Portfolio` engine is deliberately NOT changed — seeding is a C++-only capability, the first step toward a C++-only engine.

**Tech Stack:** C++20 (`pkmn_engine_core`, Catch2 tests), nanobind binding (`pkmn_quant._engine`), Python 3.12 (`NativeBacktest` adapter, pytest), numpy, polars, `uv`.

## Global Constraints

- After ANY edit under `cpp/`, run `uv sync --reinstall-package pkmn-quant` before Python code sees the new native module — a stale `.so` silently runs old C++.
- Never enable `-ffast-math`, `-ffp-contract=fast`, or MSVC `/fp:fast` anywhere in the build — bit-for-bit floating point is load-bearing.
- All four Python gates must pass before every commit: `uv run ruff check .` && `uv run ruff format --check .` && `uv run mypy` && `uv run pytest`.
- C++ tests: `cmake -S cpp -B cpp/build -DPKMN_BUILD_TESTS=ON -DCMAKE_BUILD_TYPE=Release && cmake --build cpp/build -j && ctest --test-dir cpp/build --output-on-failure`.
- The Python `engine/backtest.py` and `engine/portfolio.py` MUST NOT be modified by this plan. Seeding lives only in the native path.
- Follow existing code idioms: frozen dataclasses for value objects; Catch2 `TEST_CASE`/`CHECK`/`REQUIRE`; every C++ arithmetic comment cites the mirrored behavior.

---

### Task 1: `SeedPosition` struct and `Portfolio::seed()` (C++ core)

**Files:**
- Modify: `cpp/src/pkmn_engine/types.hpp` (add `SeedPosition` after `Position`, ~line 44)
- Modify: `cpp/src/pkmn_engine/portfolio.hpp` (declare `seed`, ~line 20)
- Modify: `cpp/src/pkmn_engine/portfolio.cpp` (implement `seed`)
- Test: `cpp/tests/test_portfolio.cpp` (append seed unit tests)

**Interfaces:**
- Consumes: existing `Position`, `InsertionMap`, `AssetId`, `Day`, `Fill` from `types.hpp`; existing `Portfolio` (`cash`, `realized_pnl`, `positions`, `apply`, `equity`).
- Produces: `struct SeedPosition { AssetId asset; std::int64_t quantity; double avg_cost; Day opened_on; };` and `void Portfolio::seed(const std::vector<SeedPosition>& holdings);` — installs each holding as a `Position` in list order, without touching `cash` or `realized_pnl`; validates `quantity > 0`, `avg_cost >= 0`, and no duplicate asset (throws `std::invalid_argument`).

- [ ] **Step 1: Write the failing tests**

Append to `cpp/tests/test_portfolio.cpp` (it already has `#include "pkmn_engine/portfolio.hpp"`, `#include <stdexcept>`, and `using pkmn::Fill; using pkmn::InsertionMap; using pkmn::Portfolio;`). Add `using pkmn::SeedPosition;` next to the other usings at the top, then append these cases at the end:

```cpp
TEST_CASE("seed installs positions without touching cash or realized pnl") {
    Portfolio pf(100.0, 4);
    // Insertion order is list order: asset 2 first, then asset 0.
    pf.seed(std::vector<SeedPosition>{{2, 3, 5.0, 90}, {0, 1, 8.0, 91}});
    CHECK(pf.cash == 100.0);          // seeding never moves cash
    CHECK(pf.realized_pnl == 0.0);    // ...or realized P&L
    const auto* p2 = pf.positions.find(2);
    REQUIRE(p2 != nullptr);
    CHECK(p2->quantity == 3);
    CHECK(p2->avg_cost == 5.0);
    CHECK(p2->opened_on == 90);
    const auto* p0 = pf.positions.find(0);
    REQUIRE(p0 != nullptr);
    CHECK(p0->quantity == 1);
    CHECK(p0->avg_cost == 8.0);
    CHECK(p0->opened_on == 91);
    // equity sums seeded positions in insertion order (2 before 0).
    InsertionMap<double> marks(4);
    marks.set(2, 6.0);
    marks.set(0, 10.0);
    CHECK(pf.equity(marks) == 100.0 + 3 * 6.0 + 1 * 10.0);  // 128.0
}

TEST_CASE("selling a seeded position realizes pnl against its cost basis") {
    Portfolio pf(0.0, 4);
    pf.seed(std::vector<SeedPosition>{{0, 4, 9.0, 90}});
    pf.apply(Fill{101, 0, -4, 12.0, 1.0, 0.0});  // sell all 4 at 12, fee 1
    // proceeds 48; cash 0 + 48 - 1 = 47
    CHECK(pf.cash == 47.0);
    // realized: 48 - 4*9 - 1 = 48 - 36 - 1 = 11
    CHECK(pf.realized_pnl == 11.0);
    CHECK(pf.positions.find(0) == nullptr);  // full close removes it
}

TEST_CASE("strategy buying more of a seeded asset averages the cost basis") {
    Portfolio pf(1000.0, 4);
    pf.seed(std::vector<SeedPosition>{{0, 2, 10.0, 90}});
    pf.apply(Fill{101, 0, 2, 20.0, 0.0, 0.0});  // buy 2 more at 20
    const auto* pos = pf.positions.find(0);
    REQUIRE(pos != nullptr);
    CHECK(pos->quantity == 4);
    CHECK(pos->avg_cost == 15.0);  // (10*2 + 20*2) / 4
    CHECK(pos->opened_on == 90);   // add keeps the seed's opened_on
}

TEST_CASE("seed validates quantity, avg_cost, and rejects duplicates") {
    Portfolio pf(100.0, 4);
    CHECK_THROWS_AS(pf.seed(std::vector<SeedPosition>{{0, 0, 5.0, 90}}),
                    std::invalid_argument);  // zero qty
    CHECK_THROWS_AS(pf.seed(std::vector<SeedPosition>{{0, -1, 5.0, 90}}),
                    std::invalid_argument);  // negative qty
    CHECK_THROWS_AS(pf.seed(std::vector<SeedPosition>{{0, 1, -1.0, 90}}),
                    std::invalid_argument);  // negative avg_cost
    CHECK_THROWS_AS(
        pf.seed(std::vector<SeedPosition>{{0, 1, 5.0, 90}, {0, 1, 5.0, 91}}),
        std::invalid_argument);  // duplicate asset
    // avg_cost == 0 is legal (a gift/pull with no cash basis)
    Portfolio ok(100.0, 4);
    ok.seed(std::vector<SeedPosition>{{0, 1, 0.0, 90}});
    CHECK(ok.positions.find(0)->avg_cost == 0.0);
}
```

- [ ] **Step 2: Run the tests to verify they fail to compile**

Run: `cmake -S cpp -B cpp/build -DPKMN_BUILD_TESTS=ON -DCMAKE_BUILD_TYPE=Release && cmake --build cpp/build -j`
Expected: FAIL — compile error, `SeedPosition` and `Portfolio::seed` are undeclared.

- [ ] **Step 3: Add the `SeedPosition` struct**

In `cpp/src/pkmn_engine/types.hpp`, immediately after the `Position` struct (ends ~line 44), add:

```cpp
// A pre-existing holding installed before bar one (Plan 2a initial
// holdings). Unlike a Fill it moves no cash: quantity and avg_cost are the
// starting position and its cost basis; opened_on dates the holding for
// duration-gated strategies.
struct SeedPosition {
    AssetId asset;
    std::int64_t quantity;
    double avg_cost;
    Day opened_on;
};
```

- [ ] **Step 4: Declare `seed` in the Portfolio header**

In `cpp/src/pkmn_engine/portfolio.hpp`, after the `apply` declaration (~line 20), add:

```cpp
    // Install pre-existing holdings before bar one (Plan 2a). Positions are
    // added in list order (insertion order is parity-relevant for equity()).
    // Touches neither cash nor realized_pnl. Throws std::invalid_argument on
    // quantity <= 0, avg_cost < 0, or a duplicate asset.
    void seed(const std::vector<SeedPosition>& holdings);
```

`portfolio.hpp` already includes `pkmn_engine/types.hpp`; add `#include <vector>` near the top includes if not present (it uses `std::size_t` via `<cstddef>` today — add `<vector>` explicitly).

- [ ] **Step 5: Implement `seed`**

In `cpp/src/pkmn_engine/portfolio.cpp`, after `Portfolio::apply` (~line 23), add:

```cpp
void Portfolio::seed(const std::vector<SeedPosition>& holdings) {
    for (const auto& s : holdings) {
        if (s.quantity <= 0)
            throw std::invalid_argument("seed quantity must be positive");
        if (s.avg_cost < 0.0)
            throw std::invalid_argument("seed avg_cost must be non-negative");
        if (positions.contains(s.asset))
            throw std::invalid_argument("duplicate seed asset");
        positions.set(s.asset, Position{s.quantity, s.avg_cost, s.opened_on});
    }
}
```

- [ ] **Step 6: Build and run the tests to verify they pass**

Run: `cmake --build cpp/build -j && ctest --test-dir cpp/build --output-on-failure`
Expected: PASS — all portfolio cases green, including the four new ones.

- [ ] **Step 7: Commit**

```bash
git add cpp/src/pkmn_engine/types.hpp cpp/src/pkmn_engine/portfolio.hpp cpp/src/pkmn_engine/portfolio.cpp cpp/tests/test_portfolio.cpp
git commit -m "feat(cpp): Portfolio::seed installs initial holdings before bar one"
```

---

### Task 2: Thread `initial_holdings` through `run_backtest` (C++ core)

**Files:**
- Modify: `cpp/src/pkmn_engine/backtest.hpp` (add trailing parameter)
- Modify: `cpp/src/pkmn_engine/backtest.cpp` (seed before the loop)
- Test: `cpp/tests/test_backtest_golden.cpp` (append seeded end-to-end golden)

**Interfaces:**
- Consumes: `Portfolio::seed` and `SeedPosition` from Task 1; existing `run_backtest(MarketView&, const ProductTable&, Strategy&, const CostModel&, double)`.
- Produces: `run_backtest(..., double initial_cash, const std::vector<SeedPosition>& initial_holdings = {})` — same behavior as before when `initial_holdings` is empty; otherwise seeds the portfolio after construction and before day one. Existing 5-argument call sites are unaffected by the default.

- [ ] **Step 1: Write the failing test**

Append to `cpp/tests/test_backtest_golden.cpp`. First, inside the existing anonymous `namespace { ... }` block (before its closing `}`, after `one_sealed()` ~line 32), add a no-op strategy so seeding is isolated from any trading:

```cpp
// Emits no orders: isolates seeding so the equity curve is purely
// cash + seeded-position value across carry-forward marks.
struct NoTrade : Strategy {
    std::vector<Order> on_bar(const Context&) override { return {}; }
};
```

Then append this test case after the last `TEST_CASE` in the file:

```cpp
TEST_CASE("seeded holdings value through the loop with no trading") {
    auto mkt = flat_view();  // asset 0, marks 10/12/15 on days 100/101/102
    auto prods = one_sealed();
    CostModel cm;  // impact off
    NoTrade strat;
    // Start with 50 cash and 4 units of asset 0 (cost basis 9, opened day 90).
    std::vector<SeedPosition> holdings{{0, 4, 9.0, 90}};
    auto res = run_backtest(mkt, prods, strat, cm, 50.0, holdings);
    // No fills ever; equity = 50 + 4*mark. D1 50+40=90, D2 50+48=98,
    // D3 50+60=110. EXACT doubles.
    REQUIRE(res.fills.empty());
    REQUIRE(res.equity == std::vector<double>{90.0, 98.0, 110.0});
}

TEST_CASE("no initial holdings leaves the existing signature behavior") {
    auto mkt = flat_view();
    auto prods = one_sealed();
    CostModel cm;
    NoTrade strat;
    // 5-arg call still compiles (default empty holdings); flat cash curve.
    auto res = run_backtest(mkt, prods, strat, cm, 50.0);
    REQUIRE(res.fills.empty());
    REQUIRE(res.equity == std::vector<double>{50.0, 50.0, 50.0});
}
```

- [ ] **Step 2: Build to verify it fails**

Run: `cmake --build cpp/build -j`
Expected: FAIL — `run_backtest` has no 6-argument overload; the `holdings` call does not compile.

- [ ] **Step 3: Add the parameter to the declaration**

In `cpp/src/pkmn_engine/backtest.hpp`, change the `run_backtest` declaration to:

```cpp
BacktestResult run_backtest(MarketView& market, const ProductTable& products,
                            Strategy& strategy, const CostModel& cost_model,
                            double initial_cash,
                            const std::vector<SeedPosition>& initial_holdings = {});
```

(`backtest.hpp` already includes `types.hpp` and `<vector>`, so `SeedPosition` and `std::vector` are visible.)

- [ ] **Step 4: Seed before the loop in the definition**

In `cpp/src/pkmn_engine/backtest.cpp`, change the signature to match (repeat the new parameter, no default in the definition) and seed right after the `Portfolio` is constructed:

```cpp
BacktestResult run_backtest(MarketView& market, const ProductTable& products,
                            Strategy& strategy, const CostModel& cost_model,
                            double initial_cash,
                            const std::vector<SeedPosition>& initial_holdings) {
    // backtest.py:50-102, same step order per day.
    strategy.reset();
    market.reset();
    Portfolio portfolio(initial_cash, market.n_assets());
    portfolio.seed(initial_holdings);  // Plan 2a: pre-existing holdings, day 0
    BacktestResult out;
```

(Leave the rest of the function body unchanged.)

- [ ] **Step 5: Build and run to verify pass**

Run: `cmake --build cpp/build -j && ctest --test-dir cpp/build --output-on-failure`
Expected: PASS — the two new golden cases plus every pre-existing case (the default-argument change must not shift any existing number).

- [ ] **Step 6: Commit**

```bash
git add cpp/src/pkmn_engine/backtest.hpp cpp/src/pkmn_engine/backtest.cpp cpp/tests/test_backtest_golden.cpp
git commit -m "feat(cpp): run_backtest seeds initial holdings before the loop"
```

---

### Task 3: Expose `initial_holdings` across the nanobind boundary

**Files:**
- Modify: `cpp/bindings/module.cpp`

**Interfaces:**
- Consumes: `run_backtest(..., const std::vector<SeedPosition>&)` from Task 2; `SeedPosition` from Task 1.
- Produces: the `_engine.run_backtest` Python function gains four required keyword arguments — `holding_asset` (int32[]), `holding_qty` (int64[]), `holding_cost` (float64[]), `holding_opened` (int32[]) — parallel arrays (equal length, possibly length 0) marshaled into a `std::vector<SeedPosition>` and passed to the core.

- [ ] **Step 1: Add the four array parameters to the C++ binding function**

In `cpp/bindings/module.cpp`, add `SeedPosition` to the includes' visibility (it comes in via `pkmn_engine/backtest.hpp` → `types.hpp`, already included). Extend the `run_backtest_py` signature: insert the four holding arrays immediately before `nb::object callback` (the last parameter), so the signature ends:

```cpp
    double initial_cash,
    Arr<std::int32_t> holding_asset, Arr<std::int64_t> holding_qty,
    Arr<double> holding_cost, Arr<std::int32_t> holding_opened,
    nb::object callback) {
```

- [ ] **Step 2: Build the `SeedPosition` vector inside the function**

In `run_backtest_py`, after the `CostModel cm; ...` block and before the `std::unique_ptr<Strategy> strategy;` line, add:

```cpp
    std::vector<SeedPosition> holdings(holding_asset.size());
    for (std::size_t i = 0; i < holdings.size(); ++i) {
        holdings[i] = SeedPosition{holding_asset(i), holding_qty(i),
                                   holding_cost(i), holding_opened(i)};
    }
```

- [ ] **Step 3: Pass `holdings` into both `run_backtest` call branches**

The function calls `run_backtest` twice (GIL-released native path and GIL-held bridge path). Add `holdings` as the trailing argument to BOTH:

```cpp
    BacktestResult res;
    if (callback.is_none()) {
        nb::gil_scoped_release release;
        res = run_backtest(market, products, *strategy, cm, initial_cash, holdings);
    } else {
        res = run_backtest(market, products, *strategy, cm, initial_cash, holdings);
    }
```

- [ ] **Step 4: Register the new arguments in `NB_MODULE`**

In the `m.def("run_backtest", ...)` call, insert the four `nb::arg` entries immediately after `nb::arg("initial_cash")` and before `nb::arg("callback").none()`:

```cpp
          nb::arg("initial_cash"), nb::arg("holding_asset"), nb::arg("holding_qty"),
          nb::arg("holding_cost"), nb::arg("holding_opened"),
          nb::arg("callback").none());
```

- [ ] **Step 5: Rebuild the extension module**

Run: `uv sync --reinstall-package pkmn-quant`
Expected: rebuild succeeds (the `.so` now carries the new signature).

- [ ] **Step 6: Verify the new signature is callable from Python**

Run: `uv run python -c "import inspect, pkmn_quant._engine as e; print('holding_asset' in e.run_backtest.__doc__ or 'ok')"`
Expected: prints `ok` (or a docstring containing `holding_asset`) without raising — confirms the rebuilt module exposes the new kwargs. If it raises `TypeError`/`ImportError`, the rebuild did not take; re-run Step 5.

- [ ] **Step 7: Commit**

```bash
git add cpp/bindings/module.cpp
git commit -m "feat(cpp): bind initial-holdings arrays through run_backtest"
```

---

### Task 4: `SeededHolding` dataclass + `NativeBacktest.initial_holdings` (Python adapter)

**Files:**
- Modify: `src/pkmn_quant/engine/native.py`
- Test: `tests/test_native_seeding.py` (create)

**Interfaces:**
- Consumes: the `_engine.run_backtest` `holding_*` kwargs from Task 3; existing `NativeBacktest`, `PreparedMarket` (`asset_index: dict[Asset, int]`), `Asset`, `_EPOCH`.
- Produces:
  - `@dataclass(frozen=True) class SeededHolding: asset: Asset; quantity: int; avg_cost: float; opened_on: date`
  - `NativeBacktest.initial_holdings: list[SeededHolding]` field (default empty), marshaled to the four arrays; holdings are sorted by dense asset id for deterministic insertion order, and an asset outside the backtest universe raises `ValueError`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_native_seeding.py`:

```python
"""NativeBacktest initial-holdings seeding (Plan 2a).

Parity is NOT the oracle here — the Python engine has no seeding. These
tests pin exact absolute numbers on a controlled 1-asset warehouse and
exercise the full Python->C++ marshaling (asset-id map, date->day, arrays).
"""

from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA
from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.costs import CostModel
from pkmn_quant.engine.native import NativeBacktest, SeededHolding
from pkmn_quant.engine.portfolio import Asset
from pkmn_quant.engine.strategy import Context, Strategy

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


class NoTrade(Strategy):
    name = "no-trade"

    def on_bar(self, ctx: Context) -> list:
        return []


def _seed_one_asset(root: Path) -> None:
    """One sealed product, three days, market 10/12/15 (marks track market)."""
    w = Warehouse(Paths(root=root))
    for i, price in enumerate((10.0, 12.0, 15.0)):
        day = START + timedelta(days=i)
        w.write_prices(
            day,
            pl.DataFrame(
                [
                    {
                        "date": day,
                        "product_id": 1,
                        "sub_type": "Normal",
                        "low": round(price * 0.9, 2),
                        "mid": round(price * 1.15, 2),
                        "high": round(price * 3.0, 2),
                        "market": price,
                    }
                ],
                schema=PRICE_SCHEMA,
            ),
        )
    w.write_products(PRODUCTS)


def _run(root: Path, holdings: list[SeededHolding]) -> list[float]:
    wh = Warehouse(Paths(root=root))
    res = NativeBacktest(
        warehouse=wh,
        strategy=NoTrade(),
        cost_model=CostModel(),
        start=START,
        end=START + timedelta(days=2),
        initial_cash=50.0,
        initial_holdings=holdings,
    ).run()
    return res.equity_curve["equity"].to_list()


def test_seeded_holdings_value_through_the_curve(tmp_path: Path) -> None:
    _seed_one_asset(tmp_path)
    asset = Asset(product_id=1, sub_type="Normal")
    equity = _run(
        tmp_path,
        [SeededHolding(asset=asset, quantity=4, avg_cost=9.0, opened_on=date(2024, 12, 1))],
    )
    # No trading; equity = 50 + 4*mark. 50+40, 50+48, 50+60.
    assert equity == [90.0, 98.0, 110.0]


def test_no_holdings_is_flat_cash(tmp_path: Path) -> None:
    _seed_one_asset(tmp_path)
    assert _run(tmp_path, []) == [50.0, 50.0, 50.0]


def test_holding_outside_universe_raises_value_error(tmp_path: Path) -> None:
    _seed_one_asset(tmp_path)
    ghost = Asset(product_id=999, sub_type="Normal")
    with pytest.raises(ValueError, match="universe"):
        _run(tmp_path, [SeededHolding(asset=ghost, quantity=1, avg_cost=5.0, opened_on=START)])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_native_seeding.py -v`
Expected: FAIL — `ImportError: cannot import name 'SeededHolding'` (and, once that is fixed, `NativeBacktest` has no `initial_holdings` field).

- [ ] **Step 3: Add the `SeededHolding` dataclass**

In `src/pkmn_quant/engine/native.py`, after the `NativeStrategySpec` dataclass (~line 47), add:

```python
@dataclass(frozen=True)
class SeededHolding:
    """A pre-existing position installed before bar one (Plan 2a).

    quantity > 0, avg_cost >= 0 (0 is legal). opened_on dates the holding for
    duration-gated strategies. The asset must be in the backtest universe.
    """

    asset: Asset
    quantity: int
    avg_cost: float
    opened_on: date
```

Add `Asset` to the existing import from `pkmn_quant.engine.portfolio` (currently `from pkmn_quant.engine.portfolio import Fill, Position` at line 23) so it reads `from pkmn_quant.engine.portfolio import Asset, Fill, Position`.

- [ ] **Step 4: Add the `initial_holdings` field**

In the `NativeBacktest` dataclass, add a field after `warmup_days: int = 0` (~line 64). Because `prepared` already has a default, put the new field before it or give it a default too — to keep field order valid, add it immediately after `warmup_days`:

```python
    warmup_days: int = 0
    initial_holdings: list[SeededHolding] = field(default_factory=list)
    prepared: PreparedMarket | None = None
```

Add `field` to the existing dataclasses import (`from dataclasses import dataclass` at line 12 → `from dataclasses import dataclass, field`).

- [ ] **Step 5: Marshal holdings to arrays and pass them across**

In `NativeBacktest.run()`, after the `tier_thresholds`/`tier_qtys` arrays are built (~line 85) and before the `if isinstance(self.strategy, NativeStrategySpec):` block, add:

```python
        # Map holdings to dense asset ids (sorted for deterministic insertion
        # order — equity() sums positions in insertion order). An asset outside
        # this window's universe cannot cross the boundary (out-of-range id is
        # UB in the engine), so reject it here with a clear error.
        try:
            indexed = sorted(
                ((p.asset_index[h.asset], h) for h in self.initial_holdings),
                key=lambda pair: pair[0],
            )
        except KeyError as e:
            raise ValueError(
                f"initial holding asset not in backtest universe: {e.args[0]}"
            ) from None
        holding_asset = np.array([aid for aid, _ in indexed], dtype=np.int32)
        holding_qty = np.array([h.quantity for _, h in indexed], dtype=np.int64)
        holding_cost = np.array([h.avg_cost for _, h in indexed], dtype=np.float64)
        holding_opened = np.array(
            [(h.opened_on - _EPOCH).days for _, h in indexed], dtype=np.int32
        )
```

- [ ] **Step 6: Pass the arrays into the `_engine.run_backtest` call**

In the `_engine.run_backtest(...)` keyword call (~line 119), add the four arguments immediately after `initial_cash=self.initial_cash,` and before `callback=callback,`:

```python
            initial_cash=self.initial_cash,
            holding_asset=holding_asset,
            holding_qty=holding_qty,
            holding_cost=holding_cost,
            holding_opened=holding_opened,
            callback=callback,
```

- [ ] **Step 7: Run the seeding tests to verify they pass**

Run: `uv run pytest tests/test_native_seeding.py -v`
Expected: PASS — all three cases green.

- [ ] **Step 8: Run the parity suite to confirm no regression**

Run: `uv run pytest tests/test_native_parity.py -v`
Expected: PASS — every existing parity case unchanged (no-holdings runs pass empty arrays; behavior identical).

- [ ] **Step 9: Run the four gates**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`
Expected: all PASS.

- [ ] **Step 10: Commit**

```bash
git add src/pkmn_quant/engine/native.py tests/test_native_seeding.py
git commit -m "feat(engine): NativeBacktest initial_holdings seeding via SeededHolding"
```

---

### Task 5: Full C++ + Python verification and CLAUDE.md note

**Files:**
- Modify: `CLAUDE.md` (engine layout note)

**Interfaces:**
- Consumes: everything from Tasks 1-4.
- Produces: a green full-suite run (both languages) and a one-line record in the engine docs that the native engine supports initial-holdings seeding.

- [ ] **Step 1: Run the full C++ test suite from clean**

Run: `cmake -S cpp -B cpp/build -DPKMN_BUILD_TESTS=ON -DCMAKE_BUILD_TYPE=Release && cmake --build cpp/build -j && ctest --test-dir cpp/build --output-on-failure`
Expected: PASS — all Catch2 tests, including the new seed + golden cases.

- [ ] **Step 2: Rebuild the extension and run the full Python suite**

Run: `uv sync --reinstall-package pkmn-quant && uv run pytest`
Expected: PASS — full suite green (the 3 dashboard tests still skip without the dashboard group, as documented).

- [ ] **Step 3: Add a note to CLAUDE.md**

In `CLAUDE.md`, in the `cpp/` layout bullet under "## Layout", append one sentence noting the new capability. Find the sentence describing `NativeBacktest` and add after it:

```
  The native engine additionally supports initial-holdings seeding
  (`SeededHolding` on `NativeBacktest`; C++ `Portfolio::seed` /
  `SeedPosition`) — a C++-only capability the Python engine deliberately
  lacks (Plan 2a, first step toward a C++-only engine), so it is pinned by
  Catch2 goldens + `tests/test_native_seeding.py`, not by parity.
```

- [ ] **Step 4: Run the four gates a final time**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: note native-engine initial-holdings seeding (Plan 2a)"
```

---

## Self-review notes

- **Spec coverage:** SeedPosition/seed (Task 1) ✓; run_backtest threading + default arg (Task 2) ✓; nanobind arrays (Task 3) ✓; SeededHolding + adapter validation/marshaling (Task 4) ✓; Catch2 goldens + Python integration test both present ✓; parity suite untouched and re-run ✓; Python engine unmodified (no task touches `engine/backtest.py`/`engine/portfolio.py`) ✓; rebuild discipline invoked at every cpp-edit boundary (Tasks 3, 5) ✓.
- **Type consistency:** `SeedPosition{asset, quantity, avg_cost, opened_on}` field order is identical in types.hpp, the Catch2 aggregate initializers, and the module.cpp marshaling. `SeededHolding(asset, quantity, avg_cost, opened_on)` matches across native.py and the test. `run_backtest`'s trailing `const std::vector<SeedPosition>&` is defaulted in the header only (not the definition), per C++ rules. The four kwargs `holding_asset/qty/cost/opened` have consistent dtypes (int32/int64/float64/int32) in module.cpp `Arr<...>`, the `nb::arg` registration, and the native.py `np.array(dtype=...)` calls.
- **Validation split honored:** universe membership in Python (Task 4 Step 5), quantity/avg_cost/duplicate in C++ (Task 1 Step 5), matching the spec.
