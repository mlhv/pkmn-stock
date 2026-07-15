# Plan 10: C++ Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the backtest engine and five rule strategies to C++20, validated bit-for-bit against the Python engine (which stays as the reference implementation), with a Python-callback bridge for ml-ranker and a `--engine {python,cpp}` CLI selector.

**Architecture:** A pure C++20 static library (`cpp/src/pkmn_engine/`, no Python/no I/O deps) with Catch2 unit tests, wrapped by one thin nanobind module (`pkmn_quant._engine`). Python adapter `NativeBacktest` crosses the boundary once per run: polars loads the warehouse exactly as today, flattens to numpy arrays, C++ runs the loop, results repackage into the existing `Result` dataclass. Metrics stay in Python (`summarize()` single-sourced).

**Tech Stack:** C++20, CMake ≥3.26, scikit-build-core (build backend, replaces hatchling), nanobind ≥2.2, Catch2 v3 (FetchContent), numpy at the boundary.

**Spec:** `docs/superpowers/specs/2026-07-14-cpp-engine-design.md`. Read it before starting any task.

## Global Constraints

- Bit-for-bit parity: same inputs ⇒ byte-identical fills and equity vs the Python engine. Differential tests assert with `==`, never `pytest.approx`.
- `-ffp-contract=off` on ALL C++ engine code (public flag on the core target). FMA contraction changes last-bit results; this is non-negotiable.
- Never `-ffast-math` or any reassociation flag.
- Mirror Python's arithmetic **operation order** exactly in every ported expression (left-associative, same grouping). Comments in ported code cite the Python file/line they mirror.
- Python `dict` semantics = `InsertionMap` (insertion-ordered). Python `sorted(..., key=...)` = `std::stable_sort` over insertion order. Python `list.sort(key=(a, b))` on candidate lists = `std::sort` with strict comparator `(a, b, asset_id)` — the extra `asset_id` closes ties Python leaves to polars `group_by` order, which Python itself does not guarantee (documented deviation, only reachable on exact float ties between sub-types of one product).
- Dates in C++ are `Day` = days since 1970-01-01 (`int32`); the adapter converts via `(d - date(1970,1,1)).days`. Calendar-day arithmetic (`timedelta`, `.days`) becomes integer subtraction — exact.
- All four gates before every commit: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`. Plus, from Task 1 on: the Catch2 suite (`ctest --test-dir cpp/build --output-on-failure`).
- Commit `pyproject.toml` and `uv.lock` together whenever deps change.
- After editing C++ sources, the installed extension is stale: run `uv sync --reinstall-package pkmn-quant` before `uv run pytest`.
- Workflow: STOP after each completed task, explain what/why at intern level, wait for explicit green light (per CLAUDE.md).
- Branch: `feat/cpp-engine` (already created; spec committed).

## File Map

Created:
- `cpp/CMakeLists.txt` — one build: core lib + nanobind module (SKBUILD only) + Catch2 tests (option)
- `cpp/src/pkmn_engine/types.hpp` — `AssetId`, `Day`, `Order`, `Fill`, `Position`, `InsertionMap`
- `cpp/src/pkmn_engine/costs.hpp` — `CostModel` (header-only)
- `cpp/src/pkmn_engine/version.cpp` / `version.hpp` — version string (keeps the static lib non-empty in Task 1)
- `cpp/src/pkmn_engine/portfolio.hpp` / `.cpp` — `Portfolio`
- `cpp/src/pkmn_engine/market.hpp` / `.cpp` — `MarketView`, `PriceRow`, `MarkEvent`, `ProductTable` (mid/low with NaN-as-missing replaces a separate quotes.hpp; deviation from spec noted, Quote had no behavior)
- `cpp/src/pkmn_engine/strategy.hpp` — `Context`, `Strategy` base
- `cpp/src/pkmn_engine/execution.hpp` / `.cpp` — `execute()`
- `cpp/src/pkmn_engine/backtest.hpp` / `.cpp` — `BacktestResult`, `run_backtest()`
- `cpp/src/pkmn_engine/strategies/buy_and_hold.hpp` / `.cpp`
- `cpp/src/pkmn_engine/strategies/sealed_accumulation.hpp` / `.cpp`
- `cpp/src/pkmn_engine/strategies/dip_buyer.hpp` / `.cpp`
- `cpp/src/pkmn_engine/strategies/momentum.hpp` / `.cpp`
- `cpp/src/pkmn_engine/strategies/cost_aware_reversion.hpp` / `.cpp`
- `cpp/src/pkmn_engine/strategies/callback.hpp` — `CallbackStrategy` (std::function, no Python)
- `cpp/src/pkmn_engine/strategies/factory.hpp` / `.cpp` — `make_strategy(name, params, universe_kind)`
- `cpp/tests/test_costs.cpp`, `test_insertion_map.cpp`, `test_portfolio.cpp`, `test_market.cpp`, `test_backtest_golden.cpp`, `test_strategies.cpp`
- `cpp/bindings/module.cpp` — nanobind glue
- `src/pkmn_quant/engine/native.py` — `NativeStrategySpec`, `NativeBacktest`, `NATIVE_STRATEGY_NAMES`
- `src/pkmn_quant/_engine.pyi` — mypy stub for the extension
- `tests/test_native_parity.py` — differential tests (synthetic fixtures)
- `scripts/parity_full.py` — full-data acceptance check (874 days, all strategies)
- `scripts/bench_engines.py` — measured speedup table

Modified:
- `pyproject.toml` — build backend hatchling → scikit-build-core; add numpy dep
- `.gitignore` — `cpp/build/`
- `.github/workflows/ci.yml` — Catch2 step
- `src/pkmn_quant/engine/data.py` — public `MarketData.mark_events()` accessor
- `src/pkmn_quant/research/walkforward.py` — `engine` + `strategy_name` params
- `src/pkmn_quant/cli.py` — `--engine` on backtest/walkforward; engine recorded in run config
- `tests/test_cli_backtest.py` — goldens parametrized over both engines
- `README.md`, `CLAUDE.md`, `docs/research-findings-2026-07.md` — Task 11

---

### Task 1: Build skeleton — scikit-build-core + CMake + nanobind hello + Catch2 harness

The riskiest integration step, done first with trivial content: after this task `uv sync` compiles a C++ extension importable as `pkmn_quant._engine`, and `ctest` runs one passing Catch2 test. No engine logic yet.

**Files:**
- Create: `cpp/CMakeLists.txt`, `cpp/src/pkmn_engine/version.hpp`, `cpp/src/pkmn_engine/version.cpp`, `cpp/bindings/module.cpp`, `cpp/tests/test_version.cpp`, `src/pkmn_quant/_engine.pyi`
- Modify: `pyproject.toml`, `.gitignore`, `.github/workflows/ci.yml`
- Test: `tests/test_native_import.py`

**Interfaces:**
- Produces: importable module `pkmn_quant._engine` with `__version__: str`; CMake targets `pkmn_engine_core` (static lib, `-ffp-contract=off` PUBLIC), `_engine` (nanobind module, SKBUILD only), `engine_tests` (Catch2, `-DPKMN_BUILD_TESTS=ON` only). Every later task adds sources to `pkmn_engine_core` and test files to `engine_tests`.

Prerequisite check (not a commit step): local builds need CMake and a C++ compiler. On this Mac: `xcode-select -p` must print a path (Command Line Tools) and `cmake --version` ≥ 3.26 — if cmake is missing, `brew install cmake ninja`. scikit-build-core downloads its own cmake/ninja wheels for the `uv sync` build, so this is only for running Catch2 locally.

- [ ] **Step 1: Write the failing Python import test**

`tests/test_native_import.py`:

```python
"""The native extension builds and imports; version pinned to the project."""


def test_engine_imports_with_version() -> None:
    from pkmn_quant import _engine

    assert _engine.__version__ == "0.1.0"
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `uv run pytest tests/test_native_import.py -v`
Expected: FAIL — `ImportError: cannot import name '_engine'`

- [ ] **Step 3: Switch pyproject.toml to scikit-build-core**

Replace the `[build-system]` and `[tool.hatch.build.targets.wheel]` sections (delete the hatch section) with:

```toml
[build-system]
requires = ["scikit-build-core>=0.10", "nanobind>=2.2"]
build-backend = "scikit_build_core.build"

[tool.scikit-build]
minimum-version = "0.10"
cmake.version = ">=3.26"
cmake.build-type = "Release"
cmake.source-dir = "cpp"
wheel.packages = ["src/pkmn_quant"]
build-dir = "build/{wheel_tag}"
```

Also add `"numpy>=1.26",` to `[project] dependencies` (alphabetical position: after `httpx`, before `optuna`) — the adapter (Task 6) hands numpy arrays across the boundary; declare it now so the lockfile changes once.

- [ ] **Step 4: Write cpp/CMakeLists.txt**

```cmake
cmake_minimum_required(VERSION 3.26)
project(pkmn_engine LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# The engine core: pure C++, no Python. Parity rule: FMA contraction changes
# last-bit float results vs CPython, so it is off for the core and everything
# that links it (PUBLIC).
add_library(pkmn_engine_core STATIC
  src/pkmn_engine/version.cpp
)
target_include_directories(pkmn_engine_core PUBLIC src)
target_compile_options(pkmn_engine_core PUBLIC -ffp-contract=off)

if(SKBUILD)
  find_package(Python 3.12 COMPONENTS Interpreter Development.Module REQUIRED)
  find_package(nanobind CONFIG REQUIRED)
  nanobind_add_module(_engine bindings/module.cpp)
  target_link_libraries(_engine PRIVATE pkmn_engine_core)
  install(TARGETS _engine LIBRARY DESTINATION pkmn_quant)
endif()

option(PKMN_BUILD_TESTS "Build Catch2 unit tests" OFF)
if(PKMN_BUILD_TESTS)
  include(FetchContent)
  FetchContent_Declare(
    catch2
    GIT_REPOSITORY https://github.com/catchorg/Catch2.git
    GIT_TAG v3.7.1
  )
  FetchContent_MakeAvailable(catch2)
  add_executable(engine_tests
    tests/test_version.cpp
  )
  target_link_libraries(engine_tests PRIVATE pkmn_engine_core Catch2::Catch2WithMain)
  include(CTest)
  list(APPEND CMAKE_MODULE_PATH ${catch2_SOURCE_DIR}/extras)
  include(Catch)
  catch_discover_tests(engine_tests)
endif()
```

Note: `find_package(nanobind CONFIG REQUIRED)` works because scikit-build-core puts the pip-installed nanobind's cmake dir on the prefix path automatically.

- [ ] **Step 5: Write the version files and binding module**

`cpp/src/pkmn_engine/version.hpp`:

```cpp
#pragma once

namespace pkmn {

const char* engine_version();

}  // namespace pkmn
```

`cpp/src/pkmn_engine/version.cpp`:

```cpp
#include "pkmn_engine/version.hpp"

namespace pkmn {

const char* engine_version() { return "0.1.0"; }

}  // namespace pkmn
```

`cpp/bindings/module.cpp`:

```cpp
#include <nanobind/nanobind.h>

#include "pkmn_engine/version.hpp"

namespace nb = nanobind;

NB_MODULE(_engine, m) {
    m.doc() = "pkmn_quant native backtest engine";
    m.attr("__version__") = pkmn::engine_version();
}
```

`cpp/tests/test_version.cpp`:

```cpp
#include <catch2/catch_test_macros.hpp>

#include <string>

#include "pkmn_engine/version.hpp"

TEST_CASE("engine reports its version") {
    REQUIRE(std::string(pkmn::engine_version()) == "0.1.0");
}
```

`src/pkmn_quant/_engine.pyi` (grows in Task 6):

```python
__version__: str
```

Append to `.gitignore`:

```
cpp/build/
build/
```

- [ ] **Step 6: Build and verify both paths**

```bash
uv sync --reinstall-package pkmn-quant
uv run pytest tests/test_native_import.py -v
cmake -S cpp -B cpp/build -DPKMN_BUILD_TESTS=ON -DCMAKE_BUILD_TYPE=Release
cmake --build cpp/build -j
ctest --test-dir cpp/build --output-on-failure
```

Expected: pytest PASS; ctest `100% tests passed, 1 test`.

- [ ] **Step 7: Run the full gate suite (whole test suite must still pass under the new backend)**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all green — the backend swap must not break packaging of the pure-Python code (`import pkmn_quant.cli` etc. all still work).

- [ ] **Step 8: Add the Catch2 step to CI**

In `.github/workflows/ci.yml`, append to the `checks` job steps (after the pytest step):

```yaml
      - name: C++ unit tests
        run: |
          cmake -S cpp -B cpp/build -DPKMN_BUILD_TESTS=ON -DCMAKE_BUILD_TYPE=Release
          cmake --build cpp/build -j
          ctest --test-dir cpp/build --output-on-failure
```

(ubuntu-latest ships cmake ≥3.26 and gcc; `uv sync --frozen` exercises the wheel build.)

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml uv.lock .gitignore .github/workflows/ci.yml cpp/ src/pkmn_quant/_engine.pyi tests/test_native_import.py
git commit -m "feat: C++ build skeleton — scikit-build-core, nanobind module, Catch2 harness"
```

---

### Task 2: Core types, InsertionMap, and CostModel

**Files:**
- Create: `cpp/src/pkmn_engine/types.hpp`, `cpp/src/pkmn_engine/costs.hpp`
- Modify: `cpp/CMakeLists.txt` (add test files to `engine_tests`)
- Test: `cpp/tests/test_insertion_map.cpp`, `cpp/tests/test_costs.cpp`

**Interfaces:**
- Produces: `pkmn::AssetId` (int32), `pkmn::Day` (int32, days since 1970-01-01), `pkmn::kNullDay`, `pkmn::Order{asset, quantity}`, `pkmn::Fill{day, asset, quantity, price, fees, impact}`, `pkmn::Position{quantity, avg_cost, opened_on}`, `pkmn::InsertionMap<V>` (ctor takes `n_keys`; `find`→`V*`/nullptr, `set`, `erase`, `contains`, `entries()`→insertion-ordered vector, `size()`), `pkmn::CostModel` with `max_daily_qty(market)`, `buy_impact(market, mid, qty, used)`, `sell_impact(market, low, qty, used)` — mid/low use **NaN as missing** (Python `None`).

- [ ] **Step 1: Write the failing Catch2 tests**

`cpp/tests/test_insertion_map.cpp`:

```cpp
#include <catch2/catch_test_macros.hpp>

#include "pkmn_engine/types.hpp"

using pkmn::InsertionMap;

TEST_CASE("InsertionMap preserves insertion order like a Python dict") {
    InsertionMap<int> m(10);
    m.set(7, 70);
    m.set(2, 20);
    m.set(5, 50);
    REQUIRE(m.size() == 3);
    // iteration order is insertion order, not key order
    REQUIRE(m.entries()[0].key == 7);
    REQUIRE(m.entries()[1].key == 2);
    REQUIRE(m.entries()[2].key == 5);
    // re-assigning an existing key keeps its original position
    m.set(2, 99);
    REQUIRE(m.entries()[1].key == 2);
    REQUIRE(m.entries()[1].value == 99);
    REQUIRE(*m.find(2) == 99);
    REQUIRE(m.find(3) == nullptr);
    REQUIRE(m.contains(5));
}

TEST_CASE("InsertionMap erase preserves relative order of survivors") {
    InsertionMap<int> m(10);
    m.set(7, 70);
    m.set(2, 20);
    m.set(5, 50);
    m.erase(2);
    REQUIRE(m.size() == 2);
    REQUIRE(m.entries()[0].key == 7);
    REQUIRE(m.entries()[1].key == 5);
    REQUIRE(m.find(2) == nullptr);
    REQUIRE(*m.find(5) == 50);  // index remap after erase
    m.set(2, 21);  // re-insert goes to the back (Python dict semantics)
    REQUIRE(m.entries()[2].key == 2);
}
```

`cpp/tests/test_costs.cpp`:

```cpp
#include <catch2/catch_test_macros.hpp>

#include <cmath>
#include <limits>

#include "pkmn_engine/costs.hpp"

using pkmn::CostModel;

namespace {
constexpr double kNaN = std::numeric_limits<double>::quiet_NaN();
}

TEST_CASE("max_daily_qty: strict < at tier thresholds (costs.py:55-61)") {
    CostModel cm;
    CHECK(cm.max_daily_qty(4.99) == 20);
    CHECK(cm.max_daily_qty(5.0) == 8);  // exactly at threshold -> NEXT tier
    CHECK(cm.max_daily_qty(49.99) == 8);
    CHECK(cm.max_daily_qty(50.0) == 3);
    CHECK(cm.max_daily_qty(200.0) == 1);  // above last tier -> fallback
}

TEST_CASE("buy_impact matches the hand-derived golden (test_cli_backtest.py)") {
    CostModel cm;
    cm.impact_enabled = true;
    // $12 price -> cap 8; spread mid-market = 16-12 = 4.
    // impact(qty=7, used=0) = 4 * 7 * 7 / (2*8) = 12.25 exactly.
    CHECK(cm.buy_impact(12.0, 16.0, 7, 0) == 12.25);
    CHECK(cm.buy_impact(12.0, 16.0, 8, 0) == 16.0);
    // depth-aware: used shifts the walk deeper: 4 * 2 * (2*3 + 2) / 16 = 4.0
    CHECK(cm.buy_impact(12.0, 16.0, 2, 3) == 4.0);
}

TEST_CASE("impact is zero when disabled, missing, crossed, or qty<=0") {
    CostModel cm;  // impact_enabled defaults false
    CHECK(cm.buy_impact(12.0, 16.0, 5, 0) == 0.0);
    cm.impact_enabled = true;
    CHECK(cm.buy_impact(12.0, kNaN, 5, 0) == 0.0);   // missing mid
    CHECK(cm.sell_impact(12.0, kNaN, 5, 0) == 0.0);  // missing low
    CHECK(cm.buy_impact(12.0, 11.0, 5, 0) == 0.0);   // crossed: mid < market
    CHECK(cm.sell_impact(12.0, 13.0, 5, 0) == 0.0);  // crossed: low > market
    CHECK(cm.buy_impact(12.0, 16.0, 0, 0) == 0.0);
}

