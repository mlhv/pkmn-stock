# Plan 11 design: parallel walk-forward search

Date: 2026-07-17
Status: approved design, pre-implementation

## Goal

Make `pkmn walkforward` — the project's most expensive operation (~300
sequential backtests on the standard config) — use all CPU cores, with
results **bit-identical to today's serial runs**. Built on the Plan 10 C++
engine, whose Python-free core makes releasing the GIL possible. Two levers,
deliberately together:

1. **Fold-level parallelism**: the 11 folds' optuna studies are mutually
   independent (each has its own freshly-seeded TPE sampler), so running
   them concurrently preserves every study's exact trial sequence — the
   same numbers, computed faster.
2. **Data-prep hoist**: today every trial re-loads the warehouse and
   re-flattens arrays (~77 redundant loads per fold). Load once per
   walkforward, prepare arrays once per fold window.

Out of scope (later plans, if ever): trial-level parallelism within a fold
(breaks reproducibility — the sampler's trajectory would depend on worker
count), process-based parallelism, parallel `pkmn backtest`.

## Decisions locked during brainstorming

- **Parallel axis**: across folds only. Within-fold trial parallelism was
  explicitly rejected: TPE is adaptive (trial N's suggestion uses results
  1..N-1), so concurrent trials change the search trajectory and make
  results depend on worker count. Fold-parallel results are provably
  identical to serial.
- **Mechanism**: thread pool + GIL release in the nanobind binding
  (Approach A). Process pools were rejected: they defeat the data-prep
  hoist (pickling/reload per fold), pay spawn costs, and ignore the
  GIL-free capability Plan 10 built.
- **Default engine flips to cpp** on BOTH `backtest` and `walkforward`
  (`--engine python` remains; the Python engine remains the reference
  implementation and keeps its CI coverage via the dual-engine goldens).
  The Plan 10 bar ("parity suite lived in CI") is met: 11 reviewed tasks,
  full-874-day acceptance, dual-engine goldens green.
- **Parallel by default**: `--workers 0` (auto) = `min(n_folds,
  cpu_count)`; `--workers 1` = today's serial loop, bypassing the executor
  entirely.

## Architecture: the fold worker

`run_walkforward` becomes: load shared data once → submit one fold worker
per fold to a `concurrent.futures.ThreadPoolExecutor` → collect
`FoldResult`s in fold order → stitch as today.

A fold worker is the entire per-fold pipeline, logic unchanged: seeded
optuna study (sequential trials), IS re-run with best params, OOS run,
return `FoldResult`. Workers share nothing mutable: each owns its optuna
study object, its `PreparedMarket` windows, and per-backtest C++ engine
instances. Results are collected by fold index, not completion order, so
stitching is order-identical. `--workers 1` runs the plain loop — the
serial reference path stays trivially intact.

## Data-prep hoist (two levels, cpp path only)

1. **One warehouse load per walkforward.** `run_walkforward` loads the
   full price frame + products up front. `MarketData` gains
   `from_frame(frame, start, end, warmup_days)`; `from_warehouse` becomes
   a thin load-then-delegate wrapper (same filters — parity-inert
   refactor). Fold workers slice their windows from the shared immutable
   polars frame instead of reading parquet ~300 times.
2. **One `PreparedMarket` per fold window.** A frozen object holding what
   the adapter currently rebuilds per run: flattened numpy arrays, asset
   interning table, mark events, product table. Each fold worker builds
   one for its IS window (reused by all trials + the IS re-run) and one
   for its OOS window. `NativeBacktest` gains an optional `prepared=`
   argument that skips load/flatten. Arrays are read-only — safe to share
   across a fold's sequential trials by construction.

Deliberate asymmetry: the hoist applies to the **cpp path only**. The
Python engine's `MarketData` carries a mutable marks cursor; sharing an
instance across runs would be a thread-safety trap for zero benefit. The
Python engine stays byte-for-byte untouched in its role as reference.

## The GIL-release contract

In `module.cpp`'s `run_backtest`, **native-strategy path only** (callback
is None): after all inputs are converted to C++ vectors, wrap the engine
call in `nb::gil_scoped_release`; re-acquire before building outputs.

- The **bridge path never releases** — `CallbackStrategy` re-enters Python
  every bar. Consequence (documented, not engineered around):
  `--workers N` with a bridged strategy (ml-ranker) is correct but roughly
  serial; sklearn's internal GIL releases give some overlap, no claims
  made.
- Invariant: **no Python object is touched inside the released region.**
  The released region is exactly one C++ function call on C++-only data
  (inputs were copied to std::vectors before it).
- Core thread-safety proven Python-free in Catch2: two `std::thread`s
  running `run_backtest` on separate `MarketView`s produce results
  identical to sequential runs.

## CLI, defaults, registry

- `--engine` default `python` → `cpp` on both commands. Pinned golden
  numbers do not move (bit-for-bit parity makes the flip invisible in
  results); goldens stay parametrized over both engines forever.
- `walkforward --workers N` (default 0 = auto; 1 = serial; validated ≥ 0
  with a clean CLI error).
- Registry: `workers` recorded on the run record **outside the config
  hash** — identical configs at different worker counts produce identical
  results, so they must hash identically; workers is operational metadata
  like duration. `engine` stays inside the hash as in Plan 10.

## Error handling

- A fold worker exception cancels not-yet-started folds and re-raises the
  first failure from `run_walkforward`; no partial `WalkForwardResult`
  ever escapes.
- Ctrl-C shuts the executor down without hung threads.

## Testing

1. **Serial ≡ parallel, bit-for-bit** (the centerpiece): synthetic-fixture
   walkforward at `--workers 1` vs `--workers 4` — stitched curve,
   per-fold params, and summaries exactly `==`. Run for a native strategy
   AND a bridged one.
2. **Determinism under repetition**: the parallel run three times,
   all identical (catches races a single comparison can miss).
3. **Hoist equivalence**: `NativeBacktest` with `PreparedMarket` vs
   without — exact `==`.
4. **Catch2 concurrency smoke**: core thread-safety with no Python.
5. Existing suite green through the default flips (dual-engine goldens,
   differential tests, walkforward smoke).

## Benchmark and findings

Extend the benchmark to a real-data walkforward: python serial baseline vs
cpp serial vs cpp `--workers auto`, wall-clock table into
`docs/research-findings-2026-07.md`. Measured, never projected. Honest
framing: fold parallelism and the hoist compound (the hoist alone removes
~300 redundant loads), but the ceiling is `min(n_folds, cores)` and
bridged strategies gain little.

## Success criteria

1. Serial/parallel equivalence suite green (bit-for-bit, native + bridge).
2. All existing tests green with cpp as default engine.
3. Measured walkforward wall-clock table in the findings doc.
4. `uv sync` remains the only setup command; `--workers 1 --engine python`
   reproduces pre-Plan-11 behavior exactly.
