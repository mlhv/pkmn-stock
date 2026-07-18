# Plan 10 design: C++ engine

Date: 2026-07-14
Status: approved design, pre-implementation

## Goal

Port the backtest engine to C++20, validated bit-for-bit against the Python
engine, and wire it into the CLI. Two wins, deliberately together:

1. **Real speed** where the project needs it next: walk-forward parameter
   search runs many backtests per fold, and the C++ engine is the foundation
   Plan 11 (parallel search) will stand on — C++ threads do not hold the GIL.
2. **A production-grade C++ artifact**: modern C++20, a native Catch2 test
   suite, Python bindings, and differential testing against a reference
   implementation — the validation practice real trading firms use for
   engine rewrites.

The Python engine is NOT deleted. It becomes the reference implementation:
every differential test and golden test runs against both engines forever.

Out of scope (later plans): parallel trials/multithreading, point-in-time
warehouse, dashboard changes, porting ml-ranker's model to C++, flipping the
CLI default to the C++ engine.

## Decisions locked during brainstorming

- **Goal**: resume showcase + real speed (not perf-only, not a standalone
  side artifact).
- **Strategy seam**: BOTH native C++ ports of the five rule strategies AND a
  Python-callback bridge so ml-ranker runs on the C++ engine unmodified.
- **Parity bar**: bit-for-bit. Same inputs ⇒ byte-identical fills and equity
  curve. Python floats and C++ doubles are both IEEE-754 64-bit; mirroring
  operation order makes exact equality achievable.
- **Scope**: engine + CLI wiring (`--engine {python,cpp}`), serial trials.
- **Binding**: nanobind. The binding layer is one thin module file crossing
  the boundary once per run; all learning-relevant C++ lives in the core
  library. Swapping to pybind11 later is a one-file change.

## Architecture

```
cpp/
├── CMakeLists.txt
├── src/pkmn_engine/          # pure C++20 static library — no Python, no I/O deps
│   ├── types.hpp             # AssetId (interned int), day indices, Position, Order, Fill
│   ├── costs.hpp/.cpp        # CostModel port
│   ├── quotes.hpp            # Quote {mid, low} for the impact model
│   ├── portfolio.hpp/.cpp
│   ├── execution.hpp/.cpp    # T+1 fills + walk-the-spread impact
│   ├── backtest.hpp/.cpp     # the daily event loop
│   └── strategies/           # Strategy base + five rule strategies + callback hook
├── tests/                    # Catch2 unit tests for the C++ core
└── bindings/module.cpp       # nanobind glue → importable as pkmn_quant._engine
```

Boundary decisions:

- **The core is a library the binding uses, not a binding with logic in
  it.** Catch2 tests exercise the core directly in C++, independent of
  Python.
- **Metrics stay in Python.** C++ returns equity curve + fills; the existing
  `summarize()` computes the summary for both engines. Sharpe/Sortino/CAGR
  stay single-sourced; the parity surface shrinks to two outputs.
- **Dates become day indices, assets become interned integers** inside C++.
  The Python adapter owns both mappings. C++ never parses a date or hashes a
  string in the hot loop.

## Build integration

- `scikit-build-core` becomes the build backend in `pyproject.toml`;
  `uv sync` configures CMake, compiles, and installs `pkmn_quant._engine`.
  No new commands for the developer workflow.
- CMake fetches Catch2 via `FetchContent`; nanobind arrives as a pip
  build dependency.
- CI: compiler already on GitHub runners; add one step to build and run the
  Catch2 suite. The four gates stay the four gates.
- mypy sees the extension through a `.pyi` stub for `pkmn_quant._engine`.

## Data boundary (once per run, not per day)

New Python adapter `NativeBacktest`, mirroring `Backtest`'s constructor:

1. Loads `MarketData` from the warehouse exactly as today (polars does all
   I/O; C++ has no Arrow/Parquet dependency).
2. Flattens day-partitioned prices into contiguous numpy arrays —
   `(day_idx, asset_id, market, mid, low)` — plus an int-encoded
   product-kind table (so strategies can filter for sealed). Zero-copy views
   into C++.
3. C++ rebuilds per-day partitions and **recomputes carry-forward marks
   internally** ("last seen price wins" — no float arithmetic, parity-safe).