TEST_CASE("sell_impact walks market toward low") {
    CostModel cm;
    cm.impact_enabled = true;
    // spread market-low = 12-10 = 2; qty 4 used 0 at cap 8: 2*4*4/16 = 2.0
    CHECK(cm.sell_impact(12.0, 10.0, 4, 0) == 2.0);
}
```

- [ ] **Step 2: Add the test files to CMake and verify they fail to build**

In `cpp/CMakeLists.txt`, extend `add_executable(engine_tests ...)`:

```cmake
  add_executable(engine_tests
    tests/test_version.cpp
    tests/test_insertion_map.cpp
    tests/test_costs.cpp
  )
```

Run: `cmake --build cpp/build -j`
Expected: compile FAILURE — `pkmn_engine/types.hpp: No such file or directory`.

- [ ] **Step 3: Write types.hpp**

`cpp/src/pkmn_engine/types.hpp`:

```cpp
#pragma once

#include <cstddef>
#include <cstdint>
#include <limits>
#include <vector>

namespace pkmn {

// Dense asset index assigned by the Python adapter: assets sorted by
// (product_id, sub_type), ids 0..n_assets-1. Comparing AssetId therefore
// orders by (product_id, sub_type).
using AssetId = std::int32_t;

// Days since 1970-01-01 (polars Date physical repr). Calendar arithmetic
// (Python timedelta / .days) is plain integer subtraction.
using Day = std::int32_t;

inline constexpr Day kNullDay = std::numeric_limits<Day>::min();

// Mirrors engine/execution.py Order.
struct Order {
    AssetId asset;
    std::int64_t quantity;  // > 0 buy, < 0 sell
};

// Mirrors engine/portfolio.py Fill.
struct Fill {
    Day day;
    AssetId asset;
    std::int64_t quantity;
    double price;
    double fees;
    double impact;
};

// Mirrors engine/portfolio.py Position. opened_on is always set by engine
// fills (Python's None case exists only for hand-built test portfolios,
// which cannot reach the C++ engine).
struct Position {
    std::int64_t quantity;
    double avg_cost;
    Day opened_on;
};

// Insertion-ordered map with dense int keys — Python dict semantics:
// iteration in first-insertion order, re-assignment keeps position,
// erase + re-insert moves to the back. O(1) find/set; erase is O(n)
// (n = live entries, small: positions/filled_today).
template <typename V>
class InsertionMap {
  public:
    struct Entry {
        AssetId key;
        V value;
    };

    explicit InsertionMap(std::size_t n_keys) : index_(n_keys, -1) {}

    V* find(AssetId k) {
        auto i = index_[static_cast<std::size_t>(k)];
        return i < 0 ? nullptr : &entries_[static_cast<std::size_t>(i)].value;
    }

    const V* find(AssetId k) const {
        auto i = index_[static_cast<std::size_t>(k)];
        return i < 0 ? nullptr : &entries_[static_cast<std::size_t>(i)].value;
    }

    bool contains(AssetId k) const { return index_[static_cast<std::size_t>(k)] >= 0; }

    void set(AssetId k, V v) {
        auto& slot = index_[static_cast<std::size_t>(k)];
        if (slot < 0) {
            slot = static_cast<std::int64_t>(entries_.size());
            entries_.push_back(Entry{k, std::move(v)});
        } else {
            entries_[static_cast<std::size_t>(slot)].value = std::move(v);
        }
    }

    void erase(AssetId k) {
        auto i = index_[static_cast<std::size_t>(k)];
        if (i < 0) return;
        entries_.erase(entries_.begin() + i);
        index_[static_cast<std::size_t>(k)] = -1;
        for (std::size_t j = static_cast<std::size_t>(i); j < entries_.size(); ++j) {
            index_[static_cast<std::size_t>(entries_[j].key)] = static_cast<std::int64_t>(j);
        }
    }

    const std::vector<Entry>& entries() const { return entries_; }
    std::size_t size() const { return entries_.size(); }

  private:
    std::vector<Entry> entries_;       // insertion order
    std::vector<std::int64_t> index_;  // AssetId -> position in entries_, or -1
};

}  // namespace pkmn
```

- [ ] **Step 4: Write costs.hpp**

`cpp/src/pkmn_engine/costs.hpp`:

```cpp
#pragma once

// Port of engine/costs.py. Every arithmetic expression mirrors the Python
// operation order exactly (bit-for-bit parity contract).

#include <cmath>
#include <cstdint>
#include <utility>
#include <vector>

namespace pkmn {

struct CostModel {
    double fee_rate = 0.1275;
    double shipping_per_line = 1.0;
    // (price threshold, units per asset per day); strict < per tier.
    std::vector<std::pair<double, std::int64_t>> liquidity_tiers = {
        {5.0, 20}, {50.0, 8}, {200.0, 3}};
    std::int64_t fallback_max_qty = 1;
    bool impact_enabled = false;

    // costs.py:55-61 — strict <: a price exactly at a threshold falls to
    // the NEXT tier.
    std::int64_t max_daily_qty(double market) const {
        for (const auto& [threshold, qty] : liquidity_tiers) {
            if (market < threshold) return qty;
        }
        return fallback_max_qty;
    }

    // costs.py:63-72 — mid NaN = Python None (missing).
    double buy_impact(double market, double mid, std::int64_t qty, std::int64_t used) const {
        return impact_(market, mid, market, qty, used);
    }

    // costs.py:74-76
    double sell_impact(double market, double low, std::int64_t qty, std::int64_t used) const {
        return impact_(market, market, low, qty, used);
    }

  private:
    // costs.py:78-87. Python: spread * qty * (2 * used + qty) / (2 * q_cap)
    // — evaluated left-to-right; (2*used + qty) and (2*q_cap) are exact
    // int-to-double conversions at these magnitudes.
    double impact_(double market, double upper, double lower, std::int64_t qty,
                   std::int64_t used) const {
        if (!impact_enabled || qty <= 0 || std::isnan(upper) || std::isnan(lower)) return 0.0;
        double spread = upper - lower;
        if (spread <= 0.0) return 0.0;
        std::int64_t q_cap = max_daily_qty(market);
        return spread * static_cast<double>(qty) * static_cast<double>(2 * used + qty) /
               static_cast<double>(2 * q_cap);
    }
};

}  // namespace pkmn
```

- [ ] **Step 5: Build and run the Catch2 suite**

```bash
cmake --build cpp/build -j && ctest --test-dir cpp/build --output-on-failure
```

Expected: PASS (6 tests).

- [ ] **Step 6: Run the Python gates (nothing Python-side changed, but keep the habit)**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: green.

- [ ] **Step 7: Commit**

```bash
git add cpp/
git commit -m "feat(cpp): core types, InsertionMap, CostModel with impact parity tests"
```

---

### Task 3: Portfolio

**Files:**
- Create: `cpp/src/pkmn_engine/portfolio.hpp`, `cpp/src/pkmn_engine/portfolio.cpp`
- Modify: `cpp/CMakeLists.txt` (add `src/pkmn_engine/portfolio.cpp` to `pkmn_engine_core`, `tests/test_portfolio.cpp` to `engine_tests`)
- Test: `cpp/tests/test_portfolio.cpp`

**Interfaces:**
- Consumes: `types.hpp` (`Fill`, `Position`, `InsertionMap`).
- Produces: `pkmn::Portfolio(double cash, std::size_t n_assets)` with public `cash`, `realized_pnl`, `positions` (`InsertionMap<Position>`), `void apply(const Fill&)` (throws `std::invalid_argument` on bad fills, mirrors Python ValueError), `double equity(const InsertionMap<double>& marks) const` (throws `std::out_of_range` on missing mark, mirrors Python KeyError).

- [ ] **Step 1: Write the failing tests**

`cpp/tests/test_portfolio.cpp`:

```cpp
#include <catch2/catch_test_macros.hpp>

#include <stdexcept>

#include "pkmn_engine/portfolio.hpp"

using pkmn::Fill;
using pkmn::InsertionMap;
using pkmn::Portfolio;

TEST_CASE("buy updates cash, avg cost, and realized pnl like portfolio.py:_buy") {
    Portfolio pf(100.0, 4);
    pf.apply(Fill{100, 0, 8, 12.0, 1.0, 0.0});
    CHECK(pf.cash == 3.0);  // 100 - 96 - 1 (golden arithmetic)
    CHECK(pf.realized_pnl == -1.0);
    const auto* pos = pf.positions.find(0);
    REQUIRE(pos != nullptr);
    CHECK(pos->quantity == 8);
    CHECK(pos->avg_cost == 12.0);
    CHECK(pos->opened_on == 100);
}

TEST_CASE("adding to a position averages cost, keeps opened_on") {
    Portfolio pf(1000.0, 4);
    pf.apply(Fill{100, 0, 2, 10.0, 1.0, 0.0});
    pf.apply(Fill{101, 0, 2, 20.0, 1.0, 0.0});
    const auto* pos = pf.positions.find(0);
    REQUIRE(pos != nullptr);
    CHECK(pos->quantity == 4);
    CHECK(pos->avg_cost == 15.0);  // (10*2 + 40) / 4
    CHECK(pos->opened_on == 100);  // unchanged by the add
}

TEST_CASE("sell realizes pnl and a full close removes the position") {
    Portfolio pf(100.0, 4);
    pf.apply(Fill{100, 0, 4, 10.0, 1.0, 0.0});  // cash 59
    pf.apply(Fill{101, 0, -4, 15.0, 2.0, 0.5});
    // proceeds 60; cash 59 + 60 - 2 - 0.5 = 116.5
    CHECK(pf.cash == 116.5);
    // realized: -1 (buy fee) + (60 - 40 - 2 - 0.5) = 16.5
    CHECK(pf.realized_pnl == 16.5);
    CHECK(pf.positions.find(0) == nullptr);
}

TEST_CASE("oversell and zero-qty fills throw like portfolio.py") {
    Portfolio pf(100.0, 4);
    pf.apply(Fill{100, 0, 2, 10.0, 1.0, 0.0});
    CHECK_THROWS_AS(pf.apply(Fill{101, 0, -3, 10.0, 1.0, 0.0}), std::invalid_argument);
    CHECK_THROWS_AS(pf.apply(Fill{101, 1, -1, 10.0, 1.0, 0.0}), std::invalid_argument);
    CHECK_THROWS_AS(pf.apply(Fill{101, 0, 0, 10.0, 1.0, 0.0}), std::invalid_argument);
    // Fill.__post_init__ validation lives in apply(): price/fees/impact
    CHECK_THROWS_AS(pf.apply(Fill{101, 0, 1, 0.0, 1.0, 0.0}), std::invalid_argument);
    CHECK_THROWS_AS(pf.apply(Fill{101, 0, 1, 10.0, -1.0, 0.0}), std::invalid_argument);
    CHECK_THROWS_AS(pf.apply(Fill{101, 0, 1, 10.0, 1.0, -0.5}), std::invalid_argument);
}

TEST_CASE("equity sums positions in insertion order; missing mark throws") {
    Portfolio pf(10.0, 4);
    pf.apply(Fill{100, 2, 1, 5.0, 0.5, 0.0});
    pf.apply(Fill{100, 0, 1, 3.0, 0.5, 0.0});
    InsertionMap<double> marks(4);
    marks.set(2, 6.0);
    marks.set(0, 4.0);
    CHECK(pf.equity(marks) == 10.0 - 5.5 - 3.5 + 6.0 + 4.0);
    InsertionMap<double> missing(4);
    missing.set(2, 6.0);
    CHECK_THROWS_AS(pf.equity(missing), std::out_of_range);
}
```

- [ ] **Step 2: Add to CMake, verify build failure**

Add `src/pkmn_engine/portfolio.cpp` under `pkmn_engine_core` sources and `tests/test_portfolio.cpp` under `engine_tests`.

Run: `cmake --build cpp/build -j`
Expected: FAIL — missing `pkmn_engine/portfolio.hpp`.

- [ ] **Step 3: Write portfolio.hpp / portfolio.cpp**

`cpp/src/pkmn_engine/portfolio.hpp`:

```cpp
#pragma once

// Port of engine/portfolio.py (Positions, cash, average-cost P&L).

#include <cstddef>

#include "pkmn_engine/types.hpp"

namespace pkmn {

class Portfolio {
  public:
    Portfolio(double cash, std::size_t n_assets) : cash(cash), positions(n_assets) {}

    double cash;
    double realized_pnl = 0.0;
    InsertionMap<Position> positions;

    // portfolio.py:64-71 + Fill.__post_init__ validation (portfolio.py:34-40).
    void apply(const Fill& f);

    // portfolio.py:100-108. Sums in positions insertion order (Python dict
    // iteration order) — parity-relevant because float addition is not
    // associative. Throws std::out_of_range on a missing mark (KeyError).
    double equity(const InsertionMap<double>& marks) const;

  private:
    void buy_(const Fill& f);
    void sell_(const Fill& f);
};

}  // namespace pkmn
```

`cpp/src/pkmn_engine/portfolio.cpp`:

```cpp
#include "pkmn_engine/portfolio.hpp"

#include <stdexcept>
#include <string>

namespace pkmn {

void Portfolio::apply(const Fill& f) {
    // Fill.__post_init__ (portfolio.py:34-40)
    if (f.price <= 0.0) throw std::invalid_argument("Fill.price must be positive");
    if (f.fees < 0.0) throw std::invalid_argument("Fill.fees must be non-negative");
    if (f.impact < 0.0) throw std::invalid_argument("Fill.impact must be non-negative");
    // portfolio.py:64-71 (ledger list is not kept: the engine returns its
    // own fills vector; Python's Portfolio.ledger is never read by the loop)
    if (f.quantity == 0) throw std::invalid_argument("zero-quantity fill");
    if (f.quantity > 0) {
        buy_(f);
    } else {
        sell_(f);
    }
}

void Portfolio::buy_(const Fill& f) {
    // portfolio.py:73-85 — same expression grouping.
    double cost = static_cast<double>(f.quantity) * f.price;
    cash -= cost + f.fees + f.impact;
    realized_pnl -= f.fees + f.impact;
    Position* pos = positions.find(f.asset);
    if (pos == nullptr) {
        positions.set(f.asset, Position{f.quantity, f.price, f.day});
    } else {
        double total_cost = pos->avg_cost * static_cast<double>(pos->quantity) + cost;
        pos->quantity += f.quantity;
        pos->avg_cost = total_cost / static_cast<double>(pos->quantity);
    }
}

void Portfolio::sell_(const Fill& f) {
    // portfolio.py:87-98 — same expression grouping.
    std::int64_t qty = -f.quantity;
    Position* pos = positions.find(f.asset);
    if (pos == nullptr || pos->quantity < qty) {
        std::int64_t held = pos ? pos->quantity : 0;
        throw std::invalid_argument("cannot sell " + std::to_string(qty) + ": hold " +
                                    std::to_string(held));
    }
    double proceeds = static_cast<double>(qty) * f.price;
    cash += proceeds - f.fees - f.impact;
    realized_pnl += proceeds - static_cast<double>(qty) * pos->avg_cost - f.fees - f.impact;
    pos->quantity -= qty;
    if (pos->quantity == 0) positions.erase(f.asset);
}

double Portfolio::equity(const InsertionMap<double>& marks) const {
    // portfolio.py:100-108: sum() starts at 0 and adds in dict order.
    double value = 0.0;
    for (const auto& e : positions.entries()) {
        const double* m = marks.find(e.key);
        if (m == nullptr) throw std::out_of_range("no mark for held asset");
        value += static_cast<double>(e.value.quantity) * *m;
    }
    return cash + value;
}

}  // namespace pkmn
```

- [ ] **Step 4: Build, run tests**

Run: `cmake --build cpp/build -j && ctest --test-dir cpp/build --output-on-failure`
Expected: PASS (11 tests).

- [ ] **Step 5: Commit**

```bash
git add cpp/
git commit -m "feat(cpp): Portfolio with average-cost accounting, parity-ordered equity"
```

---

### Task 4: MarketView — day partitions, marks cursor, history queries

**Files:**
- Create: `cpp/src/pkmn_engine/market.hpp`, `cpp/src/pkmn_engine/market.cpp`
- Modify: `cpp/CMakeLists.txt` (add `src/pkmn_engine/market.cpp`, `tests/test_market.cpp`)
- Test: `cpp/tests/test_market.cpp`

**Interfaces:**
- Consumes: `types.hpp`.
- Produces:
  - `pkmn::PriceRow{Day day; AssetId asset; double market; double mid; double low;}` (mid/low NaN = missing)
  - `pkmn::MarkEvent{Day day; AssetId asset; double price;}`
  - `pkmn::ProductTable{std::vector<std::int64_t> product_id; std::vector<std::int8_t> kind; std::vector<Day> released_on;}` — per-asset, indexed by AssetId; kind codes `0`=sealed `1`=single `-1`=other; `released_on` = `kNullDay` when null; `std::size_t n_assets() const`
  - `pkmn::MarketView(std::size_t n_assets, std::vector<Day> trading_days, std::vector<PriceRow> rows, std::vector<MarkEvent> events)` — throws `std::invalid_argument` on unsorted inputs or out-of-range asset ids
  - `n_assets()`, `days()` (trading days only), `reset()`
  - `load_day(Day)` then `price(AssetId)` / `mid(AssetId)` / `low(AssetId)` → double, NaN when the asset did not print that day (mirrors `prices_on`/`quotes_on`: no carry-forward)
  - `const InsertionMap<double>& marks_until(Day)` — carry-forward marks cursor; monotone only, throws `std::logic_error` on a backwards query (the Python replay path is never hit by the loop)
  - history queries (per-asset, binary search over date-sorted series): `std::optional<double> last_price_at_or_before(AssetId, Day)`, `std::optional<double> peak_until(AssetId, Day)` (prefix max), `std::optional<double> max_in_window(AssetId, Day from, Day to)`

Design note for the implementer: `rows` arrive date-sorted (the adapter sorts by date; within-day order is irrelevant because per-day lookups are keyed by asset and (day, asset) pairs are unique). `events` arrive in the EXACT order of Python's `MarketData._marks_rows` — replaying them through `InsertionMap::set` reproduces the Python marks dict including its insertion order, which `buy-and-hold` iteration depends on. This is why events are passed in rather than derived in C++.

- [ ] **Step 1: Write the failing tests**

`cpp/tests/test_market.cpp`:

```cpp
#include <catch2/catch_test_macros.hpp>

#include <cmath>
#include <limits>
#include <stdexcept>

#include "pkmn_engine/market.hpp"

using pkmn::AssetId;
using pkmn::Day;
using pkmn::MarketView;
using pkmn::MarkEvent;
using pkmn::PriceRow;

