# Triggerable backtests — design (Plan 2: run-triggering from the frontend)

**Goal:** Let a user trigger a backtest from the web frontend — pick any
registered strategy, set starting cash, declare initial card holdings, choose
a time window — run it as a background job, and land on a uniform, clickable
detail screen with an at-a-glance metrics summary.

**Context:** Brainstormed 2026-07-24/25, following the read-only web explorer
(Plan 1, `2026-07-22-web-explorer-design.md`). This is the "Plan 2 job-runner"
that Plan 1 explicitly deferred. Walk-forward triggering stays out of scope;
this is a single-strategy backtest trigger. Local single-user, no auth.

## Strategic direction (agreed 2026-07-25)

**The C++ engine is the destination; the Python engine is legacy on its way
out.** New capability lands in the native engine (`pkmn_engine_core` +
nanobind + `NativeBacktest`), not the Python `Backtest`/`Portfolio`. This
flips the long-standing parity invariant from "keep both engines in lockstep"
to "let them diverge, C++-forward." Actually *retiring* the Python engine
(deleting the dual path, the `--engine` flag, `parity_full.py`, the Python
`Backtest`/`Portfolio`) is real, risky work — the Python engine is currently
the parity *oracle*, and you cannot delete your oracle without first proving
the C++ engine standalone-correct another way. That teardown is a **separate
future plan**, deliberately out of scope here. Plan 2 moves *toward* C++-only
by building new capability there.

## Decomposition (three sequential sub-plans)

Each sub-plan produces working, testable software on its own and is built in
order because each depends on the one before:

- **Plan 2a — Engine: initial-holdings seeding (this design's first plan).**
  The native engine gains the ability to start a backtest from a set of
  pre-existing positions (asset, quantity, cost basis, opened-on date), not
  just cash. C++ only; the Python engine is untouched.
- **Plan 2b — Capability: parameterized single-strategy backtest.** A real
  entry point that builds *any* registered strategy from name + params, runs
  it on the native engine with cash + holdings over a window, writes the same
  `equity.parquet`/`fills.parquet` artifacts the Plan-1 viewer already renders,
  and appends a registry record. Usable from the CLI (`pkmn backtest
  --strategy ...`) with zero web code — the seam Plan 2c is tested behind.
- **Plan 2c — Web: trigger + jobs + detail screen.** `POST /api/backtest` to
  launch, a background-job model with status polling, `GET
  /api/backtest/{run_id}` for results, and the frontend (new-run form, jobs
  view, backtest detail screen with the metrics summary).

This spec details **Plan 2a**. Plans 2b and 2c are brainstormed separately
when 2a lands.

## Plan 2a: engine initial-holdings seeding

### Semantics

A *seeded holding* is `(asset, quantity, avg_cost, opened_on)`:

- `quantity` (> 0): units held at the start.
- `avg_cost` (>= 0): average cost basis per unit, for P&L. Zero is legal (a
  gift/pull with no cash basis).
- `opened_on`: the date the holding was opened. Read by strategies that gate on
  hold duration (dip-buyer take-profit, xs-momentum), so it is a real input,
  not cosmetic.

Seeding installs these positions into the `Portfolio` **before bar one**. It
**does not touch cash or realized P&L** — cash and holdings are independent
inputs. Day-one equity is therefore `cash + Σ quantity·mark`. P&L emerges
exactly as for engine-bought positions: a later strategy sell realizes against
`avg_cost`; a later strategy buy of the same asset averages into the seeded
cost basis and keeps the seed's `opened_on` (mirroring `Portfolio._buy`).

### Where it lives

- `cpp/src/pkmn_engine/types.hpp` — a `SeedPosition` struct.
- `cpp/src/pkmn_engine/portfolio.{hpp,cpp}` — `Portfolio::seed(const
  std::vector<SeedPosition>&)`: validates and installs positions in list order.
- `cpp/src/pkmn_engine/backtest.{hpp,cpp}` — `run_backtest` gains a trailing
  `const std::vector<SeedPosition>& initial_holdings = {}` parameter (default
  keeps every existing call site compiling), seeds the portfolio after
  construction and before the loop.
- `cpp/bindings/module.cpp` — four parallel arrays (`holding_asset`,
  `holding_qty`, `holding_cost`, `holding_opened`) marshaled into a
  `SeedPosition` vector.
- `src/pkmn_quant/engine/native.py` — a `SeededHolding` value dataclass and a
  `NativeBacktest.initial_holdings` field; the adapter validates each holding's
  asset is in the backtest universe (a Python-side check — an out-of-range
  `AssetId` is undefined behavior in the engine, so this is mandatory, not
  cosmetic), sorts holdings by dense asset id for deterministic insertion
  order, and marshals them across.

### Validation split

- **Python (native.py):** asset ∈ universe. Out-of-range ids cannot be allowed
  to reach C++. Raises `ValueError` with a clear message.
- **C++ (Portfolio::seed):** `quantity > 0`, `avg_cost >= 0`, no duplicate
  asset. Throws `std::invalid_argument` (surfaced to Python by nanobind).

Two constraints belong to the *capability* layer (Plan 2b), not the engine,
and are called out here so they are not forgotten: a seeded asset must have a
mark on/before the window start (else `equity()` raises on day one — the
carry-forward mark does not exist yet), and holdings should reference real
tradeable assets. Plan 2a's tests use assets that satisfy this; Plan 2b adds
the user-facing validation.

### Testing (parity is NOT the oracle here)

Because the Python engine will not support seeding, this feature cannot be
parity-tested. Correctness is pinned two ways instead:

- **Catch2 golden + unit tests** (`cpp/tests/test_portfolio.cpp`,
  `cpp/tests/test_backtest_golden.cpp`), hand-derived exact numbers:
  `seed` installs without touching cash/P&L; equity sums seeded positions in
  insertion order; selling a seeded position realizes against its cost basis; a
  strategy buy of a seeded asset averages the basis and keeps `opened_on`;
  validation throws; and an end-to-end `run_backtest` with a no-op strategy
  where the equity curve is exactly `cash + qty·mark` across three carry-forward
  bars.
- **Python integration test** (`tests/test_native_seeding.py`) through
  `NativeBacktest`, on a tiny controlled 1-asset warehouse (prices 10/12/15):
  no-op strategy + seed → exact equity `[cash+40, cash+48, cash+60]`; no
  holdings → flat `[cash, cash, cash]`; a holding naming an asset outside the
  universe → `ValueError`. This exercises the full Python→C++ marshaling
  (asset-id mapping, date→day conversion, array packing) with exact numbers.

The existing no-holdings parity tests (`tests/test_native_parity.py`,
`scripts/parity_full.py`) keep passing untouched — parity still guards
everything the two engines share; seeding is simply a C++-only capability they
no longer share.

### Rebuild discipline

Any edit under `cpp/` requires `uv sync --reinstall-package pkmn-quant` before
the Python integration test sees new native code (a stale `.so` silently runs
the old engine). Never enable fast-math / fp-contract in the build.

## Out of scope (this plan)

- Plan 2b (parameterized `run_backtest` capability, widened `pkmn backtest
  --strategy`, artifact + registry writes) and Plan 2c (web trigger, jobs,
  detail screen).
- Any change to the Python `Backtest`/`Portfolio` engine.
- Retiring the Python engine (a later plan; it is still the parity oracle).
- Capability-layer holdings validation (marks-present-at-start, real-asset
  checks) — belongs to Plan 2b.