4. Returns fills + equity arrays; the adapter repackages them into the same
   `Result` dataclass. The runs registry, reports, and walk-forward
   stitching never know which engine ran.

## Strategies

**Native.** C++ `Strategy` base: `on_bar(const Context&) ->
std::vector<Order>` plus `reset()`; `Context` is a view struct (day index,
history view, positions, cash, marks). The five rule strategies
(buy-and-hold, sealed-accumulation, dip-buyer, momentum,
cost-aware-reversion) port as subclasses. A **factory keyed by name + params
map** (`std::map<std::string, double>`) constructs them — exactly what
optuna produces, so walk-forward's tuning loop passes each trial's params
across the boundary unchanged.

Contract carried over: reset-safe, no hidden per-run state beyond
`opened_on` (Catch2 pins run-twice-identical-output, mirroring the Python
tests).

**Bridge (ml-ranker).** `CallbackStrategy : Strategy` holds a Python
callable. Per bar it crosses with only the small state (day, positions,
cash, marks). The **history DataFrame never crosses the boundary**: the
Python side of the bridge already holds `MarketData` (the adapter built the
arrays from it), reconstructs the full Python `Context` via
`history_until(day)`, and calls the real, untouched
`MLRankerStrategy.on_bar()`. Orders return as plain tuples. ml-ranker runs
unmodified on the C++ engine — no speedup (its cost is sklearn training),
but no second engine left behind.

## Parity and testing — three layers

1. **Catch2 unit tests (C++-only):** portfolio arithmetic, cost model,
   impact walking, each strategy on small synthetic fixtures.
2. **Differential parity tests (the centerpiece):** run both engines on
   identical inputs; assert fills identical (days, assets, quantities,
   prices to the last bit) and equity curves byte-equal. Fast synthetic
   fixtures in CI, plus the full 874-day real-data run per strategy as the
   acceptance test (local, data-dependent).
3. **Golden tests, dual-engine:** parametrize `tests/test_cli_backtest.py`
   over both engines; the pinned numbers validate C++ for free.

### Known risk: polars summation order in strategy math

Engine-level parity (identical orders ⇒ identical fills/equity) is safe:
scalar double arithmetic we control on both sides. Strategy-level parity is
the risk: dip-buyer and momentum compute rolling means/peaks via polars
expressions, and polars may sum floats in a different order than a naive C++
loop (pairwise/SIMD summation). A last-bit-different mean can flip a signal
on a boundary day ⇒ visibly different fills.

Treated empirically: the differential harness points at the exact divergent
day/asset. Resolution options, in order of preference:

1. Reformulate the *Python* strategy math so both sides compute identically
   (updating goldens in the same commit with a hand-derivation, per house
   rules). Ports clarify reference implementations; that is a feature.
2. Mirror polars' exact summation order in C++.

**Bit-for-bit is the acceptance bar; Python strategy math may be adjusted to
make it achievable.**

## CLI wiring and registry

- `pkmn backtest` and `pkmn walkforward` gain `--engine {python,cpp}`.
  **Default stays `python` this plan**; the C++ engine earns default status
  after the parity suite has lived in CI (flip is a one-line follow-up).
- The runs registry records the engine in each run's config hash — a cpp run
  and a python run of the same config are distinguishable forever.
- `scripts/bench_engines.py` produces the measured speedup table for
  `docs/research-findings-2026-07.md`. No speedup claims until measured;
  walk-forward wall-clock gains will be smaller than raw engine gains
  (optuna overhead and data loading do not shrink).

## Error handling

- C++ validates inputs at the boundary (monotonic days, non-negative prices,
  known strategy name) and throws; nanobind surfaces exceptions as ordinary
  Python `ValueError`s. No crashes, no silent misbehavior.
- If `_engine` failed to build/import, `--engine cpp` fails loudly with a
  clear message. No silent fallback to Python.

## Success criteria

1. All existing tests pass on both engines; differential suite green
   bit-for-bit on synthetic fixtures and the full 874-day dataset for all
   five rule strategies and ml-ranker (via bridge).
2. Catch2 suite green in CI alongside the four gates.
3. Measured, documented speedup for `pkmn backtest --engine cpp` and a
   walk-forward run, recorded in the findings doc.
4. `uv sync` remains the only setup command.