namespace {
constexpr double kNaN = std::numeric_limits<double>::quiet_NaN();

MarketView make_view() {
    // asset 0 prints on days 100,101,103; asset 1 on 100,103 (gap on 101).
    // day 102 is a trading day with no prints at all.
    std::vector<Day> days{100, 101, 102, 103};
    std::vector<PriceRow> rows{
        {100, 0, 10.0, 11.0, 9.0},
        {100, 1, 50.0, kNaN, 45.0},
        {101, 0, 12.0, 14.0, 11.0},
        {103, 0, 8.0, 9.0, 7.0},
        {103, 1, 60.0, 66.0, kNaN},
    };
    std::vector<MarkEvent> events{
        {100, 0, 10.0}, {100, 1, 50.0}, {101, 0, 12.0}, {103, 0, 8.0}, {103, 1, 60.0}};
    return MarketView(2, days, rows, events);
}
}  // namespace

TEST_CASE("load_day exposes prints without carry-forward (data.py prices_on)") {
    auto mkt = make_view();
    mkt.load_day(101);
    CHECK(mkt.price(0) == 12.0);
    CHECK(std::isnan(mkt.price(1)));  // gap day for asset 1: no stale fill price
    CHECK(mkt.mid(0) == 14.0);
    mkt.load_day(102);
    CHECK(std::isnan(mkt.price(0)));
    mkt.load_day(103);
    CHECK(mkt.price(1) == 60.0);
    CHECK(std::isnan(mkt.low(1)));  // null low stays missing
}

TEST_CASE("marks cursor carries forward and preserves event insertion order") {
    auto mkt = make_view();
    const auto& m1 = mkt.marks_until(101);
    CHECK(*m1.find(0) == 12.0);
    CHECK(*m1.find(1) == 50.0);  // carried from day 100
    // insertion order: asset 0 entered first (event order)
    CHECK(m1.entries()[0].key == 0);
    const auto& m2 = mkt.marks_until(103);
    CHECK(*m2.find(1) == 60.0);
    CHECK_THROWS_AS(mkt.marks_until(100), std::logic_error);  // monotone only
    mkt.reset();
    const auto& m3 = mkt.marks_until(100);
    CHECK(*m3.find(0) == 10.0);
    CHECK(m3.size() == 2);
}

TEST_CASE("history queries: last-at-or-before, prefix peak, window max") {
    auto mkt = make_view();
    CHECK(mkt.last_price_at_or_before(0, 99) == std::nullopt);
    CHECK(*mkt.last_price_at_or_before(0, 100) == 10.0);
    CHECK(*mkt.last_price_at_or_before(0, 102) == 12.0);  // carry across gap
    CHECK(*mkt.last_price_at_or_before(1, 102) == 50.0);
    CHECK(*mkt.peak_until(0, 103) == 12.0);
    CHECK(*mkt.peak_until(0, 100) == 10.0);
    CHECK(*mkt.max_in_window(0, 101, 103) == 12.0);
    CHECK(*mkt.max_in_window(0, 102, 103) == 8.0);
    CHECK(mkt.max_in_window(0, 104, 110) == std::nullopt);
}

TEST_CASE("constructor validates sortedness and asset range") {
    std::vector<Day> days{100, 100};  // not strictly increasing
    CHECK_THROWS_AS(MarketView(1, days, {}, {}), std::invalid_argument);
    std::vector<Day> ok{100};
    std::vector<PriceRow> bad_rows{{101, 0, 1.0, kNaN, kNaN}, {100, 0, 1.0, kNaN, kNaN}};
    CHECK_THROWS_AS(MarketView(1, ok, bad_rows, {}), std::invalid_argument);
    std::vector<PriceRow> bad_asset{{100, 5, 1.0, kNaN, kNaN}};
    CHECK_THROWS_AS(MarketView(1, ok, bad_asset, {}), std::invalid_argument);
}
```

- [ ] **Step 2: Add to CMake, verify build failure**

Run: `cmake --build cpp/build -j` → FAIL, missing `pkmn_engine/market.hpp`.

- [ ] **Step 3: Write market.hpp**

`cpp/src/pkmn_engine/market.hpp`:

```cpp
#pragma once

// Port of engine/data.py MarketData, restructured for arrays: eager day
// partition (epoch-stamped dense lookups), incremental marks cursor, and
// per-asset CSR series for the strategy history queries.

#include <cstddef>
#include <cstdint>
#include <optional>
#include <unordered_map>
#include <utility>
#include <vector>

#include "pkmn_engine/types.hpp"

namespace pkmn {

struct PriceRow {
    Day day;
    AssetId asset;
    double market;
    double mid;  // NaN = source row had no value (Python None)
    double low;  // NaN = missing
};

struct MarkEvent {
    Day day;
    AssetId asset;
    double price;
};

// Per-asset product attributes, indexed by AssetId.
struct ProductTable {
    std::vector<std::int64_t> product_id;
    std::vector<std::int8_t> kind;  // 0 sealed, 1 single, -1 other
    std::vector<Day> released_on;   // kNullDay when null

    std::size_t n_assets() const { return product_id.size(); }
};

class MarketView {
  public:
    MarketView(std::size_t n_assets, std::vector<Day> trading_days,
               std::vector<PriceRow> rows, std::vector<MarkEvent> events);

    std::size_t n_assets() const { return n_assets_; }
    const std::vector<Day>& days() const { return trading_days_; }

    // Restart the marks cursor and current-day tables (run_backtest calls
    // this first so repeated runs on one view are independent).
    void reset();

    // Stamp the day's prints into the dense current-day tables. O(rows that
    // day), amortized O(1) queries after.
    void load_day(Day day);
    double price(AssetId a) const { return current_(cur_market_, a); }
    double mid(AssetId a) const { return current_(cur_mid_, a); }
    double low(AssetId a) const { return current_(cur_low_, a); }

    // data.py marks_on: carry-forward marks as of `day`. Monotone only —
    // the event loop never goes backwards; a backwards query is a bug.
    const InsertionMap<double>& marks_until(Day day);

    // Strategy history queries (anti-look-ahead: callers pass day <= today).
    std::optional<double> last_price_at_or_before(AssetId a, Day d) const;
    std::optional<double> peak_until(AssetId a, Day d) const;
    std::optional<double> max_in_window(AssetId a, Day from, Day to) const;

  private:
    double current_(const std::vector<double>& table, AssetId a) const;
    std::pair<std::size_t, std::size_t> range_(AssetId a) const;

    std::size_t n_assets_;
    std::vector<Day> trading_days_;
    std::vector<PriceRow> rows_;  // date-sorted
    std::vector<MarkEvent> events_;

    // day -> [begin, end) into rows_
    std::unordered_map<Day, std::pair<std::size_t, std::size_t>> day_ranges_;

    // current-day tables, epoch-stamped so load_day is O(day's rows)
    std::vector<double> cur_market_, cur_mid_, cur_low_;
    std::vector<std::uint32_t> stamp_;
    std::uint32_t epoch_ = 0;

    // per-asset CSR over rows_ (day-sorted within each asset)
    std::vector<std::size_t> h_off_;   // n_assets_+1
    std::vector<Day> h_day_;
    std::vector<double> h_price_;
    std::vector<double> h_prefmax_;    // running max within each asset slice

    // marks cursor
    InsertionMap<double> marks_;
    std::size_t ev_idx_ = 0;
    Day watermark_ = kNullDay;
};

}  // namespace pkmn
```

- [ ] **Step 4: Write market.cpp**

`cpp/src/pkmn_engine/market.cpp`:

```cpp
#include "pkmn_engine/market.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>

namespace pkmn {

namespace {
constexpr double kNaN = std::numeric_limits<double>::quiet_NaN();
}

MarketView::MarketView(std::size_t n_assets, std::vector<Day> trading_days,
                       std::vector<PriceRow> rows, std::vector<MarkEvent> events)
    : n_assets_(n_assets),
      trading_days_(std::move(trading_days)),
      rows_(std::move(rows)),
      events_(std::move(events)),
      cur_market_(n_assets, kNaN),
      cur_mid_(n_assets, kNaN),
      cur_low_(n_assets, kNaN),
      stamp_(n_assets, 0),
      marks_(n_assets) {
    for (std::size_t i = 1; i < trading_days_.size(); ++i) {
        if (trading_days_[i] <= trading_days_[i - 1])
            throw std::invalid_argument("trading_days must be strictly increasing");
    }
    for (std::size_t i = 0; i < rows_.size(); ++i) {
        const auto& r = rows_[i];
        if (r.asset < 0 || static_cast<std::size_t>(r.asset) >= n_assets_)
            throw std::invalid_argument("PriceRow.asset out of range");
        if (i > 0 && r.day < rows_[i - 1].day)
            throw std::invalid_argument("rows must be date-sorted");
    }
    for (std::size_t i = 0; i < events_.size(); ++i) {
        const auto& e = events_[i];
        if (e.asset < 0 || static_cast<std::size_t>(e.asset) >= n_assets_)
            throw std::invalid_argument("MarkEvent.asset out of range");
        if (i > 0 && e.day < events_[i - 1].day)
            throw std::invalid_argument("events must be date-sorted");
    }

    // day partition
    std::size_t begin = 0;
    for (std::size_t i = 0; i <= rows_.size(); ++i) {
        if (i == rows_.size() || (i > 0 && rows_[i].day != rows_[begin].day)) {
            if (i > begin) day_ranges_[rows_[begin].day] = {begin, i};
            begin = i;
        }
    }

    // per-asset CSR (stable: rows_ is date-sorted, so each slice is too)
    std::vector<std::size_t> counts(n_assets_, 0);
    for (const auto& r : rows_) ++counts[static_cast<std::size_t>(r.asset)];
    h_off_.assign(n_assets_ + 1, 0);
    for (std::size_t a = 0; a < n_assets_; ++a) h_off_[a + 1] = h_off_[a] + counts[a];
    h_day_.resize(rows_.size());
    h_price_.resize(rows_.size());
    std::vector<std::size_t> cursor(h_off_.begin(), h_off_.end() - 1);
    for (const auto& r : rows_) {
        auto& c = cursor[static_cast<std::size_t>(r.asset)];
        h_day_[c] = r.day;
        h_price_[c] = r.market;
        ++c;
    }
    h_prefmax_.resize(rows_.size());
    for (std::size_t a = 0; a < n_assets_; ++a) {
        double running = -std::numeric_limits<double>::infinity();
        for (std::size_t i = h_off_[a]; i < h_off_[a + 1]; ++i) {
            running = std::max(running, h_price_[i]);
            h_prefmax_[i] = running;
        }
    }
}

void MarketView::reset() {
    ++epoch_;  // invalidates all current-day stamps
    marks_ = InsertionMap<double>(n_assets_);
    ev_idx_ = 0;
    watermark_ = kNullDay;
}

void MarketView::load_day(Day day) {
    ++epoch_;
    auto it = day_ranges_.find(day);
    if (it == day_ranges_.end()) return;  // trading day with no prints
    for (std::size_t i = it->second.first; i < it->second.second; ++i) {
        const auto& r = rows_[i];
        auto a = static_cast<std::size_t>(r.asset);
        cur_market_[a] = r.market;
        cur_mid_[a] = r.mid;
        cur_low_[a] = r.low;
        stamp_[a] = epoch_;
    }
}

double MarketView::current_(const std::vector<double>& table, AssetId a) const {
    auto i = static_cast<std::size_t>(a);
    return stamp_[i] == epoch_ ? table[i] : kNaN;
}

const InsertionMap<double>& MarketView::marks_until(Day day) {
    if (watermark_ != kNullDay && day < watermark_)
        throw std::logic_error("marks_until must be queried in non-decreasing day order");
    while (ev_idx_ < events_.size() && events_[ev_idx_].day <= day) {
        marks_.set(events_[ev_idx_].asset, events_[ev_idx_].price);
        ++ev_idx_;
    }
    watermark_ = day;
    return marks_;
}

std::pair<std::size_t, std::size_t> MarketView::range_(AssetId a) const {
    auto i = static_cast<std::size_t>(a);
    return {h_off_[i], h_off_[i + 1]};
}

std::optional<double> MarketView::last_price_at_or_before(AssetId a, Day d) const {
    auto [b, e] = range_(a);
    auto it = std::upper_bound(h_day_.begin() + b, h_day_.begin() + e, d);
    if (it == h_day_.begin() + b) return std::nullopt;
    return h_price_[static_cast<std::size_t>(it - h_day_.begin()) - 1];
}

std::optional<double> MarketView::peak_until(AssetId a, Day d) const {
    auto [b, e] = range_(a);
    auto it = std::upper_bound(h_day_.begin() + b, h_day_.begin() + e, d);
    if (it == h_day_.begin() + b) return std::nullopt;
    return h_prefmax_[static_cast<std::size_t>(it - h_day_.begin()) - 1];
}

std::optional<double> MarketView::max_in_window(AssetId a, Day from, Day to) const {
    auto [b, e] = range_(a);
    auto lo = std::lower_bound(h_day_.begin() + b, h_day_.begin() + e, from) - h_day_.begin();
    auto hi = std::upper_bound(h_day_.begin() + b, h_day_.begin() + e, to) - h_day_.begin();
    if (lo >= hi) return std::nullopt;
    double m = h_price_[static_cast<std::size_t>(lo)];
    for (auto i = lo + 1; i < hi; ++i)
        m = std::max(m, h_price_[static_cast<std::size_t>(i)]);
    return m;
}

}  // namespace pkmn
```

- [ ] **Step 5: Build, run tests, commit**

```bash
cmake --build cpp/build -j && ctest --test-dir cpp/build --output-on-failure
git add cpp/
git commit -m "feat(cpp): MarketView — day partition, marks cursor, CSR history queries"
```

---

### Task 5: Execution, backtest loop, BuyAndHold — the C++ golden tests

Reproduces the two hand-derived golden scenarios from `tests/test_cli_backtest.py` in pure C++, with **exact** double equality. When this passes, the whole core pipeline is proven end-to-end.

**Files:**
- Create: `cpp/src/pkmn_engine/strategy.hpp`, `cpp/src/pkmn_engine/execution.hpp`, `cpp/src/pkmn_engine/execution.cpp`, `cpp/src/pkmn_engine/backtest.hpp`, `cpp/src/pkmn_engine/backtest.cpp`, `cpp/src/pkmn_engine/strategies/buy_and_hold.hpp`, `cpp/src/pkmn_engine/strategies/buy_and_hold.cpp`
- Modify: `cpp/CMakeLists.txt` (add the three .cpp files and `tests/test_backtest_golden.cpp`)
- Test: `cpp/tests/test_backtest_golden.cpp`

**Interfaces:**
- Consumes: everything from Tasks 2–4.
- Produces:
  - `pkmn::Context{Day today; const MarketView& market; const ProductTable& products; const InsertionMap<Position>& positions; double cash; const InsertionMap<double>& marks;}`
  - `pkmn::Strategy` base: `virtual std::vector<Order> on_bar(const Context&) = 0; virtual void reset() {}` (virtual dtor)
  - `std::vector<Fill> pkmn::execute(const std::vector<Order>&, const MarketView&, Portfolio&, Day, const CostModel&)` — MarketView must be `load_day`-ed for that day
  - `pkmn::BacktestResult{std::vector<Day> days; std::vector<double> equity; std::vector<Fill> fills;}`
  - `pkmn::BacktestResult run_backtest(MarketView&, const ProductTable&, Strategy&, const CostModel&, double initial_cash)`
  - `pkmn::BuyAndHold(std::int8_t kind_code)` — Task 8's other strategies follow its file pattern

- [ ] **Step 1: Write the failing golden tests**

`cpp/tests/test_backtest_golden.cpp`:

```cpp
#include <catch2/catch_test_macros.hpp>

#include <vector>

#include "pkmn_engine/backtest.hpp"
#include "pkmn_engine/strategies/buy_and_hold.hpp"

using namespace pkmn;

namespace {
// Mirrors tests/test_cli_backtest.py seed(): one sealed product, three days,
// prices 10/12/15. price_row hardcodes mid=2.0/low=1.0 (crossed vs market
// -> zero impact even when enabled).
MarketView flat_view() {
    std::vector<Day> days{100, 101, 102};
    std::vector<PriceRow> rows{
        {100, 0, 10.0, 2.0, 1.0}, {101, 0, 12.0, 2.0, 1.0}, {102, 0, 15.0, 2.0, 1.0}};
    std::vector<MarkEvent> events{{100, 0, 10.0}, {101, 0, 12.0}, {102, 0, 15.0}};
    return MarketView(1, days, rows, events);
}

// Mirrors seed_impact(): mids 13/16/18 (uncrossed).
MarketView impact_view() {
    std::vector<Day> days{100, 101, 102};
    std::vector<PriceRow> rows{
        {100, 0, 10.0, 13.0, 1.0}, {101, 0, 12.0, 16.0, 1.0}, {102, 0, 15.0, 18.0, 1.0}};
    std::vector<MarkEvent> events{{100, 0, 10.0}, {101, 0, 12.0}, {102, 0, 15.0}};
    return MarketView(1, days, rows, events);
}

ProductTable one_sealed() { return ProductTable{{1}, {0}, {100}}; }
}  // namespace

TEST_CASE("golden flat-cost: matches test_backtest_golden_numbers exactly") {
    auto mkt = flat_view();
    auto prods = one_sealed();
    CostModel cm;  // impact off = --no-impact
    BuyAndHold strat(0);
    auto res = run_backtest(mkt, prods, strat, cm, 100.0);
    // D1: order 10 units; equity 100. D2: fill clipped to 8 (cap 8, cash 8);
    // cash 3, equity 99. D3: equity 3 + 8*15 = 123. EXACT doubles.
    REQUIRE(res.equity == std::vector<double>{100.0, 99.0, 123.0});
    REQUIRE(res.fills.size() == 1);
    CHECK(res.fills[0].day == 101);
    CHECK(res.fills[0].quantity == 8);
    CHECK(res.fills[0].price == 12.0);
    CHECK(res.fills[0].fees == 1.0);
    CHECK(res.fills[0].impact == 0.0);
}

TEST_CASE("golden impact-on: matches test_backtest_golden_numbers_with_impact") {
    auto mkt = impact_view();
    auto prods = one_sealed();
    CostModel cm;
    cm.impact_enabled = true;
    BuyAndHold strat(0);
    auto res = run_backtest(mkt, prods, strat, cm, 100.0);
    // D2: spread 4, cap 8. qty 8 -> impact 16, cost 113 > 100 -> shrink to
    // qty 7 -> impact 12.25, cost 97.25. cash 2.75, equity 86.75.
    REQUIRE(res.equity == std::vector<double>{100.0, 86.75, 107.75});
    REQUIRE(res.fills.size() == 1);
    CHECK(res.fills[0].quantity == 7);
    CHECK(res.fills[0].impact == 12.25);
}

TEST_CASE("run_backtest is repeatable on one MarketView (reset-safety)") {
    auto mkt = flat_view();
    auto prods = one_sealed();
    CostModel cm;
    BuyAndHold strat(0);
    auto r1 = run_backtest(mkt, prods, strat, cm, 100.0);
    auto r2 = run_backtest(mkt, prods, strat, cm, 100.0);
    REQUIRE(r1.equity == r2.equity);
    REQUIRE(r1.fills.size() == r2.fills.size());
}

TEST_CASE("orders for assets that do not print expire unfilled") {
    // asset prints D1 only; strategy orders on D1; no D2 print -> no fill ever
    std::vector<Day> days{100, 101};
    std::vector<PriceRow> rows{{100, 0, 10.0, 2.0, 1.0}};
    std::vector<MarkEvent> events{{100, 0, 10.0}};
    MarketView mkt(1, days, rows, events);
    auto prods = one_sealed();
    CostModel cm;
    BuyAndHold strat(0);
    auto res = run_backtest(mkt, prods, strat, cm, 100.0);
    REQUIRE(res.fills.empty());
    REQUIRE(res.equity == std::vector<double>{100.0, 100.0});
}
```

- [ ] **Step 2: Add to CMake, verify build failure**

Run: `cmake --build cpp/build -j` → FAIL.

- [ ] **Step 3: Write strategy.hpp**

`cpp/src/pkmn_engine/strategy.hpp`:

```cpp
#pragma once

// Port of engine/strategy.py: read-only Context in, Orders out (T+1 fills).
// C++ strategies receive const refs (Python copies for safety; const
// enforces the same contract at compile time).

#include <vector>

#include "pkmn_engine/market.hpp"
#include "pkmn_engine/types.hpp"

namespace pkmn {

struct Context {
    Day today;
    const MarketView& market;  // history via bounded queries (<= today)
    const ProductTable& products;
    const InsertionMap<Position>& positions;
    double cash;
    const InsertionMap<double>& marks;
};

class Strategy {
  public:
    virtual ~Strategy() = default;
    virtual std::vector<Order> on_bar(const Context& ctx) = 0;
    virtual void reset() {}
};

}  // namespace pkmn
```

- [ ] **Step 4: Write execution.hpp / execution.cpp**

`cpp/src/pkmn_engine/execution.hpp`:

```cpp
#pragma once

// Port of engine/execution.py ExecutionSimulator.execute.

#include <vector>

#include "pkmn_engine/costs.hpp"
#include "pkmn_engine/market.hpp"
#include "pkmn_engine/portfolio.hpp"
#include "pkmn_engine/types.hpp"

namespace pkmn {

// Fill `orders` against the day's prints (market must be load_day(day)-ed),
// applying fills to the portfolio. Per-asset daily liquidity cap shared
// across orders and sides (execution.py:44-72).
std::vector<Fill> execute(const std::vector<Order>& orders, const MarketView& market,
                          Portfolio& portfolio, Day day, const CostModel& cm);

}  // namespace pkmn
```

`cpp/src/pkmn_engine/execution.cpp`:

```cpp
#include "pkmn_engine/execution.hpp"

#include <algorithm>
#include <cmath>
#include <optional>

namespace pkmn {

namespace {

// execution.py:74-104
std::optional<Fill> fill_buy(const Order& order, double market, const Portfolio& pf, Day day,
                             std::int64_t cap_left, std::int64_t used, const MarketView& mkt,
                             const CostModel& cm) {
    std::int64_t qty = std::min(order.quantity, cap_left);
    // afford: qty * market + shipping_per_line + impact(qty) <= cash
    auto affordable =
        static_cast<std::int64_t>(std::floor((pf.cash - cm.shipping_per_line) / market));
    qty = std::min(qty, std::max<std::int64_t>(affordable, 0));
    double mid = mkt.mid(order.asset);  // NaN when the asset has no quote today
    double impact = cm.buy_impact(market, mid, qty, used);
    double cost = static_cast<double>(qty) * market + cm.shipping_per_line + impact;
    while (qty > 0 && cost > pf.cash) {
        --qty;
        impact = cm.buy_impact(market, mid, qty, used);
        cost = static_cast<double>(qty) * market + cm.shipping_per_line + impact;
    }
    if (qty <= 0) return std::nullopt;
    return Fill{day, order.asset, qty, market, cm.shipping_per_line, impact};
}

// execution.py:106-132
std::optional<Fill> fill_sell(const Order& order, double market, const Portfolio& pf, Day day,
                              std::int64_t cap_left, std::int64_t used, const MarketView& mkt,
                              const CostModel& cm) {
    const Position* pos = pf.positions.find(order.asset);
    if (pos == nullptr) return std::nullopt;
    std::int64_t qty = std::min({-order.quantity, pos->quantity, cap_left});
    if (qty <= 0) return std::nullopt;
    // Python: qty * market * fee_rate + shipping — left-to-right.
    double fees =
        static_cast<double>(qty) * market * cm.fee_rate + cm.shipping_per_line;
    double low = mkt.low(order.asset);
    double impact = cm.sell_impact(market, low, qty, used);
    return Fill{day, order.asset, -qty, market, fees, impact};
}

}  // namespace

std::vector<Fill> execute(const std::vector<Order>& orders, const MarketView& market,
                          Portfolio& portfolio, Day day, const CostModel& cm) {
    std::vector<Fill> fills;
    InsertionMap<std::int64_t> filled_today(market.n_assets());
    for (const auto& order : orders) {
        double px = market.price(order.asset);
        // execution.py:53-57 — NaN = didn't print (Python None); <= 0
        // defensive skip.
        if (std::isnan(px) || px <= 0.0 || order.quantity == 0) continue;
        std::int64_t used = 0;
        if (const auto* u = filled_today.find(order.asset)) used = *u;
        std::int64_t cap_left = cm.max_daily_qty(px) - used;
        if (cap_left <= 0) continue;
        auto fill = order.quantity > 0
                        ? fill_buy(order, px, portfolio, day, cap_left, used, market, cm)
                        : fill_sell(order, px, portfolio, day, cap_left, used, market, cm);
        if (fill.has_value()) {
            portfolio.apply(*fill);
            fills.push_back(*fill);
            std::int64_t filled = fill->quantity < 0 ? -fill->quantity : fill->quantity;
            filled_today.set(order.asset, used + filled);
        }
    }
    return fills;
}

}  // namespace pkmn
```

- [ ] **Step 5: Write backtest.hpp / backtest.cpp**

`cpp/src/pkmn_engine/backtest.hpp`:

```cpp
#pragma once

// Port of engine/backtest.py: history -> strategy -> orders -> T+1 fills ->
// equity. Metrics stay in Python (summarize() is single-sourced there).

#include <vector>

#include "pkmn_engine/costs.hpp"
#include "pkmn_engine/market.hpp"
#include "pkmn_engine/strategy.hpp"
#include "pkmn_engine/types.hpp"

namespace pkmn {

struct BacktestResult {
    std::vector<Day> days;
    std::vector<double> equity;
    std::vector<Fill> fills;
};

BacktestResult run_backtest(MarketView& market, const ProductTable& products,
                            Strategy& strategy, const CostModel& cost_model,
                            double initial_cash);

}  // namespace pkmn
```

`cpp/src/pkmn_engine/backtest.cpp`:

```cpp
#include "pkmn_engine/backtest.hpp"

#include "pkmn_engine/execution.hpp"
#include "pkmn_engine/portfolio.hpp"

namespace pkmn {

BacktestResult run_backtest(MarketView& market, const ProductTable& products,
                            Strategy& strategy, const CostModel& cost_model,
                            double initial_cash) {
    // backtest.py:50-102, same step order per day.
    strategy.reset();
    market.reset();
    Portfolio portfolio(initial_cash, market.n_assets());
    BacktestResult out;
    std::vector<Order> pending;
    for (Day day : market.days()) {
        // 1. Yesterday's orders fill at today's actually-printed prices.
        market.load_day(day);
        auto fills = execute(pending, market, portfolio, day, cost_model);
        out.fills.insert(out.fills.end(), fills.begin(), fills.end());
        pending.clear();
        // 2. Strategy sees history <= today, emits orders for tomorrow.
        const auto& marks = market.marks_until(day);
        Context ctx{day, market, products, portfolio.positions, portfolio.cash, marks};
        pending = strategy.on_bar(ctx);
        // 3. Mark-to-market equity.
        out.days.push_back(day);
        out.equity.push_back(portfolio.equity(marks));
    }
    return out;
}

}  // namespace pkmn
```

- [ ] **Step 6: Write buy_and_hold.hpp / buy_and_hold.cpp**

`cpp/src/pkmn_engine/strategies/buy_and_hold.hpp`:

```cpp
#pragma once

// Port of strategies/buy_and_hold.py.

#include <cstdint>
#include <vector>

#include "pkmn_engine/strategy.hpp"

namespace pkmn {

class BuyAndHold final : public Strategy {
  public:
    explicit BuyAndHold(std::int8_t kind_code) : kind_(kind_code) {}
    void reset() override { entered_ = false; }
    std::vector<Order> on_bar(const Context& ctx) override;

  private:
    std::int8_t kind_;
    bool entered_ = false;
};

}  // namespace pkmn
```

`cpp/src/pkmn_engine/strategies/buy_and_hold.cpp`:

```cpp
#include "pkmn_engine/strategies/buy_and_hold.hpp"

#include <algorithm>
#include <cmath>
#include <utility>

namespace pkmn {

std::vector<Order> BuyAndHold::on_bar(const Context& ctx) {
    // buy_and_hold.py:24-44. Python sorts all marks by product_id (stable,
    // ties in dict insertion order) then filters by kind; filter-then-
    // stable-sort commutes because both preserve relative order.
    if (entered_) return {};
    entered_ = true;

    std::vector<std::pair<AssetId, double>> universe;
    for (const auto& e : ctx.marks.entries()) {
        if (ctx.products.kind[static_cast<std::size_t>(e.key)] == kind_)
            universe.emplace_back(e.key, e.value);
    }
    std::stable_sort(universe.begin(), universe.end(), [&](const auto& a, const auto& b) {
        return ctx.products.product_id[static_cast<std::size_t>(a.first)] <
               ctx.products.product_id[static_cast<std::size_t>(b.first)];
    });
    if (universe.empty()) return {};

    double budget_per_asset = ctx.cash / static_cast<double>(universe.size());
    std::vector<Order> orders;
    for (const auto& [asset, price] : universe) {
        auto qty = static_cast<std::int64_t>(std::floor(budget_per_asset / price));
        if (qty > 0) orders.push_back(Order{asset, qty});
    }
    return orders;
}

}  // namespace pkmn
```

- [ ] **Step 7: Build, run all Catch2 tests**

Run: `cmake --build cpp/build -j && ctest --test-dir cpp/build --output-on-failure`
Expected: PASS including both goldens with EXACT equality. If a golden fails on the last bit, first suspect FMA contraction (`-ffp-contract=off` present on the target?) before touching the arithmetic.

- [ ] **Step 8: Commit**

```bash
git add cpp/
git commit -m "feat(cpp): execution + event loop + BuyAndHold pass the CLI goldens exactly"
```

---

### Task 6: nanobind bindings + NativeBacktest adapter + first differential test

The boundary crossing. After this task, `NativeBacktest` runs buy-and-hold on a synthetic warehouse and matches the Python `Backtest` bit-for-bit from Python's point of view.

**Files:**
- Create: `cpp/src/pkmn_engine/strategies/factory.hpp`, `cpp/src/pkmn_engine/strategies/factory.cpp`, `src/pkmn_quant/engine/native.py`, `tests/test_native_parity.py`
- Modify: `cpp/bindings/module.cpp` (full binding), `cpp/CMakeLists.txt` (add `factory.cpp`), `src/pkmn_quant/engine/data.py` (add `mark_events()`), `src/pkmn_quant/_engine.pyi`
- Test: `tests/test_native_parity.py`

**Interfaces:**
- Consumes: `run_backtest`, `make_strategy`, `MarketView`, `ProductTable`, `CostModel` from earlier tasks; Python `MarketData`, `Backtest`, `Result`, `CostModel`, `summarize`.
- Produces:
  - C++: `std::unique_ptr<Strategy> pkmn::make_strategy(const std::string& name, const std::map<std::string, double>& params, std::int8_t universe_kind)` — knows `"buy-and-hold"` now; Tasks 7–8 extend it; throws `std::invalid_argument` for unknown names (surfaces as Python `ValueError`).
  - Python: `pkmn_quant._engine.run_backtest(...)` (signature below), `NativeStrategySpec(name, params, kind="sealed")`, `NativeBacktest(warehouse, strategy, cost_model, start, end, initial_cash, warmup_days=0).run() -> Result`, `NATIVE_STRATEGY_NAMES: frozenset[str]` (grows in Tasks 7–8), `MarketData.mark_events() -> list[tuple[date, Asset, float]]`.
  - Test helpers other tasks reuse: `tests/test_native_parity.py::seed_rich(root)` (rich synthetic warehouse) and `assert_results_equal(py, cpp)`.

- [ ] **Step 1: Add the public mark_events accessor to MarketData**

In `src/pkmn_quant/engine/data.py`, add to `MarketData` (after `history_until`):

```python
    def mark_events(self) -> list[tuple[date, Asset, float]]:
        """Change-point rows feeding marks_on, in exact replay order.

        Public for the native-engine adapter: replaying these through the
        C++ marks cursor reproduces marks_on including dict insertion order
        (which buy-and-hold's stable sort observes), without re-deriving
        polars' row order in a second language.
        """
        return list(self._marks_rows)
```

- [ ] **Step 2: Write the failing differential test (and the shared fixture)**

`tests/test_native_parity.py`:

```python
"""Differential tests: NativeBacktest (C++) vs Backtest (Python reference).

Every assertion is EXACT (==) — bit-for-bit parity is the acceptance bar
(spec 2026-07-14). A tolerance here would hide real divergence.
"""

from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA
from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.backtest import Backtest, Result
from pkmn_quant.engine.costs import CostModel
from pkmn_quant.engine.native import NativeBacktest, NativeStrategySpec
from pkmn_quant.strategies.buy_and_hold import BuyAndHold

START = date(2025, 1, 1)

# (product_id, sub_type) -> base price. product 4 has two sub_types (an
# insertion-order tie on product_id); product 6 sits below min_price.
BASES: dict[tuple[int, str], float] = {
    (1, "Normal"): 80.0,
    (2, "Normal"): 40.0,
    (3, "Normal"): 25.0,
    (4, "Normal"): 12.0,
    (4, "Foil"): 18.0,
    (5, "Normal"): 6.0,
    (6, "Normal"): 1.5,
}

PRODUCTS = pl.DataFrame(
    {
        "product_id": [1, 2, 3, 4, 5, 6],
        "group_id": [1, 1, 1, 1, 1, 1],
        "name": ["Box A", "Box B", "Card C", "Card D", "Card E", "Penny F"],
        "rarity": [None, None, "Rare", "Rare", "Holo", "Common"],
        "kind": ["sealed", "sealed", "single", "single", "single", "single"],
        "released_on": [
            date(2024, 11, 1),
            date(2024, 6, 1),
            date(2024, 11, 1),
            date(2024, 11, 1),
            date(2024, 11, 1),
            date(2024, 11, 1),
        ],
    }
)


def _path(i: int) -> float:
    """Deterministic ramp-crash-recover cycle: guarantees dips, drawdowns,
    momentum reversals, and take-profit recoveries within 25 days."""
    i = i % 25
    if i < 10:
        return 1.0 + 0.05 * i  # ramp to 1.45
    if i < 15:
        return 0.8 - 0.05 * (i - 10)  # crash to 0.60
    return 0.62 + 0.03 * (i - 15)  # recovery


def seed_rich(root: Path, n_days: int = 40) -> None:
    w = Warehouse(Paths(root=root))
    for i in range(n_days):
        day = START + timedelta(days=i)
        if i % 9 == 4:  # market-wide gap day
            continue
        rows = []
        for (pid, st), base in BASES.items():
            if (pid * 3 + i) % 11 == 0:  # per-asset missing prints
                continue
            market = round(base * _path(i + pid), 2)
            rows.append(
                {
                    "date": day,
                    "product_id": pid,
                    "sub_type": st,
                    "low": round(market * 0.9, 2),
                    "mid": round(market * 1.15, 2),
                    "high": round(market * 3.0, 2),
                    "market": market,
                }
            )
        w.write_prices(day, pl.DataFrame(rows, schema=PRICE_SCHEMA))
    w.write_products(PRODUCTS)


def assert_results_equal(py: Result, cpp: Result) -> None:
    assert py.equity_curve["date"].to_list() == cpp.equity_curve["date"].to_list()
    assert py.equity_curve["equity"].to_list() == cpp.equity_curve["equity"].to_list()
    assert len(py.fills) == len(cpp.fills)
    for a, b in zip(py.fills, cpp.fills, strict=True):
        assert (a.day, a.asset, a.quantity) == (b.day, b.asset, b.quantity)
        assert a.price == b.price
        assert a.fees == b.fees
        assert a.impact == b.impact
    assert py.summary == cpp.summary
    assert py.strategy_name == cpp.strategy_name


@pytest.mark.parametrize("impact", [False, True])
def test_buy_and_hold_parity(tmp_path: Path, impact: bool) -> None:
    seed_rich(tmp_path)
    wh = Warehouse(Paths(root=tmp_path))
    cm = CostModel(impact_enabled=impact)
    end = START + timedelta(days=39)
    py = Backtest(
        warehouse=wh, strategy=BuyAndHold(kind="sealed"), cost_model=cm,
        start=START, end=end, initial_cash=1000.0,
    ).run()
    cpp = NativeBacktest(
        warehouse=wh, strategy=NativeStrategySpec("buy-and-hold", {}, kind="sealed"),
        cost_model=cm, start=START, end=end, initial_cash=1000.0,
    ).run()
    assert len(py.fills) > 0  # the test must not pass vacuously
    assert_results_equal(py, cpp)


def test_buy_and_hold_parity_single_universe(tmp_path: Path) -> None:
    """Exercises the marks insertion-order tie: product 4 Normal vs Foil."""
    seed_rich(tmp_path)
    wh = Warehouse(Paths(root=tmp_path))
    cm = CostModel()
    end = START + timedelta(days=39)
    py = Backtest(
        warehouse=wh, strategy=BuyAndHold(kind="single"), cost_model=cm,
        start=START, end=end, initial_cash=1000.0,
    ).run()
    cpp = NativeBacktest(
        warehouse=wh, strategy=NativeStrategySpec("buy-and-hold", {}, kind="single"),
        cost_model=cm, start=START, end=end, initial_cash=1000.0,
    ).run()
    assert len(py.fills) > 0
    assert_results_equal(py, cpp)


def test_warmup_days_parity(tmp_path: Path) -> None:
    seed_rich(tmp_path)
    wh = Warehouse(Paths(root=tmp_path))
    cm = CostModel(impact_enabled=True)
    start = START + timedelta(days=15)
    end = START + timedelta(days=39)
    py = Backtest(
        warehouse=wh, strategy=BuyAndHold(kind="sealed"), cost_model=cm,
        start=start, end=end, initial_cash=1000.0, warmup_days=10,
    ).run()
    cpp = NativeBacktest(
        warehouse=wh, strategy=NativeStrategySpec("buy-and-hold", {}, kind="sealed"),
        cost_model=cm, start=start, end=end, initial_cash=1000.0, warmup_days=10,
    ).run()
    assert_results_equal(py, cpp)


def test_unknown_strategy_raises_value_error(tmp_path: Path) -> None:
    seed_rich(tmp_path, n_days=3)
    wh = Warehouse(Paths(root=tmp_path))
    with pytest.raises(ValueError):
        NativeBacktest(
            warehouse=wh, strategy=NativeStrategySpec("nope", {}),
            cost_model=CostModel(), start=START, end=START + timedelta(days=2),
            initial_cash=100.0,
        ).run()
```

Run: `uv run pytest tests/test_native_parity.py -v` → FAIL (`No module named 'pkmn_quant.engine.native'`).

- [ ] **Step 3: Write the C++ strategy factory**

`cpp/src/pkmn_engine/strategies/factory.hpp`:

```cpp
#pragma once

// Strategy construction keyed by registry name + optuna params map.
// Missing params fall back to the Python constructor defaults.

#include <cstdint>
#include <map>
#include <memory>
#include <string>

#include "pkmn_engine/strategy.hpp"

namespace pkmn {

using ParamMap = std::map<std::string, double>;

// Throws std::invalid_argument for unknown names (-> Python ValueError).
// universe_kind (0 sealed, 1 single) is used only by "buy-and-hold".
std::unique_ptr<Strategy> make_strategy(const std::string& name, const ParamMap& params,
                                        std::int8_t universe_kind);

// Helpers shared by strategy constructors.
double param(const ParamMap& p, const std::string& key, double dflt);
std::int64_t iparam(const ParamMap& p, const std::string& key, std::int64_t dflt);

}  // namespace pkmn
```

`cpp/src/pkmn_engine/strategies/factory.cpp` (Task 6 version; Tasks 7–8 extend the if-chain):

```cpp
#include "pkmn_engine/strategies/factory.hpp"

#include <stdexcept>

#include "pkmn_engine/strategies/buy_and_hold.hpp"

namespace pkmn {

double param(const ParamMap& p, const std::string& key, double dflt) {
    auto it = p.find(key);
    return it == p.end() ? dflt : it->second;
}

std::int64_t iparam(const ParamMap& p, const std::string& key, std::int64_t dflt) {
    auto it = p.find(key);
    // optuna int params arrive as exact doubles; llround is the safe cast.
    return it == p.end() ? dflt : static_cast<std::int64_t>(std::llround(it->second));
}

std::unique_ptr<Strategy> make_strategy(const std::string& name, const ParamMap& params,
                                        std::int8_t universe_kind) {
    (void)params;
    if (name == "buy-and-hold") return std::make_unique<BuyAndHold>(universe_kind);
    throw std::invalid_argument("unknown native strategy: " + name);
}

}  // namespace pkmn
```

(Add `#include <cmath>` for `llround`.)

- [ ] **Step 4: Write the full binding**

Replace `cpp/bindings/module.cpp`:

```cpp
#include <nanobind/nanobind.h>
#include <nanobind/ndarray.h>
#include <nanobind/stl/string.h>

#include <cstdint>
#include <memory>
#include <utility>
#include <vector>

#include "pkmn_engine/backtest.hpp"
#include "pkmn_engine/strategies/callback.hpp"
#include "pkmn_engine/strategies/factory.hpp"
#include "pkmn_engine/version.hpp"

namespace nb = nanobind;
using namespace pkmn;

namespace {

template <typename T>
using Arr = nb::ndarray<const T, nb::ndim<1>, nb::c_contig, nb::device::cpu>;

// One flat entry point: arrays in, plain Python lists out. Crossed once per
// run — clarity over marshaling micro-optimizations.
nb::object run_backtest_py(
    Arr<std::int32_t> trading_days,
    Arr<std::int32_t> row_day, Arr<std::int32_t> row_asset, Arr<double> row_market,
    Arr<double> row_mid, Arr<double> row_low,
    Arr<std::int32_t> ev_day, Arr<std::int32_t> ev_asset, Arr<double> ev_price,
    Arr<std::int64_t> prod_id, Arr<std::int8_t> prod_kind, Arr<std::int32_t> prod_released,
    const std::string& strategy_name, nb::dict params, std::int8_t universe_kind,
    double fee_rate, double shipping_per_line,
    Arr<double> tier_thresholds, Arr<std::int64_t> tier_qtys,
    std::int64_t fallback_max_qty, bool impact_enabled,
    double initial_cash, nb::object callback) {
    std::size_t n_assets = prod_id.size();

    std::vector<Day> days(trading_days.data(), trading_days.data() + trading_days.size());
    std::vector<PriceRow> rows(row_day.size());
    for (std::size_t i = 0; i < rows.size(); ++i) {
        rows[i] = PriceRow{row_day(i), row_asset(i), row_market(i), row_mid(i), row_low(i)};
    }
    std::vector<MarkEvent> events(ev_day.size());
    for (std::size_t i = 0; i < events.size(); ++i) {
        events[i] = MarkEvent{ev_day(i), ev_asset(i), ev_price(i)};
    }
    MarketView market(n_assets, std::move(days), std::move(rows), std::move(events));

    ProductTable products;
    products.product_id.assign(prod_id.data(), prod_id.data() + n_assets);
    products.kind.assign(prod_kind.data(), prod_kind.data() + n_assets);
    products.released_on.assign(prod_released.data(), prod_released.data() + n_assets);

    CostModel cm;
    cm.fee_rate = fee_rate;
    cm.shipping_per_line = shipping_per_line;
    cm.liquidity_tiers.clear();
    for (std::size_t i = 0; i < tier_thresholds.size(); ++i) {
        cm.liquidity_tiers.emplace_back(tier_thresholds(i), tier_qtys(i));
    }
    cm.fallback_max_qty = fallback_max_qty;
    cm.impact_enabled = impact_enabled;

    std::unique_ptr<Strategy> strategy;
    if (!callback.is_none()) {
        nb::callable cb = nb::cast<nb::callable>(callback);
        strategy = std::make_unique<CallbackStrategy>([cb](const Context& ctx) {
            nb::list pos;
            for (const auto& e : ctx.positions.entries()) {
                pos.append(nb::make_tuple(e.key, e.value.quantity, e.value.avg_cost,
                                          e.value.opened_on));
            }
            nb::object ret = cb(ctx.today, pos, ctx.cash);
            std::vector<Order> orders;
            for (nb::handle h : nb::cast<nb::list>(ret)) {
                auto t = nb::cast<nb::tuple>(h);
                orders.push_back(
                    Order{nb::cast<AssetId>(t[0]), nb::cast<std::int64_t>(t[1])});
            }
            return orders;
        });
    } else {
        ParamMap pmap;
        for (auto item : params) {
            pmap[nb::cast<std::string>(item.first)] = nb::cast<double>(item.second);
        }
        strategy = make_strategy(strategy_name, pmap, universe_kind);
    }

    BacktestResult res = run_backtest(market, products, *strategy, cm, initial_cash);

    nb::list out_days, out_equity, out_fills;
    for (Day d : res.days) out_days.append(d);
    for (double e : res.equity) out_equity.append(e);
    for (const Fill& f : res.fills) {
        out_fills.append(
            nb::make_tuple(f.day, f.asset, f.quantity, f.price, f.fees, f.impact));
    }
    return nb::make_tuple(out_days, out_equity, out_fills);
}

}  // namespace

NB_MODULE(_engine, m) {
    m.doc() = "pkmn_quant native backtest engine";
    m.attr("__version__") = pkmn::engine_version();
    m.def("run_backtest", &run_backtest_py, nb::arg("trading_days"), nb::arg("row_day"),
          nb::arg("row_asset"), nb::arg("row_market"), nb::arg("row_mid"), nb::arg("row_low"),
          nb::arg("ev_day"), nb::arg("ev_asset"), nb::arg("ev_price"), nb::arg("prod_id"),
          nb::arg("prod_kind"), nb::arg("prod_released"), nb::arg("strategy_name"),
          nb::arg("params"), nb::arg("universe_kind"), nb::arg("fee_rate"),
          nb::arg("shipping_per_line"), nb::arg("tier_thresholds"), nb::arg("tier_qtys"),
          nb::arg("fallback_max_qty"), nb::arg("impact_enabled"), nb::arg("initial_cash"),
          nb::arg("callback").none());
}
```

Also create `cpp/src/pkmn_engine/strategies/callback.hpp` now (the binding needs it; the Python-side bridge test is Task 9):

```cpp
#pragma once

// A Strategy that delegates on_bar to a std::function. The binding wraps a
// Python callable in one; the core stays Python-free.

#include <functional>
#include <utility>
#include <vector>

#include "pkmn_engine/strategy.hpp"

namespace pkmn {

class CallbackStrategy final : public Strategy {
  public:
    using Fn = std::function<std::vector<Order>(const Context&)>;
    explicit CallbackStrategy(Fn fn) : fn_(std::move(fn)) {}
    std::vector<Order> on_bar(const Context& ctx) override { return fn_(ctx); }

  private:
    Fn fn_;
};

}  // namespace pkmn
```

Add `src/pkmn_engine/strategies/factory.cpp` and `src/pkmn_engine/strategies/buy_and_hold.cpp` (if not already) to `pkmn_engine_core` in CMakeLists.

- [ ] **Step 5: Write the Python adapter**

`src/pkmn_quant/engine/native.py`:

```python
"""NativeBacktest: the C++ engine behind the same Result type.

Crosses the Python/C++ boundary once per run: MarketData loads and shapes
the data exactly as the Python engine sees it (same frame, same mark
change-point order), flattened to numpy arrays. Fills and equity come back
and are repackaged into engine.backtest.Result, so downstream consumers
(runs registry, reports, walk-forward stitching) cannot tell engines apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import numpy as np
import polars as pl

from pkmn_quant import _engine
from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.backtest import Result
from pkmn_quant.engine.costs import CostModel
from pkmn_quant.engine.data import MarketData
from pkmn_quant.engine.metrics import summarize
from pkmn_quant.engine.portfolio import Asset, Fill, Position
from pkmn_quant.engine.strategy import Context, Strategy

_EPOCH = date(1970, 1, 1)
_NULL_DAY = -(2**31)
_KIND_CODES = {"sealed": 0, "single": 1}

# Rule strategies with a native C++ port (factory.cpp). Anything else runs
# on the C++ engine via the callback bridge. Tasks 7-8 extend this set.
NATIVE_STRATEGY_NAMES: frozenset[str] = frozenset({"buy-and-hold"})


def _to_day(d: date) -> int:
    return (d - _EPOCH).days


def _from_day(i: int) -> date:
    return _EPOCH + timedelta(days=int(i))


@dataclass(frozen=True)
class NativeStrategySpec:
    """A strategy the C++ factory can build: registry name + params."""

    name: str
    params: dict[str, float]
    kind: str = "sealed"  # only read by buy-and-hold


@dataclass
class NativeBacktest:
    """Drop-in for engine.backtest.Backtest, running the C++ engine.

    strategy: a NativeStrategySpec (native C++ strategy) or a Python
    Strategy instance (runs unmodified via the per-bar callback bridge —
    correct but without the native speedup).
    """

    warehouse: Warehouse
    strategy: NativeStrategySpec | Strategy
    cost_model: CostModel
    start: date
    end: date
    initial_cash: float
    warmup_days: int = 0
    _asset_list: list[Asset] = field(default_factory=list, init=False, repr=False)

    def run(self) -> Result:
        market = MarketData.from_warehouse(
            self.warehouse, self.start, self.end, warmup_days=self.warmup_days
        )
        products = self.warehouse.load_products()

        frame = market.frame.sort("date")
        assets_df = (
            frame.select("product_id", "sub_type")
            .unique()
            .sort(["product_id", "sub_type"])
            .with_row_index("asset_id")
        )
        asset_list = [
            Asset(product_id=int(pid), sub_type=str(st))
            for pid, st in assets_df.select("product_id", "sub_type").iter_rows()
        ]
        self._asset_list = asset_list
        asset_index = {a: i for i, a in enumerate(asset_list)}

        joined = frame.join(assets_df, on=["product_id", "sub_type"], how="left").sort("date")
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

        prod_info = {
            int(r["product_id"]): (str(r["kind"]), r["released_on"])
            for r in products.iter_rows(named=True)
        }
        prod_id = np.array([a.product_id for a in asset_list], dtype=np.int64)
        prod_kind = np.array(
            [_KIND_CODES.get(prod_info[a.product_id][0], -1) for a in asset_list],
            dtype=np.int8,
        )
        prod_released = np.array(
            [
                _to_day(rel) if (rel := prod_info[a.product_id][1]) is not None else _NULL_DAY
                for a in asset_list
            ],
            dtype=np.int32,
        )

        trading_days = np.array([_to_day(d) for d in market.days], dtype=np.int32)
        tiers = self.cost_model.liquidity_tiers
        tier_thresholds = np.array([t for t, _ in tiers], dtype=np.float64)
        tier_qtys = np.array([q for _, q in tiers], dtype=np.int64)

        if isinstance(self.strategy, NativeStrategySpec):
            name = self.strategy.name
            params = {k: float(v) for k, v in self.strategy.params.items()}
            universe_kind = _KIND_CODES.get(self.strategy.kind, -1)
            callback = None
            strategy_name = (
                f"buy-and-hold-{self.strategy.kind}" if name == "buy-and-hold" else name
            )
        else:
            strategy = self.strategy
            strategy.reset()  # Backtest.run() parity: fresh per-run state
            name, params, universe_kind = "", {}, -1
            strategy_name = strategy.name

            def callback(
                day_i: int, raw: list[tuple[int, int, float, int]], cash: float
            ) -> list[tuple[int, int]]:
                today = _from_day(day_i)
                positions = {
                    asset_list[aid]: Position(
                        quantity=qty, avg_cost=avg, opened_on=_from_day(op)
                    )
                    for aid, qty, avg, op in raw
                }
                ctx = Context(
                    today=today,
                    history=market.history_until(today),
                    products=products,
                    positions=positions,
                    cash=cash,
                    marks=market.marks_on(today),
                )
                return [(asset_index[o.asset], o.quantity) for o in strategy.on_bar(ctx)]

        days_out, equity_out, fills_out = _engine.run_backtest(
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
            strategy_name=name,
            params=params,
            universe_kind=universe_kind,
            fee_rate=self.cost_model.fee_rate,
            shipping_per_line=self.cost_model.shipping_per_line,
            tier_thresholds=tier_thresholds,
            tier_qtys=tier_qtys,
            fallback_max_qty=self.cost_model.fallback_max_qty,
            impact_enabled=self.cost_model.impact_enabled,
            initial_cash=self.initial_cash,
            callback=callback,
        )

        equity_curve = pl.DataFrame(
            {"date": [_from_day(i) for i in days_out], "equity": equity_out},
            schema={"date": pl.Date, "equity": pl.Float64},
        )
        fills = [
            Fill(
                day=_from_day(d),
                asset=asset_list[aid],
                quantity=qty,
                price=price,
                fees=fees,
                impact=impact,
            )
            for d, aid, qty, price, fees, impact in fills_out
        ]
        return Result(
            strategy_name=strategy_name,
            equity_curve=equity_curve,
            fills=fills,
            summary=summarize(equity_curve),
            cost_model=self.cost_model.as_dict(),
        )


def _cost_model_dict(cm: CostModel) -> dict[str, Any]:  # pragma: no cover - debug helper
    return cm.as_dict()
```

(If ruff flags the unused debug helper, delete `_cost_model_dict` — it is optional.)

Update `src/pkmn_quant/_engine.pyi`:

```python
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
```

- [ ] **Step 6: Rebuild, run the differential tests**

```bash
uv sync --reinstall-package pkmn-quant
uv run pytest tests/test_native_parity.py -v
```

Expected: PASS, all exact. Debugging a mismatch: print the first divergent index of the two equity lists — the day tells you which subsystem diverged (fill day → execution/costs; later drift → marks/equity order).

- [ ] **Step 7: Full gates + Catch2, then commit**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy
cmake --build cpp/build -j && ctest --test-dir cpp/build --output-on-failure
git add cpp/ src/pkmn_quant/ tests/test_native_parity.py
git commit -m "feat: nanobind boundary + NativeBacktest adapter, buy-and-hold bit-for-bit parity"
```

---

### Task 7: Native strategies I — SealedAccumulation and DipBuyer

Both follow the same shape: sells first (stable-sorted positions), then a candidate scan over all assets, sort, budget-clipped buys. The comparator convention from Global Constraints applies: Python's `list.sort(key=(k1, k2))` becomes a strict `std::sort` on `(k1, k2, asset_id)`.

**Files:**
- Create: `cpp/src/pkmn_engine/strategies/sealed_accumulation.hpp`/`.cpp`, `cpp/src/pkmn_engine/strategies/dip_buyer.hpp`/`.cpp`
- Modify: `cpp/src/pkmn_engine/strategies/factory.cpp` (register both), `cpp/CMakeLists.txt`, `src/pkmn_quant/engine/native.py` (`NATIVE_STRATEGY_NAMES`), `tests/test_native_parity.py` (differential tests)
- Test: `cpp/tests/test_strategies.cpp` (new), `tests/test_native_parity.py`

**Interfaces:**
- Consumes: `Strategy`, `Context`, `MarketView` queries, `param`/`iparam` from factory.hpp.
- Produces: `pkmn::SealedAccumulation(min_age_days, max_age_days, min_drawdown, take_profit, max_positions, budget_frac)`, `pkmn::DipBuyer(dip_window_days, dip_threshold, hold_days, take_profit, max_positions, budget_frac, min_price)`; factory names `"sealed-accumulation"`, `"dip-buyer"`; `NATIVE_STRATEGY_NAMES` grows to include both.

- [ ] **Step 1: Write the failing differential tests**

Append to `tests/test_native_parity.py`:

```python
@pytest.mark.parametrize("impact", [False, True])
def test_sealed_accumulation_parity(tmp_path: Path, impact: bool) -> None:
    from pkmn_quant.strategies.sealed_accumulation import SealedAccumulation

    seed_rich(tmp_path)
    wh = Warehouse(Paths(root=tmp_path))
    cm = CostModel(impact_enabled=impact)
    end = START + timedelta(days=39)
    params = {
        "min_age_days": 30, "max_age_days": 400, "min_drawdown": 0.15,
        "take_profit": 1.1, "max_positions": 5, "budget_frac": 0.4,
    }
    py = Backtest(
        warehouse=wh,
        strategy=SealedAccumulation(
            min_age_days=30, max_age_days=400, min_drawdown=0.15,
            take_profit=1.1, max_positions=5, budget_frac=0.4,
        ),
        cost_model=cm, start=START, end=end, initial_cash=1000.0,
    ).run()
    cpp = NativeBacktest(
        warehouse=wh,
        strategy=NativeStrategySpec("sealed-accumulation", {k: float(v) for k, v in params.items()}),
        cost_model=cm, start=START, end=end, initial_cash=1000.0,
    ).run()
    assert len(py.fills) > 0
    assert_results_equal(py, cpp)


@pytest.mark.parametrize("impact", [False, True])
def test_dip_buyer_parity(tmp_path: Path, impact: bool) -> None:
    from pkmn_quant.strategies.dip_buyer import DipBuyer

    seed_rich(tmp_path)
    wh = Warehouse(Paths(root=tmp_path))
    cm = CostModel(impact_enabled=impact)
    end = START + timedelta(days=39)
    py = Backtest(
        warehouse=wh,
        strategy=DipBuyer(
            dip_window_days=5, dip_threshold=0.10, hold_days=7,
            take_profit=1.05, max_positions=5, budget_frac=0.4, min_price=3.0,
        ),
        cost_model=cm, start=START, end=end, initial_cash=1000.0,
    ).run()
    cpp = NativeBacktest(
        warehouse=wh,
        strategy=NativeStrategySpec(
            "dip-buyer",
            {
                "dip_window_days": 5.0, "dip_threshold": 0.10, "hold_days": 7.0,
                "take_profit": 1.05, "max_positions": 5.0, "budget_frac": 0.4,
                "min_price": 3.0,
            },
        ),
        cost_model=cm, start=START, end=end, initial_cash=1000.0,
    ).run()
    assert len(py.fills) > 2  # entries AND exits must occur
    assert any(f.quantity < 0 for f in py.fills)
    assert_results_equal(py, cpp)
```

Run: `uv run pytest tests/test_native_parity.py -k "sealed or dip" -v` → FAIL (ValueError: unknown native strategy).

- [ ] **Step 2: Write SealedAccumulation**

`cpp/src/pkmn_engine/strategies/sealed_accumulation.hpp`:

```cpp
#pragma once

// Port of strategies/sealed_accumulation.py.

#include <cstdint>
#include <vector>

#include "pkmn_engine/strategy.hpp"

namespace pkmn {

class SealedAccumulation final : public Strategy {
  public:
    SealedAccumulation(std::int64_t min_age_days, std::int64_t max_age_days,
                       double min_drawdown, double take_profit, std::int64_t max_positions,
                       double budget_frac)
        : min_age_days_(min_age_days),
          max_age_days_(max_age_days),
          min_drawdown_(min_drawdown),
          take_profit_(take_profit),
          max_positions_(max_positions),
          budget_frac_(budget_frac) {}

    std::vector<Order> on_bar(const Context& ctx) override;

  private:
    std::int64_t min_age_days_;
    std::int64_t max_age_days_;
    double min_drawdown_;
    double take_profit_;
    std::int64_t max_positions_;
    double budget_frac_;
};

}  // namespace pkmn
```

`cpp/src/pkmn_engine/strategies/sealed_accumulation.cpp`:

```cpp
#include "pkmn_engine/strategies/sealed_accumulation.hpp"

#include <algorithm>
#include <cmath>

namespace pkmn {

std::vector<Order> SealedAccumulation::on_bar(const Context& ctx) {
    std::vector<Order> orders;

    // Sells first (sealed_accumulation.py:43-46): positions in insertion
    // order, stable-sorted by product_id — Python's sorted() over dict items.
    auto held = ctx.positions.entries();
    std::stable_sort(held.begin(), held.end(), [&](const auto& a, const auto& b) {
        return ctx.products.product_id[static_cast<std::size_t>(a.key)] <
               ctx.products.product_id[static_cast<std::size_t>(b.key)];
    });
    for (const auto& e : held) {
        const double* mark = ctx.marks.find(e.key);
        if (mark != nullptr && *mark >= e.value.avg_cost * take_profit_)
            orders.push_back(Order{e.key, -e.value.quantity});
    }

    // sealed_accumulation.py:48-50
    std::int64_t open_slots = max_positions_ - (static_cast<std::int64_t>(ctx.positions.size()) -
                                                static_cast<std::int64_t>(orders.size()));
    if (open_slots <= 0) return orders;

    // Candidate scan (py:52-80). Iterating asset_id ascending visits assets
    // in (product_id, sub_type) order; the deterministic tie-break the sort
    // below completes. peak_until = groupby market.max over history<=today.
    struct Cand {
        double drawdown;
        AssetId asset;
        double mark;
    };
    std::vector<Cand> candidates;
    auto n = static_cast<AssetId>(ctx.products.n_assets());
    for (AssetId a = 0; a < n; ++a) {
        auto ai = static_cast<std::size_t>(a);
        if (ctx.products.kind[ai] != 0) continue;  // sealed only
        Day rel = ctx.products.released_on[ai];
        if (rel == kNullDay) continue;  // Python: null comparison is false
        if (!(rel <= ctx.today - min_age_days_ && rel >= ctx.today - max_age_days_)) continue;
        auto peak = ctx.market.peak_until(a, ctx.today);
        if (!peak.has_value()) continue;  // no history row => not in groupby
        if (ctx.positions.contains(a) || *peak <= 0.0) continue;
        const double* mark = ctx.marks.find(a);
        if (mark == nullptr) continue;
        double drawdown = 1.0 - *mark / *peak;
        if (drawdown >= min_drawdown_) candidates.push_back(Cand{drawdown, a, *mark});
    }

    // py:83 sort(key=(-drawdown, product_id)); asset id closes exact ties.
    std::sort(candidates.begin(), candidates.end(), [&](const Cand& x, const Cand& y) {
        if (x.drawdown != y.drawdown) return x.drawdown > y.drawdown;
        auto px = ctx.products.product_id[static_cast<std::size_t>(x.asset)];
        auto py_ = ctx.products.product_id[static_cast<std::size_t>(y.asset)];
        if (px != py_) return px < py_;
        return x.asset < y.asset;
    });

    // py:84-91: qty>0 filter happens BEFORE the open_slots cutoff.
    double budget = ctx.cash * budget_frac_;
    std::int64_t taken = 0;
    for (const auto& c : candidates) {
        if (taken >= open_slots) break;
        auto qty = static_cast<std::int64_t>(std::floor(budget / c.mark));
        if (qty > 0) {
            orders.push_back(Order{c.asset, qty});
            ++taken;
        }
    }
    return orders;
}

}  // namespace pkmn
```

- [ ] **Step 3: Write DipBuyer**

`cpp/src/pkmn_engine/strategies/dip_buyer.hpp`:

```cpp
#pragma once

// Port of strategies/dip_buyer.py. Stateless: exit timing from
// Position.opened_on (always set by engine fills).

#include <cstdint>
#include <vector>

#include "pkmn_engine/strategy.hpp"

namespace pkmn {

class DipBuyer final : public Strategy {
  public:
    DipBuyer(std::int64_t dip_window_days, double dip_threshold, std::int64_t hold_days,
             double take_profit, std::int64_t max_positions, double budget_frac,
             double min_price)
        : dip_window_days_(dip_window_days),
          dip_threshold_(dip_threshold),
          hold_days_(hold_days),
          take_profit_(take_profit),
          max_positions_(max_positions),
          budget_frac_(budget_frac),
          min_price_(min_price) {}

    std::vector<Order> on_bar(const Context& ctx) override;

  private:
    std::int64_t dip_window_days_;
    double dip_threshold_;
    std::int64_t hold_days_;
    double take_profit_;
    std::int64_t max_positions_;
    double budget_frac_;
    double min_price_;
};

}  // namespace pkmn
```

`cpp/src/pkmn_engine/strategies/dip_buyer.cpp`:

```cpp
#include "pkmn_engine/strategies/dip_buyer.hpp"

#include <algorithm>
#include <cmath>

namespace pkmn {

std::vector<Order> DipBuyer::on_bar(const Context& ctx) {
    std::vector<Order> orders;

    // Sells first (dip_buyer.py:56-68).
    auto held = ctx.positions.entries();
    std::stable_sort(held.begin(), held.end(), [&](const auto& a, const auto& b) {
        return ctx.products.product_id[static_cast<std::size_t>(a.key)] <
               ctx.products.product_id[static_cast<std::size_t>(b.key)];
    });
    for (const auto& e : held) {
        const double* mark = ctx.marks.find(e.key);
        bool too_old = (ctx.today - e.value.opened_on) >= hold_days_;
        bool hit_target = mark != nullptr && *mark >= e.value.avg_cost * take_profit_;
        if (too_old || hit_target) orders.push_back(Order{e.key, -e.value.quantity});
    }

    std::int64_t open_slots = max_positions_ - (static_cast<std::int64_t>(ctx.positions.size()) -
                                                static_cast<std::int64_t>(orders.size()));
    if (open_slots <= 0) return orders;

    // Entries (py:74-97): singles whose last print at-or-before
    // window_start exists (the groupby membership condition).
    Day window_start = ctx.today - static_cast<Day>(dip_window_days_);
    struct Cand {
        double ret;
        AssetId asset;
        double mark;
    };
    std::vector<Cand> candidates;
    auto n = static_cast<AssetId>(ctx.products.n_assets());
    for (AssetId a = 0; a < n; ++a) {
        if (ctx.products.kind[static_cast<std::size_t>(a)] != 1) continue;  // singles
        auto past = ctx.market.last_price_at_or_before(a, window_start);
        if (!past.has_value()) continue;
        if (ctx.positions.contains(a) || *past <= 0.0) continue;
        const double* mark = ctx.marks.find(a);
        if (mark == nullptr || *mark < min_price_) continue;
        double ret = *mark / *past - 1.0;
        if (ret <= -dip_threshold_) candidates.push_back(Cand{ret, a, *mark});
    }

    // py:99 sort(key=(ret, product_id)) — deepest dip (most negative) first.
    std::sort(candidates.begin(), candidates.end(), [&](const Cand& x, const Cand& y) {
        if (x.ret != y.ret) return x.ret < y.ret;
        auto px = ctx.products.product_id[static_cast<std::size_t>(x.asset)];
        auto py_ = ctx.products.product_id[static_cast<std::size_t>(y.asset)];
        if (px != py_) return px < py_;
        return x.asset < y.asset;
    });

    double budget = ctx.cash * budget_frac_;
    std::int64_t taken = 0;
    for (const auto& c : candidates) {
        if (taken >= open_slots) break;
        auto qty = static_cast<std::int64_t>(std::floor(budget / c.mark));
        if (qty > 0) {
            orders.push_back(Order{c.asset, qty});
            ++taken;
        }
    }
    return orders;
}

}  // namespace pkmn
```

- [ ] **Step 4: Register in the factory, adapter, CMake**

In `factory.cpp`, add includes and cases:

```cpp
#include "pkmn_engine/strategies/dip_buyer.hpp"
#include "pkmn_engine/strategies/sealed_accumulation.hpp"
```

```cpp
    if (name == "sealed-accumulation") {
        return std::make_unique<SealedAccumulation>(
            iparam(params, "min_age_days", 60), iparam(params, "max_age_days", 365),
            param(params, "min_drawdown", 0.25), param(params, "take_profit", 1.5),
            iparam(params, "max_positions", 10), param(params, "budget_frac", 0.10));
    }
    if (name == "dip-buyer") {
        return std::make_unique<DipBuyer>(
            iparam(params, "dip_window_days", 7), param(params, "dip_threshold", 0.30),
            iparam(params, "hold_days", 30), param(params, "take_profit", 1.25),
            iparam(params, "max_positions", 10), param(params, "budget_frac", 0.10),
            param(params, "min_price", 3.0));
    }
```

(Defaults copied from the Python constructors — sealed_accumulation.py:21-29, dip_buyer.py:34-43.)

In `native.py`: `NATIVE_STRATEGY_NAMES = frozenset({"buy-and-hold", "sealed-accumulation", "dip-buyer"})`.

CMake: add both `.cpp` files to `pkmn_engine_core` and `tests/test_strategies.cpp` to `engine_tests`.

- [ ] **Step 5: Add a focused Catch2 behavior test**

`cpp/tests/test_strategies.cpp`:

```cpp
#include <catch2/catch_test_macros.hpp>

#include "pkmn_engine/backtest.hpp"
#include "pkmn_engine/strategies/dip_buyer.hpp"
#include "pkmn_engine/strategies/sealed_accumulation.hpp"

using namespace pkmn;

TEST_CASE("dip buyer: buys the dip, exits on hold_days from the FILL date") {
    // one single, price 10 for days 100-104, crashes to 6 on day 105+.
    std::vector<Day> days;
    std::vector<PriceRow> rows;
    std::vector<MarkEvent> events;
    for (Day d = 100; d <= 118; ++d) {
        double px = d < 105 ? 10.0 : 6.0;
        days.push_back(d);
        rows.push_back({d, 0, px, px * 1.2, px * 0.9});
        if (d == 100 || d == 105) events.push_back({d, 0, px});
    }
    MarketView mkt(1, days, rows, events);
    ProductTable prods{{3}, {1}, {100}};
    CostModel cm;
    DipBuyer strat(3, 0.20, 5, 99.0, 10, 1.0, 3.0);  // 20% dip vs 3d ago, hold 5
    auto res = run_backtest(mkt, prods, strat, cm, 100.0);
    REQUIRE(res.fills.size() >= 2);
    CHECK(res.fills[0].quantity > 0);   // dip entry
    CHECK(res.fills[1].quantity < 0);   // hold_days exit
    // exit fill lands hold_days+1 after entry: emitted when
    // (today - opened_on) >= 5, filled T+1.
    CHECK(res.fills[1].day - res.fills[0].day == 6);
}

TEST_CASE("sealed accumulation: age gate excludes too-new and too-old product") {
    // two sealed products in deep drawdown; only asset 0 is inside the age band
    std::vector<Day> days{200, 201, 202};
    std::vector<PriceRow> rows;
    std::vector<MarkEvent> events;
    for (Day d = 200; d <= 202; ++d) {
        double px = d == 200 ? 100.0 : 50.0;  // 50% drawdown from peak
        for (AssetId a = 0; a < 2; ++a) {
            rows.push_back({d, a, px, px * 1.1, px * 0.9});
            if (d == 200 || d == 201) events.push_back({d, a, px});
        }
    }
    MarketView mkt(2, days, rows, events);
    // asset 0 released 100 days ago (in band 60..365); asset 1 released
    // 10 days ago (too new)
    ProductTable prods{{1, 2}, {0, 0}, {100, 190}};
    CostModel cm;
    SealedAccumulation strat(60, 365, 0.25, 99.0, 10, 1.0);
    auto res = run_backtest(mkt, prods, strat, cm, 1000.0);
    REQUIRE(!res.fills.empty());
    for (const auto& f : res.fills) CHECK(f.asset == 0);
}
```

- [ ] **Step 6: Build, run everything**

```bash
cmake --build cpp/build -j && ctest --test-dir cpp/build --output-on-failure
uv sync --reinstall-package pkmn-quant
uv run pytest tests/test_native_parity.py -v
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Expected: all green, differential tests exact.

- [ ] **Step 7: Commit**

```bash
git add cpp/ src/pkmn_quant/engine/native.py tests/test_native_parity.py
git commit -m "feat(cpp): native sealed-accumulation + dip-buyer, bit-for-bit vs Python"
```

---

### Task 8: Native strategies II — CrossSectionalMomentum and CostAwareReversion

**Files:**
- Create: `cpp/src/pkmn_engine/strategies/momentum.hpp`/`.cpp`, `cpp/src/pkmn_engine/strategies/cost_aware_reversion.hpp`/`.cpp`
- Modify: `cpp/src/pkmn_engine/strategies/factory.cpp`, `cpp/CMakeLists.txt`, `src/pkmn_quant/engine/native.py` (`NATIVE_STRATEGY_NAMES` complete), `tests/test_native_parity.py`
- Test: `cpp/tests/test_strategies.cpp` (extend), `tests/test_native_parity.py`

**Interfaces:**
- Consumes: same as Task 7.
- Produces: `pkmn::CrossSectionalMomentum(lookback_days, top_n, rebalance_days, min_price)`, `pkmn::CostAwareReversion(dip_window_days, dip_threshold, min_edge, take_profit, max_hold_days, max_positions, budget_frac, min_price, fee_rate, shipping_per_line)`; factory names `"xs-momentum"`, `"cost-aware-reversion"`; final `NATIVE_STRATEGY_NAMES = frozenset({"buy-and-hold", "sealed-accumulation", "dip-buyer", "xs-momentum", "cost-aware-reversion"})`.

- [ ] **Step 1: Write the failing differential tests**

Append to `tests/test_native_parity.py`:

```python
@pytest.mark.parametrize("impact", [False, True])
def test_momentum_parity(tmp_path: Path, impact: bool) -> None:
    from pkmn_quant.strategies.momentum import CrossSectionalMomentum

    seed_rich(tmp_path)
    wh = Warehouse(Paths(root=tmp_path))
    cm = CostModel(impact_enabled=impact)
    end = START + timedelta(days=39)
    py = Backtest(
        warehouse=wh,
        strategy=CrossSectionalMomentum(
            lookback_days=10, top_n=3, rebalance_days=5, min_price=3.0
        ),
        cost_model=cm, start=START, end=end, initial_cash=1000.0,
    ).run()
    cpp = NativeBacktest(
        warehouse=wh,
        strategy=NativeStrategySpec(
            "xs-momentum",
            {"lookback_days": 10.0, "top_n": 3.0, "rebalance_days": 5.0, "min_price": 3.0},
        ),
        cost_model=cm, start=START, end=end, initial_cash=1000.0,
    ).run()
    assert len(py.fills) > 2
    assert_results_equal(py, cpp)


@pytest.mark.parametrize("impact", [False, True])
def test_cost_aware_reversion_parity(tmp_path: Path, impact: bool) -> None:
    from pkmn_quant.strategies.cost_aware_reversion import CostAwareReversion

    seed_rich(tmp_path)
    wh = Warehouse(Paths(root=tmp_path))
    cm = CostModel(impact_enabled=impact)
    end = START + timedelta(days=39)
    py = Backtest(
        warehouse=wh,
        strategy=CostAwareReversion(
            dip_window_days=10, dip_threshold=0.15, min_edge=0.02, take_profit=1.05,
            max_hold_days=20, max_positions=5, budget_frac=0.4, min_price=3.0,
        ),
        cost_model=cm, start=START, end=end, initial_cash=1000.0,
    ).run()
    cpp = NativeBacktest(
        warehouse=wh,
        strategy=NativeStrategySpec(
            "cost-aware-reversion",
            {
                "dip_window_days": 10.0, "dip_threshold": 0.15, "min_edge": 0.02,
                "take_profit": 1.05, "max_hold_days": 20.0, "max_positions": 5.0,
                "budget_frac": 0.4, "min_price": 3.0,
            },
        ),
        cost_model=cm, start=START, end=end, initial_cash=1000.0,
    ).run()
    assert len(py.fills) > 0
    assert_results_equal(py, cpp)
```

Run → FAIL (unknown native strategy).

- [ ] **Step 2: Write CrossSectionalMomentum**

`cpp/src/pkmn_engine/strategies/momentum.hpp`:

```cpp
#pragma once

// Port of strategies/momentum.py. Stateless: rebalance clock derived from
// the newest Position.opened_on.

#include <cstdint>
#include <vector>

#include "pkmn_engine/strategy.hpp"

namespace pkmn {

class CrossSectionalMomentum final : public Strategy {
  public:
    CrossSectionalMomentum(std::int64_t lookback_days, std::int64_t top_n,
                           std::int64_t rebalance_days, double min_price)
        : lookback_days_(lookback_days),
          top_n_(top_n),
          rebalance_days_(rebalance_days),
          min_price_(min_price) {}

    std::vector<Order> on_bar(const Context& ctx) override;

  private:
    bool rebalance_due_(const Context& ctx) const;

    std::int64_t lookback_days_;
    std::int64_t top_n_;
    std::int64_t rebalance_days_;
    double min_price_;
};

}  // namespace pkmn
```

`cpp/src/pkmn_engine/strategies/momentum.cpp`:

```cpp
#include "pkmn_engine/strategies/momentum.hpp"

#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace pkmn {

bool CrossSectionalMomentum::rebalance_due_(const Context& ctx) const {
    // momentum.py:55-67: flat portfolio evaluates every bar; else newest
    // opened_on approximates the last rebalance buy.
    if (ctx.positions.size() == 0) return true;
    Day newest = kNullDay;
    for (const auto& e : ctx.positions.entries()) newest = std::max(newest, e.value.opened_on);
    return (ctx.today - newest) >= rebalance_days_;
}

std::vector<Order> CrossSectionalMomentum::on_bar(const Context& ctx) {
    if (!rebalance_due_(ctx)) return {};

    // momentum.py:73-92: rank singles by trailing return.
    Day window_start = ctx.today - static_cast<Day>(lookback_days_);
    struct Mom {
        double ret;
        AssetId asset;
        double mark;
    };
    std::vector<Mom> momentum;
    auto n = static_cast<AssetId>(ctx.products.n_assets());
    for (AssetId a = 0; a < n; ++a) {
        if (ctx.products.kind[static_cast<std::size_t>(a)] != 1) continue;
        auto past = ctx.market.last_price_at_or_before(a, window_start);
        if (!past.has_value()) continue;  // groupby membership
        const double* mark = ctx.marks.find(a);
        if (mark == nullptr || *mark < min_price_ || *past <= 0.0) continue;
        momentum.push_back(Mom{*mark / *past - 1.0, a, *mark});
    }
    // py:91 sort(key=(-ret, product_id)); asset id closes exact ties.
    std::sort(momentum.begin(), momentum.end(), [&](const Mom& x, const Mom& y) {
        if (x.ret != y.ret) return x.ret > y.ret;
        auto px = ctx.products.product_id[static_cast<std::size_t>(x.asset)];
        auto py_ = ctx.products.product_id[static_cast<std::size_t>(y.asset)];
        if (px != py_) return px < py_;
        return x.asset < y.asset;
    });
    // target dict: insertion order = ranking order (py:92)
    InsertionMap<double> target(ctx.products.n_assets());
    auto keep = std::min<std::size_t>(momentum.size(), static_cast<std::size_t>(top_n_));
    for (std::size_t i = 0; i < keep; ++i) target.set(momentum[i].asset, momentum[i].mark);

    std::vector<Order> orders;
    // Sells first (py:95-99): everything not in the target.
    auto held = ctx.positions.entries();
    std::stable_sort(held.begin(), held.end(), [&](const auto& a, const auto& b) {
        return ctx.products.product_id[static_cast<std::size_t>(a.key)] <
               ctx.products.product_id[static_cast<std::size_t>(b.key)];
    });
    for (const auto& e : held) {
        if (!target.contains(e.key)) orders.push_back(Order{e.key, -e.value.quantity});
    }

    if (target.size() == 0) return orders;

    // py:104-107: equity from ctx marks; held asset without a mark is a bug.
    double held_value_sum = 0.0;
    for (const auto& e : ctx.positions.entries()) {
        const double* m = ctx.marks.find(e.key);
        if (m == nullptr) throw std::out_of_range("held asset without mark");
        held_value_sum += static_cast<double>(e.value.quantity) * *m;
    }
    double equity = ctx.cash + held_value_sum;
    double per_name = equity / static_cast<double>(target.size());

    // py:110-115: buys sorted by product_id (stable over target insertion
    // order = ranking order).
    auto targets = target.entries();
    std::stable_sort(targets.begin(), targets.end(), [&](const auto& a, const auto& b) {
        return ctx.products.product_id[static_cast<std::size_t>(a.key)] <
               ctx.products.product_id[static_cast<std::size_t>(b.key)];
    });
    for (const auto& t : targets) {
        const Position* held_pos = ctx.positions.find(t.key);
        double held_value =
            held_pos ? static_cast<double>(held_pos->quantity) * t.value : 0.0;
        auto qty = static_cast<std::int64_t>(std::floor((per_name - held_value) / t.value));
        if (qty > 0) orders.push_back(Order{t.key, qty});
    }
    return orders;
}

}  // namespace pkmn
```

PARITY NOTE for the implementer: Python computes `equity = ctx.cash + sum(...)` where `sum()` adds position values sequentially starting from `0` and then adds to `ctx.cash`. The code above mirrors that exactly (`held_value_sum` accumulates from 0.0 in insertion order, then `ctx.cash + held_value_sum`). Do NOT "simplify" to `equity = ctx.cash; equity += ...` — that changes the addition order and can flip the last bit.

- [ ] **Step 3: Write CostAwareReversion**

`cpp/src/pkmn_engine/strategies/cost_aware_reversion.hpp`:

```cpp
#pragma once

// Port of strategies/cost_aware_reversion.py. The fee/shipping hurdle uses
// the strategy's OWN cost assumptions (Python: CostModel() defaults),
// independent of the engine's cost model — mirrored here as plain fields.

#include <cstdint>
#include <vector>

#include "pkmn_engine/strategy.hpp"

namespace pkmn {

class CostAwareReversion final : public Strategy {
  public:
    CostAwareReversion(std::int64_t dip_window_days, double dip_threshold, double min_edge,
                       double take_profit, std::int64_t max_hold_days,
                       std::int64_t max_positions, double budget_frac, double min_price,
                       double fee_rate, double shipping_per_line)
        : dip_window_days_(dip_window_days),
          dip_threshold_(dip_threshold),
          min_edge_(min_edge),
          take_profit_(take_profit),
          max_hold_days_(max_hold_days),
          max_positions_(max_positions),
          budget_frac_(budget_frac),
          min_price_(min_price),
          fee_rate_(fee_rate),
          shipping_per_line_(shipping_per_line) {}

    std::vector<Order> on_bar(const Context& ctx) override;

  private:
    std::int64_t dip_window_days_;
    double dip_threshold_;
    double min_edge_;
    double take_profit_;
    std::int64_t max_hold_days_;
    std::int64_t max_positions_;
    double budget_frac_;
    double min_price_;
    double fee_rate_;
    double shipping_per_line_;
};

}  // namespace pkmn
```

`cpp/src/pkmn_engine/strategies/cost_aware_reversion.cpp`:

```cpp
#include "pkmn_engine/strategies/cost_aware_reversion.hpp"

#include <algorithm>
#include <cmath>

namespace pkmn {

std::vector<Order> CostAwareReversion::on_bar(const Context& ctx) {
    std::vector<Order> orders;

    // Sells first (cost_aware_reversion.py:57-69).
    auto held = ctx.positions.entries();
    std::stable_sort(held.begin(), held.end(), [&](const auto& a, const auto& b) {
        return ctx.products.product_id[static_cast<std::size_t>(a.key)] <
               ctx.products.product_id[static_cast<std::size_t>(b.key)];
    });
    for (const auto& e : held) {
        const double* mark = ctx.marks.find(e.key);
        bool too_old = (ctx.today - e.value.opened_on) >= max_hold_days_;
        bool hit_target = mark != nullptr && *mark >= e.value.avg_cost * take_profit_;
        if (too_old || hit_target) orders.push_back(Order{e.key, -e.value.quantity});
    }

    std::int64_t open_slots = max_positions_ - (static_cast<std::int64_t>(ctx.positions.size()) -
                                                static_cast<std::int64_t>(orders.size()));
    if (open_slots <= 0) return orders;

    // Entries (py:75-97): ALL assets (no kind filter), window high over
    // [window_start, today].
    Day window_start = ctx.today - static_cast<Day>(dip_window_days_);
    struct Cand {
        double neg_dip;  // Python stores -dip as the sort key
        AssetId asset;
        double mark;
    };
    std::vector<Cand> candidates;
    auto n = static_cast<AssetId>(ctx.products.n_assets());
    for (AssetId a = 0; a < n; ++a) {
        auto high = ctx.market.max_in_window(a, window_start, ctx.today);
        if (!high.has_value()) continue;  // groupby membership
        if (ctx.positions.contains(a)) continue;
        const double* mark = ctx.marks.find(a);
        if (mark == nullptr || *mark < min_price_ || *high <= 0.0) continue;
        double dip = 1.0 - *mark / *high;
        if (dip < dip_threshold_) continue;
        double rebound = *high / *mark - 1.0;
        // py:94: fee_rate + 2 * shipping / mark (left-to-right precedence)
        double hurdle = fee_rate_ + 2.0 * shipping_per_line_ / *mark;
        if (rebound < hurdle + min_edge_) continue;
        candidates.push_back(Cand{-dip, a, *mark});
    }

    // py:99 sort(key=(-dip, product_id)) ascending.
    std::sort(candidates.begin(), candidates.end(), [&](const Cand& x, const Cand& y) {
        if (x.neg_dip != y.neg_dip) return x.neg_dip < y.neg_dip;
        auto px = ctx.products.product_id[static_cast<std::size_t>(x.asset)];
        auto py_ = ctx.products.product_id[static_cast<std::size_t>(y.asset)];
        if (px != py_) return px < py_;
        return x.asset < y.asset;
    });

    double budget = ctx.cash * budget_frac_;
    std::int64_t taken = 0;
    for (const auto& c : candidates) {
        if (taken >= open_slots) break;
        auto qty = static_cast<std::int64_t>(std::floor(budget / c.mark));
        if (qty > 0) {
            orders.push_back(Order{c.asset, qty});
            ++taken;
        }
    }
    return orders;
}

}  // namespace pkmn
```

- [ ] **Step 4: Register both, extend NATIVE_STRATEGY_NAMES, add a Catch2 test**

factory.cpp additions:

```cpp
    if (name == "xs-momentum") {
        return std::make_unique<CrossSectionalMomentum>(
            iparam(params, "lookback_days", 60), iparam(params, "top_n", 10),
            iparam(params, "rebalance_days", 30), param(params, "min_price", 3.0));
    }
    if (name == "cost-aware-reversion") {
        return std::make_unique<CostAwareReversion>(
            iparam(params, "dip_window_days", 30), param(params, "dip_threshold", 0.25),
            param(params, "min_edge", 0.05), param(params, "take_profit", 1.25),
            iparam(params, "max_hold_days", 120), iparam(params, "max_positions", 10),
            param(params, "budget_frac", 0.10), param(params, "min_price", 3.0),
            0.1275, 1.0);  // hurdle costs: CostModel() defaults, like the registry
    }
```

native.py:

```python
NATIVE_STRATEGY_NAMES: frozenset[str] = frozenset(
    {"buy-and-hold", "sealed-accumulation", "dip-buyer", "xs-momentum", "cost-aware-reversion"}
)
```

Append to `cpp/tests/test_strategies.cpp`:

```cpp
#include "pkmn_engine/strategies/momentum.hpp"

TEST_CASE("momentum: flat portfolio rebalances immediately, holds winners") {
    // two singles: asset 0 rising, asset 1 falling; top_n=1 must pick 0.
    std::vector<Day> days;
    std::vector<PriceRow> rows;
    std::vector<MarkEvent> events;
    for (Day d = 100; d <= 110; ++d) {
        double up = 10.0 + static_cast<double>(d - 100);
        double down = 20.0 - static_cast<double>(d - 100);
        days.push_back(d);
        rows.push_back({d, 0, up, up * 1.2, up * 0.9});
        rows.push_back({d, 1, down, down * 1.2, down * 0.9});
        events.push_back({d, 0, up});
        events.push_back({d, 1, down});
    }
    MarketView mkt(2, days, rows, events);
    ProductTable prods{{3, 4}, {1, 1}, {100, 100}};
    CostModel cm;
    CrossSectionalMomentum strat(5, 1, 3, 3.0);
    auto res = run_backtest(mkt, prods, strat, cm, 100.0);
    REQUIRE(!res.fills.empty());
    for (const auto& f : res.fills) {
        if (f.quantity > 0) CHECK(f.asset == 0);  // only the winner is bought
    }
}
```

- [ ] **Step 5: Build, run everything, commit**

```bash
cmake --build cpp/build -j && ctest --test-dir cpp/build --output-on-failure
uv sync --reinstall-package pkmn-quant && uv run pytest tests/test_native_parity.py -v
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy
git add cpp/ src/pkmn_quant/engine/native.py tests/test_native_parity.py
git commit -m "feat(cpp): native xs-momentum + cost-aware-reversion complete the rule strategies"
```

---

### Task 9: Callback bridge — ml-ranker (and any Python strategy) on the C++ engine

The C++ side (`CallbackStrategy`, binding lambda) already exists from Task 6; this task proves the bridge with differential tests. The bridge contract: per bar, C++ sends `(day, positions, cash)`; the Python wrapper rebuilds the full `Context` locally (history/marks from its own `MarketData` — identical values by construction) and calls the untouched Python strategy.

**Files:**
- Modify: `tests/test_native_parity.py` (bridge tests)
- Test: `tests/test_native_parity.py`

**Interfaces:**
- Consumes: `NativeBacktest` with a Python `Strategy` instance (bridge path, built in Task 6), `MLRanker` (deterministic: `random_state=0`, ml_ranker.py:102-106).

- [ ] **Step 1: Write the failing bridge tests**

Append to `tests/test_native_parity.py`:

```python
def test_bridge_runs_python_strategy_bit_for_bit(tmp_path: Path) -> None:
    """The bridge path: a Python Strategy instance on the C++ engine."""
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

    py = Backtest(
        warehouse=wh, strategy=make(), cost_model=cm,
        start=START, end=end, initial_cash=1000.0,
    ).run()
    cpp = NativeBacktest(
        warehouse=wh, strategy=make(), cost_model=cm,
        start=START, end=end, initial_cash=1000.0,
    ).run()
    assert len(py.fills) > 0
    assert_results_equal(py, cpp)


def test_bridge_ml_ranker_parity(tmp_path: Path) -> None:
    """ml-ranker (sklearn, random_state=0) runs unmodified via the bridge."""
    from pkmn_quant.strategies.ml_ranker import MLRanker

    seed_rich(tmp_path, n_days=60)
    wh = Warehouse(Paths(root=tmp_path))
    cm = CostModel(impact_enabled=True)
    end = START + timedelta(days=59)

    def make() -> MLRanker:
        return MLRanker(
            horizon_days=5, rebalance_days=7, top_n=2, train_days=30,
            max_iter=50, learning_rate=0.1, min_samples_leaf=5,
        )

    py = Backtest(
        warehouse=wh, strategy=make(), cost_model=cm,
        start=START + timedelta(days=20), end=end, initial_cash=1000.0,
        warmup_days=20,
    ).run()
    cpp = NativeBacktest(
        warehouse=wh, strategy=make(), cost_model=cm,
        start=START + timedelta(days=20), end=end, initial_cash=1000.0,
        warmup_days=20,
    ).run()
    assert_results_equal(py, cpp)
```

NOTE for the implementer: check `MLRanker.__init__` (strategies/ml_ranker.py:40-63) for the exact constructor parameter names before running; adjust the `make()` kwargs if any differ. If the ml-ranker test trades zero times on this fixture, that is acceptable ONLY if the dip-buyer bridge test above trades — the bridge mechanics are what this task proves; note it in the test docstring if so.

Run: `uv run pytest tests/test_native_parity.py -k bridge -v`
Expected: these may PASS immediately (the bridge shipped in Task 6). If they fail, the divergence is in the bridge marshaling (positions order, opened_on conversion) — fix `native.py`/`module.cpp`, not the tests.

- [ ] **Step 2: Run, fix any marshaling gaps, commit**

```bash
uv run pytest tests/test_native_parity.py -v
git add tests/test_native_parity.py
git commit -m "test: callback bridge parity — Python strategies + ml-ranker on the C++ engine"
```

---

### Task 10: CLI `--engine` flag, walkforward wiring, registry recording, dual-engine goldens

**Files:**
- Modify: `src/pkmn_quant/cli.py` (backtest + walkforward commands), `src/pkmn_quant/research/walkforward.py`, `tests/test_cli_backtest.py`
- Test: `tests/test_cli_backtest.py`, `tests/test_walkforward.py` (extend if present; otherwise the CLI test covers the path)

**Interfaces:**
- Consumes: `NativeBacktest`, `NativeStrategySpec`, `NATIVE_STRATEGY_NAMES`.
- Produces: `pkmn backtest --engine {python,cpp}` (default `python`), `pkmn walkforward --engine {python,cpp}`; `run_walkforward(..., engine="python", strategy_name=None)`; run-registry configs gain an `"engine"` key (config hash distinguishes engines forever).

- [ ] **Step 1: Parametrize the golden CLI tests over both engines (failing first)**

In `tests/test_cli_backtest.py`, change `run_cli` and the two golden tests:

```python
def run_cli(root: Path, *extra: str) -> object:
    return CliRunner().invoke(
        app,
        [
            "backtest",
            "--start",
            "2025-06-01",
            "--end",
            "2025-06-03",
            "--cash",
            "100",
            "--root",
            str(root),
            *extra,
        ],
    )
```

(unchanged), then parametrize:

```python
@pytest.mark.parametrize("engine", ["python", "cpp"])
def test_backtest_golden_numbers(tmp_path: Path, engine: str) -> None:
    ...
    result = run_cli(tmp_path, "--no-impact", "--engine", engine)
    ...


@pytest.mark.parametrize("engine", ["python", "cpp"])
def test_backtest_golden_numbers_with_impact(tmp_path: Path, engine: str) -> None:
    ...
    result = run_cli(tmp_path, "--engine", engine)
    ...
```

(Keep the docstrings and assertions untouched — the pinned numbers now validate both engines.)

Run: `uv run pytest tests/test_cli_backtest.py -v` → FAIL (`No such option: --engine`).

- [ ] **Step 2: Add --engine to the backtest command**

In `cli.py` `backtest()` (cli.py:250-341), add the option after `impact`:

```python
    engine: str = typer.Option(
        "python",
        help="Backtest engine: python (reference) or cpp (native, parity-tested).",
    ),
```

Replace the `result = Backtest(...)` block (cli.py:277-284) with:

```python
    if engine == "cpp":
        from pkmn_quant.engine.native import NativeBacktest, NativeStrategySpec

        result = NativeBacktest(
            warehouse=wh,
            strategy=NativeStrategySpec("buy-and-hold", {}, kind=kind),
            cost_model=cm,
            start=start_date,
            end=end_date,
            initial_cash=cash,
        ).run()
    elif engine == "python":
        result = Backtest(
            warehouse=wh,
            strategy=BuyAndHold(kind=kind),
            cost_model=cm,
            start=start_date,
            end=end_date,
            initial_cash=cash,
        ).run()
    else:
        raise typer.BadParameter(f"unknown engine {engine!r}; choose python or cpp")
```

And add `"engine": engine,` to the `config=` dict of `record_run` (after `"kind": kind,`).

- [ ] **Step 3: Thread engine through run_walkforward**

In `src/pkmn_quant/research/walkforward.py`, extend the signature (after `warmup_days`):

```python
def run_walkforward(
    ...
    warmup_days: int = 0,
    engine: str = "python",
    strategy_name: str | None = None,
) -> WalkForwardResult:
```

Add a local runner right after the `objective_metric` validation (before the fold loop), replacing the three inline `Backtest(...)` constructions (walkforward.py:116-124, 129-137, 139-147) with calls to it:

```python
    if engine == "cpp":
        from pkmn_quant.engine.native import (
            NATIVE_STRATEGY_NAMES,
            NativeBacktest,
            NativeStrategySpec,
        )

    def _run(params: Params, window_start: date, window_end: date) -> Result:
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
```

(`Result` needs importing from `pkmn_quant.engine.backtest`; it is already imported there via `Backtest` module — add `Result` to that import.) The three call sites become:

```python
        def evaluate(params: Params, _fold: Fold = fold) -> float:
            result = _run(params, _fold.is_start, _fold.is_end)
            return float(result.summary[objective_metric])

        best = optimizer(fold, evaluate)
        is_result = _run(best, fold.is_start, fold.is_end)
        oos_result = _run(best, fold.oos_start, fold.oos_end)
```

Guard at the top of the function (with the other validation):

```python
    if engine not in ("python", "cpp"):
        raise ValueError(f"unknown engine {engine!r}; choose python or cpp")
    if engine == "cpp" and strategy_name is None:
        raise ValueError("engine='cpp' requires strategy_name")
```

- [ ] **Step 4: Add --engine to the walkforward command**

In `cli.py` `walkforward()`, add the same `engine` typer.Option after `impact`, pass through:

```python
    result = run_walkforward(
        ...,
        warmup_days=warmup_days,
        engine=engine,
        strategy_name=strategy,
    )
```

and add `"engine": engine,` to its `record_run` config dict (after `"cost_model": cm.as_dict(),`).

- [ ] **Step 5: Add a walkforward-cpp smoke test**

Append to `tests/test_native_parity.py`:

```python
def test_walkforward_cpp_matches_python(tmp_path: Path) -> None:
    """Whole-walkforward differential: fixed params (trivial optimizer), both engines."""
    from pkmn_quant.research.walkforward import run_walkforward
    from pkmn_quant.strategies.dip_buyer import DipBuyer

    seed_rich(tmp_path, n_days=60)
    wh = Warehouse(Paths(root=tmp_path))
    cm = CostModel(impact_enabled=True)
    fixed = {
        "dip_window_days": 5, "dip_threshold": 0.10, "hold_days": 7, "take_profit": 1.05,
    }

    def factory(p: dict[str, float | int]) -> DipBuyer:
        return DipBuyer(
            dip_window_days=int(p["dip_window_days"]), dip_threshold=float(p["dip_threshold"]),
            hold_days=int(p["hold_days"]), take_profit=float(p["take_profit"]),
        )

    def optimizer(fold: object, evaluate: object) -> dict[str, float | int]:
        return dict(fixed)

    kwargs = dict(
        warehouse=wh, strategy_factory=factory, optimizer=optimizer, cost_model=cm,
        start=START, end=START + timedelta(days=59), is_days=20, oos_days=10,
        initial_cash=1000.0, warmup_days=10,
    )
    py = run_walkforward(**kwargs)  # type: ignore[arg-type]
    cpp = run_walkforward(**kwargs, engine="cpp", strategy_name="dip-buyer")  # type: ignore[arg-type]
    assert py.stitched_curve["equity"].to_list() == cpp.stitched_curve["equity"].to_list()
    assert py.summary == cpp.summary
```

(If mypy rejects the `**kwargs` spreading, expand the calls explicitly — exact keyword args as above.)

- [ ] **Step 6: Run everything, commit**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy
git add src/pkmn_quant/cli.py src/pkmn_quant/research/walkforward.py tests/
git commit -m "feat: --engine {python,cpp} on backtest/walkforward, engine recorded in runs registry"
```

---

### Task 11: Full-data acceptance, benchmark, docs

The acceptance test from the spec: bit-for-bit on all 874 real days for every strategy, plus the measured (not guessed) speedup table.

**Files:**
- Create: `scripts/parity_full.py`, `scripts/bench_engines.py`
- Modify: `docs/research-findings-2026-07.md` (new Plan 10 section), `README.md` (engine section + commands), `CLAUDE.md` (status, commands, layout)
- Test: manual script runs against `data/` (gitignored, local-only)

**Interfaces:**
- Consumes: everything.
- Produces: `uv run python scripts/parity_full.py` (exit 0 = all strategies bit-for-bit; `--ml` includes the slow bridge run), `uv run python scripts/bench_engines.py` (markdown speedup table on stdout).

- [ ] **Step 1: Write scripts/parity_full.py**

```python
"""Full-data parity acceptance: every strategy, both engines, 874 days.

Bit-for-bit or bust (spec 2026-07-14). Exit 0 only if every comparison is
exact. Run from the repo root (needs data/). --ml adds the ml-ranker bridge
run (slow: sklearn trains in-loop).
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date

from pkmn_quant.config import Paths
from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.backtest import Backtest, Result
from pkmn_quant.engine.costs import CostModel
from pkmn_quant.engine.native import NativeBacktest, NativeStrategySpec
from pkmn_quant.engine.strategy import Strategy
from pkmn_quant.strategies.buy_and_hold import BuyAndHold
from pkmn_quant.strategies.cost_aware_reversion import CostAwareReversion
from pkmn_quant.strategies.dip_buyer import DipBuyer
from pkmn_quant.strategies.ml_ranker import MLRanker
from pkmn_quant.strategies.momentum import CrossSectionalMomentum
from pkmn_quant.strategies.sealed_accumulation import SealedAccumulation

START, END = date(2024, 3, 1), date(2026, 6, 30)
CASH = 10_000.0
WARMUP = 120

RULE_STRATEGIES: list[tuple[str, Strategy]] = [
    ("buy-and-hold", BuyAndHold(kind="sealed")),
    ("sealed-accumulation", SealedAccumulation()),
    ("dip-buyer", DipBuyer()),
    ("xs-momentum", CrossSectionalMomentum()),
    ("cost-aware-reversion", CostAwareReversion()),
]


def compare(name: str, py: Result, cpp: Result) -> bool:
    ok = True
    eq_py = py.equity_curve["equity"].to_list()
    eq_cpp = cpp.equity_curve["equity"].to_list()
    if eq_py != eq_cpp:
        first = next(i for i, (a, b) in enumerate(zip(eq_py, eq_cpp)) if a != b)
        print(f"  EQUITY DIVERGES at index {first}: {eq_py[first]!r} != {eq_cpp[first]!r}")
        ok = False
    if len(py.fills) != len(cpp.fills):
        print(f"  FILL COUNT differs: {len(py.fills)} vs {len(cpp.fills)}")
        ok = False
    else:
        for i, (a, b) in enumerate(zip(py.fills, cpp.fills)):
            same = (
                a.day == b.day and a.asset == b.asset and a.quantity == b.quantity
                and a.price == b.price and a.fees == b.fees and a.impact == b.impact
            )
            if not same:
                print(f"  FILL {i} differs: {a} vs {b}")
                ok = False
                break
    print(f"{'PASS' if ok else 'FAIL'}  {name}  ({len(py.fills)} fills)")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ml", action="store_true", help="include ml-ranker (slow)")
    parser.add_argument("--impact", action="store_true", default=True)
    args = parser.parse_args()

    wh = Warehouse(Paths(root=Paths().root))
    cm = CostModel(impact_enabled=args.impact)
    all_ok = True
    for name, strategy in RULE_STRATEGIES:
        t0 = time.perf_counter()
        py = Backtest(
            warehouse=wh, strategy=strategy, cost_model=cm,
            start=START, end=END, initial_cash=CASH, warmup_days=WARMUP,
        ).run()
        t_py = time.perf_counter() - t0
        spec = (
            NativeStrategySpec("buy-and-hold", {}, kind="sealed")
            if name == "buy-and-hold"
            else NativeStrategySpec(name, {})
        )
        t0 = time.perf_counter()
        cpp = NativeBacktest(
            warehouse=wh, strategy=spec, cost_model=cm,
            start=START, end=END, initial_cash=CASH, warmup_days=WARMUP,
        ).run()
        t_cpp = time.perf_counter() - t0
        print(f"[{name}] python {t_py:.2f}s / cpp {t_cpp:.2f}s")
        all_ok &= compare(name, py, cpp)

    if args.ml:
        py = Backtest(
            warehouse=wh, strategy=MLRanker(), cost_model=cm,
            start=START, end=END, initial_cash=CASH, warmup_days=WARMUP,
        ).run()
        cpp = NativeBacktest(
            warehouse=wh, strategy=MLRanker(), cost_model=cm,
            start=START, end=END, initial_cash=CASH, warmup_days=WARMUP,
        ).run()
        all_ok &= compare("ml-ranker (bridge)", py, cpp)

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
```

NOTE: check `Paths()` — if it has no zero-arg default, use `Paths(root=Path("."))` (see `pkmn_quant/config.py`). Check `MLRanker()` default constructor likewise.

- [ ] **Step 2: Run the acceptance check**

```bash
uv run python scripts/parity_full.py
```

Expected: `PASS` for all five rule strategies. **If any strategy FAILs**, this is the polars-order risk from the spec materializing: the script prints the first divergent day/fill. Diagnose per the spec's resolution order (reformulate the Python strategy math, or mirror polars' order in C++; goldens updated in the same commit with a hand-derivation). Do not proceed to benchmarks until every line is PASS. Then:

```bash
uv run python scripts/parity_full.py --ml   # slow; run once, record the result
```

- [ ] **Step 3: Write scripts/bench_engines.py**

```python
"""Measured engine speedup: python vs cpp, full 874-day range, best of 3.

Prints a markdown table for docs/research-findings-2026-07.md. Separates
total wall-clock from engine-only time (NativeBacktest total includes the
polars load + flatten, which the C++ loop does not shrink).
"""

from __future__ import annotations

import time
from datetime import date

from pkmn_quant.config import Paths
from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.backtest import Backtest
from pkmn_quant.engine.costs import CostModel
from pkmn_quant.engine.native import NativeBacktest, NativeStrategySpec
from pkmn_quant.strategies.buy_and_hold import BuyAndHold
from pkmn_quant.strategies.dip_buyer import DipBuyer
from pkmn_quant.strategies.sealed_accumulation import SealedAccumulation

START, END = date(2024, 3, 1), date(2026, 6, 30)
CASH = 10_000.0
WARMUP = 120
REPS = 3

CASES = [
    ("buy-and-hold", lambda: BuyAndHold(kind="sealed"),
     NativeStrategySpec("buy-and-hold", {}, kind="sealed")),
    ("sealed-accumulation", SealedAccumulation, NativeStrategySpec("sealed-accumulation", {})),
    ("dip-buyer", DipBuyer, NativeStrategySpec("dip-buyer", {})),
]


def best_of(fn: object, reps: int = REPS) -> float:
    times = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()  # type: ignore[operator]
        times.append(time.perf_counter() - t0)
    return min(times)


def main() -> None:
    wh = Warehouse(Paths(root=Paths().root))
    cm = CostModel(impact_enabled=True)
    print("| strategy | python (s) | cpp (s) | speedup |")
    print("|---|---|---|---|")
    for name, make_py, spec in CASES:
        t_py = best_of(
            lambda: Backtest(
                warehouse=wh, strategy=make_py(), cost_model=cm,
                start=START, end=END, initial_cash=CASH, warmup_days=WARMUP,
            ).run()
        )
        t_cpp = best_of(
            lambda: NativeBacktest(
                warehouse=wh, strategy=spec, cost_model=cm,
                start=START, end=END, initial_cash=CASH, warmup_days=WARMUP,
            ).run()
        )
        print(f"| {name} | {t_py:.2f} | {t_cpp:.2f} | {t_py / t_cpp:.1f}x |")


if __name__ == "__main__":
    main()
```

(Fix the same `Paths()` note as above; ruff will demand `# noqa: B023` or default-arg binding for the loop lambdas — bind with `lambda make_py=make_py, spec=spec: ...` if flagged.)

- [ ] **Step 4: Run the benchmark, record findings**

```bash
uv run python scripts/bench_engines.py | tee /tmp/bench.md
```

Add a "Plan 10 (2026-07-14): C++ engine" section to `docs/research-findings-2026-07.md` containing: the parity acceptance result (all strategies PASS, ml-ranker bridge PASS), the measured speedup table verbatim, and one honest paragraph: research CONCLUSIONS are unchanged by construction (bit-for-bit parity means every number is identical); what changed is the cost of producing them, and what it unlocks (Plan 11 parallel search, GIL-free).

- [ ] **Step 5: Update README.md and CLAUDE.md**

README: add an "Engines" subsection — the C++ engine, how to select it (`--engine cpp`), the parity guarantee and how to verify it (`scripts/parity_full.py`), the build prerequisites (CLT/cmake for local Catch2; `uv sync` handles the extension), and the measured speedup.

CLAUDE.md: add the Plan 10 status bullet (what shipped, test counts, measured speedup, parity result); add to Commands: `uv run pkmn backtest ... --engine cpp`, `cmake -S cpp -B cpp/build -DPKMN_BUILD_TESTS=ON && cmake --build cpp/build -j && ctest --test-dir cpp/build`, `uv run python scripts/parity_full.py`; add to Layout: `cpp/` description (core lib, Catch2 tests, nanobind binding) and `engine/native.py`; add gotcha: "after editing C++, `uv sync --reinstall-package pkmn-quant`; never enable fast-math or fp-contract — bit-for-bit parity depends on it."

- [ ] **Step 6: Final gates, commit**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy
cmake --build cpp/build -j && ctest --test-dir cpp/build --output-on-failure
git add scripts/ docs/ README.md CLAUDE.md
git commit -m "docs: Plan 10 findings — full-data parity PASS + measured engine speedup"
```

---

## Self-review notes (already applied)

- Spec coverage: architecture/build/boundary → Tasks 1–6; native strategies → 5, 7, 8; bridge → 6 (mechanics) + 9 (proof); three-layer testing → Catch2 (2–5, 7–8), differential (6–9), dual-engine goldens (10); CLI + registry → 10; acceptance + benchmark + docs → 11. Error handling (validation → ValueError, loud import failure) → Tasks 4, 6, 10.
- Deviation from spec, intentional: no `quotes.hpp` (mid/low are NaN-able fields on `PriceRow`/`MarketView` — the Quote struct had no behavior to port); mark change-points cross the boundary as data (`mark_events()`) instead of being recomputed in C++ — recomputing polars' within-day tie order in C++ is exactly the class of bug the pass-through eliminates, and the replay cursor itself IS in C++. Both noted where they occur.
- Type consistency: `AssetId`/`Day` int32, quantities int64 everywhere; `InsertionMap` API used identically in Tasks 3–8; factory registered names match `NATIVE_STRATEGY_NAMES` and the Python registry keys exactly.
- Known judgment calls the executor may hit: `nb::ndarray` template/API details and scikit-build-core editable behavior can differ by version — if the exact incantation fails, consult the installed nanobind/scikit-build-core docs and adjust the glue, NOT the core or the parity contract.
