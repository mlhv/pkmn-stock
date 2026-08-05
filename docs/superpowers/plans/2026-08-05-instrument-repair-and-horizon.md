# Instrument Repair and Horizon-Matched Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the benchmark actually invested, price buys at a number something is offered at, and measure every strategy trade against holding sealed over that trade's own window.

**Architecture:** Two engine repairs (each landing in Python *and* C++ in the same task, because `tests/test_native_parity.py` runs in the standard gate and fails the moment the engines disagree), then a Python-only evaluation module that reads run artifacts after the fact. Design spec: `docs/superpowers/specs/2026-08-05-instrument-repair-and-horizon-design.md`.

**Tech Stack:** Python 3.12, polars, DuckDB, C++20 + nanobind (scikit-build-core), pytest, Catch2.

## Global Constraints

- All four gates pass before every commit: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`. Run `uv sync --group api` first if fastapi/pydantic import errors appear.
- Baseline before this plan: **528 passed** with the dashboard group installed (523 passed + 1 skipped without it), mypy clean over **55** source files. State which you observed.
- **After editing anything under `cpp/`, run `uv sync --reinstall-package pkmn-quant`.** `uv sync` alone will not rebuild the extension, and a stale `.so` silently runs the old C++ code and produces a passing-but-meaningless parity result.
- **Never enable fast-math or fp-contract** (`-ffast-math`, `-ffp-contract=fast`, MSVC `/fp:fast`) anywhere in the build. Bit-for-bit parity depends on IEEE-754-exact, non-reassociated floating point; either flag passes the C++ unit tests and silently breaks parity on real data.
- **Never delete anything under `data/`.** `data/warehouse-old/` and `data/warehouse-full-failed-197groups/` are retained deliberately.
- Never use pip; `uv` manages everything.
- `tests/test_cli_backtest.py` goldens WILL move in **Task 1 and Task 2**, and only there. Buy-and-hold is that file's default strategy, so the deficit sweep changes its quantities; the buy floor then changes its costs. Update them in the task that moves them, with a hand-derivation. If they move in Task 3 or 4, STOP and report — those tasks touch no engine code.
- `tests/test_native_parity.py` runs in the standard gate, so **a behaviour change must land in Python and C++ in the same commit**. There is no valid Python-only intermediate state.
- Marking is unchanged throughout: positions continue to mark at `market`. This plan changes the price a fill executes at, never the price a held position is valued at.

## Reference: existing interfaces this plan builds on

- `Context` (`engine/strategy.py:21-33`): `today`, `history` (price rows, `date <= today` only), `products`, `positions`, `cash`, `marks`.
- `BuyAndHold` (`strategies/buy_and_hold.py`): `__init__(kind="sealed")`, sets `self.name = f"buy-and-hold-{kind}"`, `_entered` flag reset by `reset()`.
- `CostModel` (`engine/costs.py`): `buy_impact(market, mid, qty, used=0)`, `sell_impact(market, low, qty, used=0)`, `_impact(market, upper, lower, qty, used)` returning `spread * qty * (2*used + qty) / (2*q_cap)`, `max_daily_qty(market)`, `as_dict()`.
- `Executor._fill_buy` (`engine/execution.py:74-104`): `Fill.price` is **always** the observable market print; all costs live in `fees` and `impact` (stated at `execution.py:7`). This plan preserves that invariant.
- `Quote` (`engine/quotes.py`): frozen dataclass, `mid: float | None`, `low: float | None`. Missing fields mean zero impact — never invent them.
- C++ mirrors: `cpp/src/pkmn_engine/costs.hpp` (`buy_impact`, `sell_impact`, `impact_`, `max_daily_qty`), `cpp/src/pkmn_engine/strategies/buy_and_hold.{hpp,cpp}`, `cpp/src/pkmn_engine/market.hpp` (`MarketView`: `mid(a)`, `low(a)`, `last_price_at_or_before`, `peak_until`, `max_in_window`), `cpp/src/pkmn_engine/strategy.hpp` (`Context`).
- `research/backtest_run.py`: `buy-and-hold` is special-cased at lines 57-59 (`raise ValueError("buy-and-hold has no tunable parameters")`), 87-88, 237, 301-305. It is deliberately **not** a `RegistryEntry`.

## Deviation from the spec, and why

The spec says `n_assets` becomes "CLI-settable, walk-forward-searchable, and declarative". **This plan does not make it walk-forward-searchable**, and deliberately keeps `buy-and-hold` out of `research/registry.py`.

Tuning the benchmark's own parameter to maximise the benchmark's own return would turn the passive bar into an optimised strategy, and every "strategy X beats/loses to the benchmark" statement would then be comparing against a self-serving baseline. The bar must be fixed by construction, not fitted. `n_assets` is therefore declarative and CLI-settable, with no optuna space.

---

### Task 1: Liquidity screen and deficit sweep

**Files:**
- Create: `src/pkmn_quant/research/screen.py`
- Create: `tests/research/test_screen.py`
- Modify: `src/pkmn_quant/strategies/buy_and_hold.py`
- Modify: `tests/strategies/test_buy_and_hold.py`
- Modify: `tests/test_cli_backtest.py` (goldens move — see Step 14)
- Modify: `cpp/src/pkmn_engine/strategies/buy_and_hold.hpp`
- Modify: `cpp/src/pkmn_engine/strategies/buy_and_hold.cpp`
- Modify: `cpp/src/pkmn_engine/strategies/factory.cpp`
- Modify: `cpp/src/pkmn_engine/market.hpp`
- Modify: `cpp/src/pkmn_engine/market.cpp`
- Modify: `cpp/tests/test_strategies.cpp`
- Modify: `src/pkmn_quant/research/backtest_run.py`
- Modify: `src/pkmn_quant/engine/native.py`

**Interfaces:**
- Produces: `rank_assets(history: pl.DataFrame, today: date) -> pl.DataFrame` in `research/screen.py` — one row per `(product_id, sub_type)` with columns `product_id`, `sub_type`, `n_prints`, `spread`, sorted best-first.
- Produces: `BuyAndHold(kind: str = "sealed", n_assets: int = 100)`.
- Produces: C++ `BuyAndHold(std::int8_t kind_code, std::int64_t n_assets)` and `MarketView::print_count_at_or_before(AssetId a, Day d) const -> std::int64_t`.

- [ ] **Step 1: Write the failing screen tests**

Create `tests/research/test_screen.py`:

```python
"""The liquidity screen: rank assets by print density, then quote sanity."""

from datetime import date

import polars as pl

from pkmn_quant.research.screen import rank_assets

D = date(2025, 6, 3)


def _rows(rows: list[tuple[int, str, date, float, float, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {"product_id": p, "sub_type": s, "date": d, "low": lo, "mid": md, "market": mk}
            for p, s, d, lo, md, mk in rows
        ]
    )


def test_denser_asset_ranks_first() -> None:
    """Print density is the primary key: three prints beats one."""
    df = _rows(
        [
            (1, "Normal", date(2025, 6, 1), 10.0, 12.0, 11.0),
            (1, "Normal", date(2025, 6, 2), 10.0, 12.0, 11.0),
            (1, "Normal", D, 10.0, 12.0, 11.0),
            (2, "Normal", D, 10.0, 12.0, 11.0),
        ]
    )
    out = rank_assets(df, today=D)
    assert out["product_id"].to_list() == [1, 2]
    assert out["n_prints"].to_list() == [3, 1]


def test_tighter_spread_breaks_a_density_tie() -> None:
    """Equal density -> the tighter relative spread wins. This is the key that
    does the real work at the first bar, where density saturates."""
    df = _rows(
        [
            (1, "Normal", D, 5.0, 10.0, 7.0),  # spread 0.5
            (2, "Normal", D, 9.0, 10.0, 9.5),  # spread 0.1
        ]
    )
    assert rank_assets(df, today=D)["product_id"].to_list() == [2, 1]


def test_missing_mid_sorts_last() -> None:
    """A product with no mid has an undefined spread; it must sort last, not
    first, and must not be dropped."""
    df = _rows([(1, "Normal", D, 9.0, 10.0, 9.5)])
    df = pl.concat(
        [
            df,
            pl.DataFrame(
                [{"product_id": 2, "sub_type": "Normal", "date": D,
                  "low": 9.0, "mid": None, "market": 9.5}],
                schema=df.schema,
            ),
        ]
    )
    out = rank_assets(df, today=D)
    assert out["product_id"].to_list() == [1, 2]


def test_product_id_is_the_final_tie_break() -> None:
    """Identical density and spread must still order deterministically."""
    df = _rows([(7, "Normal", D, 9.0, 10.0, 9.5), (3, "Normal", D, 9.0, 10.0, 9.5)])
    assert rank_assets(df, today=D)["product_id"].to_list() == [3, 7]


def test_sub_types_rank_independently() -> None:
    """Marks are keyed by (product_id, sub_type), so the screen must be too."""
    df = _rows(
        [
            (1, "Normal", date(2025, 6, 1), 9.0, 10.0, 9.5),
            (1, "Normal", D, 9.0, 10.0, 9.5),
            (1, "Holofoil", D, 9.0, 10.0, 9.5),
        ]
    )
    out = rank_assets(df, today=D)
    assert out.select("product_id", "sub_type").rows() == [(1, "Normal"), (1, "Holofoil")]


def test_history_after_today_is_ignored() -> None:
    """rank_assets must never count a print the strategy cannot have seen."""
    df = _rows(
        [
            (1, "Normal", D, 9.0, 10.0, 9.5),
            (1, "Normal", date(2025, 6, 10), 9.0, 10.0, 9.5),
        ]
    )
    assert rank_assets(df, today=D)["n_prints"].to_list() == [1]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/research/test_screen.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pkmn_quant.research.screen'`.

- [ ] **Step 3: Implement the screen**

Create `src/pkmn_quant/research/screen.py`:

```python
"""Rank assets by tradeability: print density first, quote sanity second.

Density is the honest primary key, but at the first bar of a backtest the
available history is only whatever precedes the start date (22 days for a
2024-03-01 start), so density saturates and cannot discriminate. Relative
spread carries the load there, and doubles as a quote-sanity filter:
products whose listings are internally consistent sort ahead of those whose
are not.

Leakage-bounded by construction — callers pass `today` and rows after it are
dropped, so this can be called from inside a strategy's on_bar.
"""

from __future__ import annotations

from datetime import date

import polars as pl


def rank_assets(history: pl.DataFrame, today: date) -> pl.DataFrame:
    """One row per (product_id, sub_type), best-first.

    Sort key: n_prints descending, then relative spread (mid-low)/mid
    ascending, then product_id, then sub_type — the last two purely so the
    order is total and reproducible, which the C++ port depends on.

    A row whose mid is null or <= 0 has an undefined spread and is given
    infinity so it sorts last without being dropped.
    """
    seen = history.filter(pl.col("date") <= today)
    density = seen.group_by("product_id", "sub_type").agg(
        pl.col("date").n_unique().alias("n_prints")
    )
    latest = (
        seen.sort("date")
        .group_by("product_id", "sub_type")
        .agg(pl.col("low").last().alias("low"), pl.col("mid").last().alias("mid"))
    )
    return (
        density.join(latest, on=["product_id", "sub_type"], how="left")
        .with_columns(
            pl.when(pl.col("mid").is_null() | (pl.col("mid") <= 0))
            .then(float("inf"))
            .otherwise((pl.col("mid") - pl.col("low")) / pl.col("mid"))
            .alias("spread")
        )
        .select("product_id", "sub_type", "n_prints", "spread")
        .sort(
            ["n_prints", "spread", "product_id", "sub_type"],
            descending=[True, False, False, False],
            nulls_last=True,
        )
    )
```

- [ ] **Step 4: Run the screen tests**

Run: `uv run pytest tests/research/test_screen.py -v`
Expected: PASS — all six.

- [ ] **Step 5: Write the failing benchmark-allocation tests**

Read `tests/strategies/test_buy_and_hold.py` first. Its `make_ctx` helper passes `history=pl.DataFrame()` — an **empty** frame — and its existing assertions pin exact quantities from the old equal-dollar rule. Two consequences you must handle rather than work around:

1. **The screen must tolerate an asset with no history.** An asset present in `ctx.marks` but absent from `ctx.history` gets `n_prints = 0` and `spread = inf` and sorts last; it is never dropped. With an empty history every asset ties there and the order falls through to `product_id`, so these tests keep exercising the allocation rather than the ranking.
2. **The existing quantity assertions will move**, because the sweep now spends the remainder. Update each one with a hand-derivation in the test docstring showing pass 1 and pass 2, exactly as the new tests below do. Do not weaken an assertion to a range or a `>=` to make it pass.

Add to `tests/strategies/test_buy_and_hold.py`:

```python
def test_buy_and_hold_screens_to_n_assets() -> None:
    """Only the top n_assets by screen order are bought at all."""
    from pkmn_quant.strategies.buy_and_hold import BuyAndHold

    strat = BuyAndHold(kind="sealed", n_assets=2)
    ctx = _bh_ctx(n_products=5, cash=1000.0, price=10.0)
    orders = strat.on_bar(ctx)
    assert len(orders) == 2


def test_buy_and_hold_deficit_sweep_deploys_the_remainder() -> None:
    """Equal-dollar alone leaves cash on the table when price does not divide
    the per-asset budget; the sweep must spend it down.

    Hand-derivation: cash 1000, n_assets 2, both priced 30.
    Pass 1: target 500 -> floor(500/30) = 16 units each = 960, cash 40 left.
    Pass 2: both held 480 vs target 500, deficit 20 each; buy 1 unit of the
    first in screen order (strict >, so earliest wins the tie) -> cash 10,
    nothing else affordable. Totals: 17 and 16 units, 990 spent.
    """
    from pkmn_quant.strategies.buy_and_hold import BuyAndHold

    strat = BuyAndHold(kind="sealed", n_assets=2)
    ctx = _bh_ctx(n_products=2, cash=1000.0, price=30.0)
    qty = sorted(o.quantity for o in strat.on_bar(ctx))
    assert qty == [16, 17]


def test_buy_and_hold_sweep_is_deterministic_under_ties() -> None:
    """Two identical assets must always produce the same split, never an
    order-dependent one — bit-for-bit C++ parity depends on it."""
    from pkmn_quant.strategies.buy_and_hold import BuyAndHold

    runs = []
    for _ in range(3):
        strat = BuyAndHold(kind="sealed", n_assets=2)
        ctx = _bh_ctx(n_products=2, cash=1000.0, price=30.0)
        runs.append([(o.asset.product_id, o.quantity) for o in strat.on_bar(ctx)])
    assert runs[0] == runs[1] == runs[2]
```

Write `_bh_ctx(n_products, cash, price)` in that same file, built on top of the existing `make_ctx` rather than as a second pattern: it constructs `n_products` sealed `Asset`s all marked at `price`, a matching `products` frame with `kind="sealed"`, and a `history` with one print per product on `D1`. Reuse `make_ctx`'s `Context(...)` construction so there is one way to build a context in this file.

- [ ] **Step 6: Run to verify failure**

Run: `uv run pytest tests/strategies/test_buy_and_hold.py -v`
Expected: FAIL — `BuyAndHold.__init__() got an unexpected keyword argument 'n_assets'`.

- [ ] **Step 7: Implement the screen and sweep in the Python strategy**

Rewrite `on_bar` in `src/pkmn_quant/strategies/buy_and_hold.py`. Keep the module docstring and the `_entered` reset behaviour:

```python
    def __init__(self, kind: str = "sealed", n_assets: int = 100) -> None:
        self.kind = kind
        self.n_assets = n_assets
        self.name = f"buy-and-hold-{kind}"
        self._entered = False

    def on_bar(self, ctx: Context) -> list[Order]:
        if self._entered:
            return []
        self._entered = True

        wanted_ids = set(ctx.products.filter(pl.col("kind") == self.kind)["product_id"].to_list())
        # Rank every eligible mark. An asset with no history is not dropped —
        # it ranks last (0 prints, infinite spread) and stays buyable.
        rank: dict[tuple[int, str], tuple[int, float]] = {}
        if ctx.history.height:
            for pid, sub_type, n_prints, spread in rank_assets(
                ctx.history, ctx.today
            ).iter_rows():
                rank[(pid, sub_type)] = (n_prints, spread)
        candidates = [
            (asset, price)
            for asset, price in ctx.marks.items()
            if asset.product_id in wanted_ids and price > 0
        ]
        candidates.sort(
            key=lambda ap: (
                -rank.get((ap[0].product_id, ap[0].sub_type), (0, float("inf")))[0],
                rank.get((ap[0].product_id, ap[0].sub_type), (0, float("inf")))[1],
                ap[0].product_id,
                ap[0].sub_type,
            )
        )
        universe = candidates[: self.n_assets]
        if not universe:
            return []

        target = ctx.cash / len(universe)
        qty = [math.floor(target / price) for _, price in universe]
        cash = ctx.cash - sum(q * price for q, (_, price) in zip(qty, universe, strict=True))

        # Deficit sweep: buy one unit at a time of whichever screened asset is
        # furthest below its target dollar weight. Strict > means ties go to
        # the earliest in screen order, which is itself product_id-tie-broken,
        # so the result is total and reproducible in both engines.
        while True:
            best = -1
            best_deficit = 0.0
            for i, (_, price) in enumerate(universe):
                if price > cash:
                    continue
                deficit = target - qty[i] * price
                if deficit > best_deficit:
                    best_deficit = deficit
                    best = i
            if best < 0:
                break
            qty[best] += 1
            cash -= universe[best][1]

        return [
            Order(asset=asset, quantity=q)
            for q, (asset, _) in zip(qty, universe, strict=True)
            if q > 0
        ]
```

Add `from pkmn_quant.engine.strategy import Asset` (or wherever `Asset` is defined — check `engine/data.py` and follow the existing import in this file's neighbours) and `from pkmn_quant.research.screen import rank_assets`.

Note the deviation from the spec's wording: the spec says sweep ties break "by `product_id` ascending". Screen order is already product_id-tie-broken, so breaking by screen order is equivalent, simpler, and equally deterministic.

- [ ] **Step 8: Run the Python strategy tests**

Run: `uv run pytest tests/strategies/test_buy_and_hold.py tests/research/test_screen.py -v`
Expected: PASS, including the pre-existing tests you re-derived in Step 5.

- [ ] **Step 9: Add the C++ print-count query**

In `cpp/src/pkmn_engine/market.hpp`, declare beside the other history queries:

```cpp
    // Count of distinct printed days for `a` at or before `d`. Mirrors
    // research/screen.py's n_prints. Anti-look-ahead: callers pass d <= today.
    std::int64_t print_count_at_or_before(AssetId a, Day d) const;
```

Implement it in `cpp/src/pkmn_engine/market.cpp` using the same per-asset range and binary-search shape the existing `last_price_at_or_before` uses — read that function first and mirror it rather than inventing a second access pattern. Each `(asset, day)` appears at most once in the per-asset range, so the count is the distance from the range start to the upper bound of `d`.

- [ ] **Step 10: Port the screen and sweep to C++**

Change the constructor in `cpp/src/pkmn_engine/strategies/buy_and_hold.hpp` to `BuyAndHold(std::int8_t kind_code, std::int64_t n_assets)` with an `n_assets_` member, and rewrite `on_bar` in `buy_and_hold.cpp` to mirror the Python exactly:

1. Build candidates from `ctx.marks.entries()` filtered by `kind_`, keeping only `value > 0`.
2. Compute, for each, `n_prints = ctx.market.print_count_at_or_before(id, ctx.today)` and `spread` from `ctx.market.mid(id)` / `ctx.market.low(id)`, using `std::numeric_limits<double>::infinity()` when mid is missing or `<= 0`.
3. `std::stable_sort` by `(n_prints desc, spread asc, AssetId asc)`. **`AssetId` alone replaces the Python key's trailing `product_id, sub_type`**: `cpp/src/pkmn_engine/types.hpp:11-13` states ids are assigned in `(product_id, sub_type)` order, so comparing `AssetId` *is* comparing that pair. Use it rather than reaching for `product_id` plus a string compare — the integer compare is provably equivalent and cannot diverge on locale or encoding. `ProductTable` carries no `sub_type` column, so this is also the only way to express the tie-break on this side.
4. Truncate to `n_assets_`.
5. Pass 1 `std::floor(target / price)`, then the identical deficit sweep loop with strict `>`.

The arithmetic must be written in the same order as the Python — `target - qty[i] * price`, not an algebraically equal rearrangement — or the doubles diverge and parity fails.

Update `cpp/src/pkmn_engine/strategies/factory.cpp` to read `n_assets` from the params map with default `100`, following how the other strategies read their params there.

- [ ] **Step 11: Pass n_assets through the Python-side adapter**

In `src/pkmn_quant/engine/native.py`, ensure the `NativeStrategySpec` params for `buy-and-hold` carry `n_assets`. In `src/pkmn_quant/research/backtest_run.py`, replace the `buy-and-hold has no tunable parameters` rejection (lines 57-59) with acceptance of exactly one parameter, `n_assets` (int, default 100), and pass it through at lines 87-88 and into the config recorded at line 237. Do **not** add a `RegistryEntry` for buy-and-hold — see "Deviation from the spec" above.

- [ ] **Step 12: Add the C++ unit test**

Add to `cpp/tests/test_strategies.cpp` a case mirroring `test_buy_and_hold_deficit_sweep_deploys_the_remainder`: two sealed assets priced 30.0, cash 1000, `n_assets = 2`, expect quantities 17 and 16. Follow the fixture style already in that file.

- [ ] **Step 13: Rebuild and run both suites**

```bash
uv sync --reinstall-package pkmn-quant
cmake -S cpp -B cpp/build -DPKMN_BUILD_TESTS=ON && cmake --build cpp/build -j && ctest --test-dir cpp/build
uv run pytest tests/test_native_parity.py -v
```
Expected: Catch2 all pass; parity passes. **If parity fails, the two implementations disagree — fix that before proceeding, do not adjust the parity test.**

- [ ] **Step 14: Run the four gates**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`

Expected: `tests/test_cli_backtest.py` goldens **will fail**, because buy-and-hold is that file's default strategy and the deficit sweep now spends the remainder. That is this task's intended effect, not a regression. Update them in this commit, adding a hand-derivation to the test docstring: state the fixture's cash and prices, the pass-1 quantities, the cash left over, and which asset the sweep buys with it. The fixture's universe is smaller than `n_assets = 100`, so the screen itself is a no-op there and the *only* legitimate source of change is the sweep — if a golden moves in a way the sweep cannot explain, STOP and report.

- [ ] **Step 15: Commit**

```bash
git add src/pkmn_quant/research/screen.py tests/research/test_screen.py \
        src/pkmn_quant/strategies/buy_and_hold.py \
        tests/strategies/test_buy_and_hold.py tests/test_cli_backtest.py \
        src/pkmn_quant/research/backtest_run.py src/pkmn_quant/engine/native.py \
        cpp/src/pkmn_engine/strategies/buy_and_hold.hpp \
        cpp/src/pkmn_engine/strategies/buy_and_hold.cpp \
        cpp/src/pkmn_engine/strategies/factory.cpp \
        cpp/src/pkmn_engine/market.hpp cpp/src/pkmn_engine/market.cpp \
        cpp/tests/test_strategies.cpp
git commit -m "feat(benchmark): liquidity screen + deficit sweep so buy-and-hold is actually invested"
```

---

### Task 2: Executable price floor for buys

**Files:**
- Modify: `src/pkmn_quant/engine/costs.py`
- Modify: `src/pkmn_quant/engine/execution.py`
- Modify: `tests/engine/test_costs.py`
- Modify: `tests/engine/test_execution.py`
- Modify: `tests/test_cli_backtest.py`
- Modify: `cpp/src/pkmn_engine/costs.hpp`
- Modify: `cpp/src/pkmn_engine/execution.cpp`
- Modify: `cpp/tests/test_costs.cpp`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `CostModel.buy_impact(market: float, mid: float | None, low: float | None, qty: int, used: int = 0) -> float` — note the new third positional parameter `low`. C++ mirror `buy_impact(double market, double mid, double low, std::int64_t qty, std::int64_t used)`.

**Why the floor goes into `impact` rather than `Fill.price`:** `execution.py:7` states that `Fill.price` is always the observable market print and that all costs live in `fees` and `impact`. Routing the floor premium through `impact` preserves that invariant, keeps every downstream artifact reader working unchanged, and makes the premium visible as a cost rather than hidden in a price.

- [ ] **Step 1: Write the failing cost tests**

Add to `tests/engine/test_costs.py`:

```python
def test_buy_impact_charges_the_premium_to_reach_the_lowest_listing() -> None:
    """market is sales-derived, low is the cheapest current listing. You
    cannot lift an offer that does not exist, so a buy pays at least `low`
    and the shortfall is a cost.

    Hand-derivation: market 10, low 25, mid 25, qty 2, used 0, q_cap 8.
    base = max(10, 25) = 25. Floor premium = (25 - 10) * 2 = 30.
    Walk spread = mid - base = 0, so the walk term is 0. Total 30.
    """
    cm = CostModel(impact_enabled=True)
    assert cm.buy_impact(10.0, 25.0, 25.0, qty=2) == 30.0


def test_buy_impact_walks_from_the_floor_not_from_market() -> None:
    """With a floor in play the walk starts at the floor, not at market.

    Hand-derivation: market 10, low 20, mid 40, qty 2, used 0, q_cap 8.
    base = 20. Premium = (20 - 10) * 2 = 20.
    Walk spread = 40 - 20 = 20 -> 20 * 2 * (0 + 2) / (2 * 8) = 5.0.
    Total 25.0.
    """
    cm = CostModel(impact_enabled=True)
    assert cm.buy_impact(10.0, 40.0, 20.0, qty=2) == 25.0


def test_buy_impact_unchanged_when_market_is_at_or_above_low() -> None:
    """The ordinary case is untouched: no premium, walk from market to mid."""
    cm = CostModel(impact_enabled=True)
    assert cm.buy_impact(30.0, 40.0, 20.0, qty=2) == cm.buy_impact(30.0, 40.0, None, qty=2)


def test_buy_impact_with_no_low_falls_back_to_market() -> None:
    """A missing low is not an excuse to invent one."""
    cm = CostModel(impact_enabled=True)
    assert cm.buy_impact(10.0, 40.0, None, qty=1) > 0.0


def test_buy_impact_is_zero_when_disabled_even_with_a_floor() -> None:
    cm = CostModel(impact_enabled=False)
    assert cm.buy_impact(10.0, 40.0, 1000.0, qty=5) == 0.0
```

Note the third test asserts a *behavioural equality*, so it keeps passing only while the no-floor path is genuinely untouched.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/engine/test_costs.py -k buy_impact -v`
Expected: FAIL — `buy_impact() takes 4 positional arguments but 5 were given`.

- [ ] **Step 3: Implement the floor in the Python cost model**

In `src/pkmn_quant/engine/costs.py`, replace `buy_impact`:

```python
    def buy_impact(
        self, market: float, mid: float | None, low: float | None, qty: int, used: int = 0
    ) -> float:
        """Total $ impact for buying qty units after `used` already filled today.

        tcgcsv's `market` is derived from recent completed SALES while `low`
        is the cheapest current LISTING, so `market < low` is ordinary rather
        than corrupt — it means recent sales cleared below today's cheapest
        offer. A buy cannot execute below that offer, so the fill starts at
        max(market, low) and the shortfall is charged as impact; the walk to
        `mid` then starts from there. Zero when disabled, when mid is
        missing, or when the quote is crossed — never negative.
        """
        if not self.impact_enabled or qty <= 0:
            return 0.0
        base = market if low is None else max(market, low)
        premium = (base - market) * qty
        return premium + self._impact(market, mid, base, qty, used)
```

`_impact` already returns 0.0 when `upper` is None or the spread is non-positive, so `mid <= base` needs no special case. Do not change `sell_impact` or `_impact`.

- [ ] **Step 4: Update the Python executor**

In `src/pkmn_quant/engine/execution.py` `_fill_buy`, take `low` from the quote and use the floor for both the impact call and the affordability estimate:

```python
        mid = quote.mid if quote is not None else None
        low = quote.low if quote is not None else None
        base = market if low is None else max(market, low)
        qty = min(order.quantity, cap_left)
        affordable = math.floor((portfolio.cash - self.cost_model.shipping_per_line) / base)
        qty = min(qty, max(affordable, 0))
        impact = self.cost_model.buy_impact(market, mid, low, qty, used)
        cost = qty * market + self.cost_model.shipping_per_line + impact
        while qty > 0 and cost > portfolio.cash:
            qty -= 1
            impact = self.cost_model.buy_impact(market, mid, low, qty, used)
            cost = qty * market + self.cost_model.shipping_per_line + impact
```

Using `base` for `affordable` only changes how many iterations the decrement loop runs, never its result — but without it the loop can run hundreds of times when `low >> market`, which is exactly the case this task exists for. `Fill.price` stays `market`.

- [ ] **Step 5: Run the Python cost and engine tests**

Run: `uv run pytest tests/engine/test_costs.py tests/engine/test_execution.py -v`
Expected: PASS.

- [ ] **Step 6: Port the floor to C++**

In `cpp/src/pkmn_engine/costs.hpp`, change `buy_impact` to take `low` and mirror the Python exactly:

```cpp
    double buy_impact(double market, double mid, double low, std::int64_t qty,
                      std::int64_t used) const {
        if (!impact_enabled || qty <= 0) return 0.0;
        double base = std::isnan(low) ? market : std::max(market, low);
        double premium = (base - market) * static_cast<double>(qty);
        return premium + impact_(market, mid, base, qty, used);
    }
```

Match however this codebase already represents a missing quote field in C++ — read the existing `sell_impact` and `impact_` first and use the same sentinel (NaN check above is a placeholder for whatever that is; if the code uses a separate `has_low` flag or a sentinel value, use that instead). Update the `execution.cpp` call site with the same `base`-for-affordability change.

- [ ] **Step 7: Add the C++ cost test**

Add to `cpp/tests/test_costs.cpp` the two hand-derived cases from Step 1: `(market 10, mid 25, low 25, qty 2) == 30.0` and `(market 10, mid 40, low 20, qty 2) == 25.0`.

- [ ] **Step 8: Rebuild and check parity**

```bash
uv sync --reinstall-package pkmn-quant
cmake --build cpp/build -j && ctest --test-dir cpp/build
uv run pytest tests/test_native_parity.py -v
```
Expected: all PASS.

- [ ] **Step 9: Update the goldens with a hand-derivation**

`tests/test_cli_backtest.py` pins exact engine numbers and this change moves them wherever the fixture has `low > market` on a fill day. Run the test, read the actual values, and update them **in the same commit**, adding to the test's docstring a derivation of at least one changed number from the fixture's quotes — following the existing docstring convention in that file. Do not simply paste the new outputs.

If **no** golden moves, that means the synthetic fixture never has `low > market`; say so explicitly in your report rather than treating it as nothing to do.

- [ ] **Step 10: Run the four gates**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`
Expected: all PASS.

- [ ] **Step 11: Commit**

```bash
git add src/pkmn_quant/engine/costs.py src/pkmn_quant/engine/execution.py \
        tests/engine/test_costs.py tests/engine/test_execution.py tests/test_cli_backtest.py \
        cpp/src/pkmn_engine/costs.hpp cpp/src/pkmn_engine/execution.cpp \
        cpp/tests/test_costs.cpp
git commit -m "fix(costs): buys price at max(market, low) — market is sales-derived, low is the cheapest listing"
```

---

### Task 3: Round-trip pairing and horizon-matched edge

**Files:**
- Create: `src/pkmn_quant/research/horizon.py`
- Create: `tests/research/test_horizon.py`

**Interfaces:**
- Consumes: nothing from Tasks 1-2 at the code level; it reads run artifacts.
- Produces:
  - `@dataclass(frozen=True) RoundTrip` with fields `product_id: int`, `sub_type: str`, `entry: date`, `exit: date`, `holding_days: int`, `quantity: int`, `strategy_return: float`, `index_return: float`, `edge: float`, `still_open: bool`.
  - `pair_round_trips(fills: pl.DataFrame) -> list[tuple[...]]` — internal FIFO pairing.
  - `horizon_report(fills: pl.DataFrame, index: pl.DataFrame, final_marks: dict[tuple[int, str], float], last_day: date) -> HorizonReport`.
  - `@dataclass(frozen=True) HorizonReport` with `trips: list[RoundTrip]`, `median_edge: float`, `mean_edge: float`, `win_rate: float`, `holding_buckets: dict[str, int]`, `edge_by_bucket: dict[str, float]`.

Buckets are fixed and named exactly: `"<1m"`, `"1-3m"`, `"3-6m"`, `">6m"`, split at 30, 90 and 180 holding days (`<30`, `30-89`, `90-179`, `>=180`).

- [ ] **Step 1: Write the failing tests**

Create `tests/research/test_horizon.py`:

```python
"""Horizon-matched evaluation: each round trip against the sealed index over
that trip's own entry and exit dates."""

from datetime import date

import polars as pl
import pytest

from pkmn_quant.research.horizon import horizon_report

FILL_SCHEMA = {
    "day": pl.Date, "product_id": pl.Int64, "sub_type": pl.Utf8,
    "quantity": pl.Int64, "price": pl.Float64, "fees": pl.Float64, "impact": pl.Float64,
}


def _fills(rows: list[dict[str, object]]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=FILL_SCHEMA)


def _index(points: list[tuple[date, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        [{"date": d, "equity": e} for d, e in points],
        schema={"date": pl.Date, "equity": pl.Float64},
    )


def test_edge_is_strategy_return_minus_index_return_over_the_same_dates() -> None:
    """The whole point: compare against holding sealed for the SAME window.

    Hand-derivation: buy 1 @ 100 (fees 1, impact 0) on 2025-01-01, sell 1 @
    120 (fees 1, impact 0) on 2025-03-02. Cost basis 101, proceeds 119,
    strategy return 119/101 - 1 = +17.8218%. Index 1000 -> 1100 = +10%.
    Edge = +7.8218 pts.
    """
    fills = _fills([
        {"day": date(2025, 1, 1), "product_id": 1, "sub_type": "Normal",
         "quantity": 1, "price": 100.0, "fees": 1.0, "impact": 0.0},
        {"day": date(2025, 3, 2), "product_id": 1, "sub_type": "Normal",
         "quantity": -1, "price": 120.0, "fees": 1.0, "impact": 0.0},
    ])
    index = _index([(date(2025, 1, 1), 1000.0), (date(2025, 3, 2), 1100.0)])
    rep = horizon_report(fills, index, final_marks={}, last_day=date(2025, 3, 2))
    assert len(rep.trips) == 1
    trip = rep.trips[0]
    assert trip.holding_days == 60
    assert trip.strategy_return == pytest.approx(0.178218, abs=1e-6)
    assert trip.index_return == pytest.approx(0.10, abs=1e-12)
    assert trip.edge == pytest.approx(0.078218, abs=1e-6)
    assert trip.still_open is False
    assert rep.holding_buckets["1-3m"] == 1


def test_fifo_splits_one_sell_across_two_buy_lots() -> None:
    """Two buys then one sell of both units must produce two round trips,
    each priced against its own lot."""
    fills = _fills([
        {"day": date(2025, 1, 1), "product_id": 1, "sub_type": "Normal",
         "quantity": 1, "price": 100.0, "fees": 0.0, "impact": 0.0},
        {"day": date(2025, 2, 1), "product_id": 1, "sub_type": "Normal",
         "quantity": 1, "price": 200.0, "fees": 0.0, "impact": 0.0},
        {"day": date(2025, 3, 1), "product_id": 1, "sub_type": "Normal",
         "quantity": -2, "price": 300.0, "fees": 0.0, "impact": 0.0},
    ])
    index = _index([
        (date(2025, 1, 1), 1000.0), (date(2025, 2, 1), 1000.0), (date(2025, 3, 1), 1000.0)
    ])
    rep = horizon_report(fills, index, final_marks={}, last_day=date(2025, 3, 1))
    assert [t.entry for t in rep.trips] == [date(2025, 1, 1), date(2025, 2, 1)]
    assert rep.trips[0].strategy_return == pytest.approx(2.0)   # 100 -> 300
    assert rep.trips[1].strategy_return == pytest.approx(0.5)   # 200 -> 300


def test_open_position_is_closed_at_the_final_mark_and_flagged() -> None:
    """Open trades must be counted but never silently mixed with completed
    ones."""
    fills = _fills([
        {"day": date(2025, 1, 1), "product_id": 1, "sub_type": "Normal",
         "quantity": 1, "price": 100.0, "fees": 0.0, "impact": 0.0},
    ])
    index = _index([(date(2025, 1, 1), 1000.0), (date(2025, 3, 2), 1100.0)])
    rep = horizon_report(
        fills, index, final_marks={(1, "Normal"): 150.0}, last_day=date(2025, 3, 2)
    )
    assert len(rep.trips) == 1
    assert rep.trips[0].still_open is True
    assert rep.trips[0].exit == date(2025, 3, 2)
    assert rep.trips[0].strategy_return == pytest.approx(0.5)


def test_same_day_entry_and_exit_has_zero_holding_days() -> None:
    fills = _fills([
        {"day": date(2025, 1, 1), "product_id": 1, "sub_type": "Normal",
         "quantity": 1, "price": 100.0, "fees": 0.0, "impact": 0.0},
        {"day": date(2025, 1, 1), "product_id": 1, "sub_type": "Normal",
         "quantity": -1, "price": 110.0, "fees": 0.0, "impact": 0.0},
    ])
    index = _index([(date(2025, 1, 1), 1000.0)])
    rep = horizon_report(fills, index, final_marks={}, last_day=date(2025, 1, 1))
    assert rep.trips[0].holding_days == 0
    assert rep.trips[0].index_return == 0.0


def test_index_uses_the_nearest_prior_date_when_a_day_is_missing() -> None:
    """Non-trading days must not silently become a zero index return."""
    fills = _fills([
        {"day": date(2025, 1, 1), "product_id": 1, "sub_type": "Normal",
         "quantity": 1, "price": 100.0, "fees": 0.0, "impact": 0.0},
        {"day": date(2025, 3, 2), "product_id": 1, "sub_type": "Normal",
         "quantity": -1, "price": 100.0, "fees": 0.0, "impact": 0.0},
    ])
    index = _index([(date(2025, 1, 1), 1000.0), (date(2025, 2, 28), 1100.0)])
    rep = horizon_report(fills, index, final_marks={}, last_day=date(2025, 3, 2))
    assert rep.trips[0].index_return == pytest.approx(0.10)


def test_a_sell_with_no_matching_buy_raises() -> None:
    """The engine cannot produce this; if it appears the artifact is wrong and
    must not be silently averaged into a result."""
    fills = _fills([
        {"day": date(2025, 1, 1), "product_id": 1, "sub_type": "Normal",
         "quantity": -1, "price": 100.0, "fees": 0.0, "impact": 0.0},
    ])
    index = _index([(date(2025, 1, 1), 1000.0)])
    with pytest.raises(ValueError, match="no matching buy"):
        horizon_report(fills, index, final_marks={}, last_day=date(2025, 1, 1))


def test_win_rate_and_median_edge_summarise_the_distribution() -> None:
    """Three trips, two beating the flat index."""
    rows: list[dict[str, object]] = []
    for i, (pid, exit_price) in enumerate([(1, 120.0), (2, 120.0), (3, 80.0)]):
        rows.append({"day": date(2025, 1, 1), "product_id": pid, "sub_type": "Normal",
                     "quantity": 1, "price": 100.0, "fees": 0.0, "impact": 0.0})
        rows.append({"day": date(2025, 3, 2), "product_id": pid, "sub_type": "Normal",
                     "quantity": -1, "price": exit_price, "fees": 0.0, "impact": 0.0})
    index = _index([(date(2025, 1, 1), 1000.0), (date(2025, 3, 2), 1000.0)])
    rep = horizon_report(_fills(rows), index, final_marks={}, last_day=date(2025, 3, 2))
    assert rep.win_rate == pytest.approx(2 / 3)
    assert rep.median_edge == pytest.approx(0.20)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/research/test_horizon.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pkmn_quant.research.horizon'`.

- [ ] **Step 3: Implement the module**

Create `src/pkmn_quant/research/horizon.py`. Requirements the tests pin, stated so you implement behaviour rather than pattern-match the assertions:

- Read `fills` sorted by `day`, then by the frame's existing order within a day. Positive `quantity` is a buy, negative a sell.
- Maintain a FIFO deque of open lots per `(product_id, sub_type)`. Each lot carries entry date, remaining quantity, and per-unit cost basis `price + (fees + impact) / quantity`.
- A sell consumes lots front-first, splitting when it spans more than one, and emits one `RoundTrip` per consumed lot with per-unit proceeds `price - (fees + impact) / abs(quantity)`.
- `strategy_return = proceeds_per_unit / basis_per_unit - 1`.
- `index_return = index_at(exit) / index_at(entry) - 1`, where `index_at(d)` is the equity on `d` or, if absent, the nearest strictly-earlier date. Raise `ValueError` if `d` precedes the first index date.
- `edge = strategy_return - index_return`.
- After all fills, any lot still open becomes a `RoundTrip` with `exit=last_day`, `still_open=True`, and proceeds taken from `final_marks[(product_id, sub_type)]`. If a mark is missing, raise `ValueError` naming the asset — do not fall back to the entry price, which would fabricate a 0% return.
- A sell with no open lot raises `ValueError` containing the text `"no matching buy"`.
- `win_rate` is the fraction of trips with `edge > 0`. `median_edge`/`mean_edge` are over all trips. `holding_buckets` counts trips per bucket; `edge_by_bucket` is the median edge per bucket, omitting empty buckets.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/research/test_horizon.py -v`
Expected: PASS — all seven.

- [ ] **Step 5: Run the four gates**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/pkmn_quant/research/horizon.py tests/research/test_horizon.py
git commit -m "feat(research): horizon-matched round-trip evaluation against the sealed index"
```

---

### Task 4: `pkmn horizon` command

**Files:**
- Modify: `src/pkmn_quant/cli.py`
- Create: `tests/test_cli_horizon.py`

**Interfaces:**
- Consumes: `horizon_report`, `HorizonReport`, `RoundTrip` from Task 3.
- Produces: `pkmn horizon <run-id> [--benchmark <run-id>]`, writing `data/results/horizon-<run-id>/report.md` and `horizon.json`, and recording a registry entry via the existing `record_run` path.

- [ ] **Step 1: Write the failing CLI test**

Create `tests/test_cli_horizon.py`. It must, using `tmp_path` and the same CLI-invocation helper the existing `tests/test_cli_backtest.py` uses (read that file first and follow its pattern — do not invent a second way to invoke the CLI):

1. write a fake run directory containing `fills.parquet` and `equity.parquet` plus a registry record, mirroring what `run_single_backtest` produces;
2. write a fake benchmark run directory with its own `equity.parquet`;
3. invoke `pkmn horizon <run-id> --benchmark <bench-id>`;
4. assert exit code 0, that `horizon.json` exists and contains `median_edge`, `win_rate` and a `trips` array of the expected length, and that `report.md` contains the string `1-3m`.

Add a second test asserting a clear non-zero exit and message when the run id does not exist.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cli_horizon.py -v`
Expected: FAIL — no such command `horizon`.

- [ ] **Step 3: Implement the command**

Add a `horizon` command to `src/pkmn_quant/cli.py` following the structure of the existing `evaluate` command. It must:

- resolve `<run-id>` through the run registry and load `fills.parquet` from that run's artifact directory;
- resolve the benchmark: use `--benchmark` when given; otherwise pick the most recent registry record whose `command == "backtest"` and whose `strategy` starts with `buy-and-hold` and which is **not** tagged `benchmark_for` — the same rule `/api/runs` and the dashboard already apply. **Do not glob `data/results/` for a directory name.** That is exactly the bug that published a wrong Reality Check p-value against a stale pre-swap benchmark; the fix and its reasoning are recorded in CLAUDE.md's gotchas.
- fail with a clear message, not a traceback, when the run id is unknown or the run has no fills;
- build `final_marks` from the run's last equity date and the warehouse marks on that date;
- write `report.md` (a summary table, the holding-period histogram, and edge by bucket) and `horizon.json` (the full per-trip table plus summary) into `data/results/horizon-<run-id>/`;
- record a registry entry so the run is discoverable via `pkmn runs list`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_cli_horizon.py -v`
Expected: PASS.

- [ ] **Step 5: Run the four gates**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/pkmn_quant/cli.py tests/test_cli_horizon.py
git commit -m "feat(cli): pkmn horizon — per-trade edge against the sealed index"
```

---

### Task 5: Full-data acceptance and the research re-run

**Files:**
- Modify: none (this task produces measurements, not code)

**Interfaces:**
- Consumes: everything from Tasks 1-4.
- Produces: the numbers Task 6 documents.

**Report the real output of every command, including anything surprising or that contradicts what this plan predicts.** Task 6 rewrites the project's published results from your numbers.

- [ ] **Step 1: Full-data bit-for-bit acceptance**

```bash
uv run python scripts/parity_full.py 2>&1 | tail -20
```
Expected: all five native strategies plus the ml-ranker bridge PASS. **If parity fails, STOP and report** — running the zoo on two engines that disagree produces numbers nobody can defend.

- [ ] **Step 2: Record the benchmark's deployment**

Run the screened benchmark and report `total_return`, fill count, run id, and the fraction of the $10,000 actually deployed (notional, and all-in including fees and impact):

```bash
uv run pkmn backtest --start 2024-03-01 --end 2026-06-30 2>&1 | tail -12
```

The pre-repair reference is **+10.53%** on **4.7%** deployed ($466.78 notional / $550.77 all-in). Expected here: deployment in the high 90s. **If deployment is below 90%, STOP and report** — the sweep is not working and every downstream number would be measured against a broken bar.

- [ ] **Step 3: Re-run the four native strategies**

```bash
for s in sealed-accumulation dip-buyer xs-momentum cost-aware-reversion; do
  echo "=== $s ==="
  caffeinate -dimsu uv run pkmn walkforward --strategy "$s" \
    --start 2024-03-01 --end 2026-06-30 --trials 15 2>&1 | tail -8
done
```

Roughly 4.5 minutes each. Record every stitched OOS total return and run id.

- [ ] **Step 4: Re-run the two ML strategies, serially**

```bash
caffeinate -dimsu uv run pkmn walkforward --strategy ml-ranker \
  --start 2024-03-01 --end 2026-06-30 --trials 15 --workers 1 2>&1 | tail -8
caffeinate -dimsu uv run pkmn walkforward --strategy ml-ranker-v2 \
  --start 2024-03-01 --end 2026-06-30 --trials 15 --workers 1 2>&1 | tail -8
```

**`--workers 1` and `caffeinate -dimsu` are both mandatory and both were learned the hard way.** Fold-parallel thrashed the machine to a 7.4-hour non-completion (load 89, swap 24.6 GB of 25.6 GB, 97 threads on 8 cores); serial finished in 75 minutes. `caffeinate -i` blocks only *idle* sleep and a run died silently at 64 minutes with a zero-byte log. Expect roughly 75 and 140 minutes. Run these in the background and poll — a foreground call will exceed any tool timeout. `pkmn walkforward` prints nothing until it completes, so absence of output is not a failure signal; check the process is alive instead.

- [ ] **Step 5: Rigor and horizon reports**

```bash
uv run pkmn evaluate 2>&1 | tail -20
for s in sealed-accumulation dip-buyer xs-momentum cost-aware-reversion ml-ranker ml-ranker-v2; do
  echo "=== $s ==="; uv run pkmn horizon "<that strategy's run id>" 2>&1 | tail -12
done
```

Record the joint Reality Check p, every deflated Sharpe, and for each strategy the median edge, win rate, holding-period histogram, and edge in the `1-3m` bucket.

- [ ] **Step 6: Report**

No commit. Report every number above, plus: how many strategies trade in the `1-3m` band at all, and whether any has a positive median edge there.

---

### Task 6: Document the results

**Files:**
- Modify: `docs/research-findings-2026-07.md`
- Modify: `CLAUDE.md`
- Modify: `README.md`

- [ ] **Step 1: Write the findings section**

Append a section to `docs/research-findings-2026-07.md` matching the voice of the existing ones — hypothesis, measurement, honest conclusion, negative results stated plainly. It must contain:

- **What changed and why**: the benchmark was 4.7% deployed and buys priced from a number nothing was offered at. Quote the new deployment figure beside the old.
- **The resolution of the `market < low` question**, which `docs/research-findings-2026-07.md:1482` records as open: neither a data defect nor a thin print, but a category error — `marketPrice` is sales-derived, `lowPrice` is the cheapest listing. Mark that open question closed.
- **A before/after table** for all six strategies plus the benchmark, stating each number's cost regime, taking the "before" column from the full-set-coverage section of this same file.
- **The horizon results**: per strategy, median edge, win rate, and the holding-period histogram, with the `1-3m` bucket called out — this is the number the project now exists to produce.
- **The honest conclusion**, including the case where nothing wins. A strategy that loses to a properly-invested benchmark over its own holding window is a real finding and must be reported as one.
- **Whether the strategies got worse**, which this plan predicted they might. Say so plainly and do not present it as a regression.

- [ ] **Step 2: Update CLAUDE.md**

- Add a status bullet matching the existing density: what shipped, the measured before/after, your own gate counts, the honest conclusion.
- **Update the buy-and-hold benchmark reference figure** in the Conventions section. It is the comparison point for every future strategy and a stale value there misleads every later comparison.
- Add `pkmn horizon` to the Commands block.
- Add a Conventions bullet: the benchmark is deliberately **not** walk-forward-searchable, and why (tuning the bar to maximise its own return makes every comparison self-serving).
- Add a Conventions bullet on the buy floor and the deliberate sell-side asymmetry (no bid series exists).
- Note in the layout section that `research/screen.py` is shared by the benchmark now and the liquidity-floor experiment later.

- [ ] **Step 3: Update README.md**

Update any line quoting the benchmark return or describing how strategies are compared. The README currently carries the 4.7%-deployed figures.

- [ ] **Step 4: Full verification sweep**

```bash
uv sync --group api && uv run pytest
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run --group dashboard pytest tests/test_dashboard.py
cd web && npm run check && npm run build
```

Run these with nothing else touching the venv. Report the real counts.

- [ ] **Step 5: Commit**

```bash
git add docs/research-findings-2026-07.md CLAUDE.md README.md
git commit -m "docs: instrument repair and horizon-matched results"
```

---

## Self-review notes

- **Spec coverage:** liquidity screen with density/spread/product_id keys (Task 1) ✓; deficit sweep to full deployment (Task 1) ✓; `n_assets` declarative and CLI-settable (Task 1 Step 11) ✓ — *deliberately not walk-forward-searchable, see the deviation section*; buy floor at `max(market, low)` with the walk starting from the floor (Task 2) ✓; sells and marking explicitly unchanged (Task 2, Global Constraints) ✓; C++ ports with bit-for-bit parity in the same task as each behaviour change (Tasks 1-2) ✓; goldens updated with hand-derivation (Task 2 Step 9) ✓; FIFO pairing with all five named edge cases (Task 3) ✓; distribution outputs including the 1-3m bucket (Tasks 3-4) ✓; `pkmn horizon` (Task 4) ✓; re-run in benchmark-first order with `--workers 1` and `caffeinate -dimsu` (Task 5) ✓; docs including the benchmark reference figure and closing the `market < low` open question (Task 6) ✓. Out of scope and in no task: the liquidity-floor experiment, ML loss diagnosis, max-hold constraints, sell-side floors, run-id-keyed evaluate artifacts ✓.
- **Type consistency:** `rank_assets(history, today) -> pl.DataFrame` is produced in Task 1 Step 3 and consumed in Step 7. `buy_impact(market, mid, low, qty, used=0)` has the same signature in Task 2's Python (Step 3), executor call sites (Step 4), and C++ (Step 6). `horizon_report(fills, index, final_marks, last_day) -> HorizonReport` is defined in Task 3 and called in Task 4. Bucket names `"<1m"`, `"1-3m"`, `"3-6m"`, `">6m"` are fixed in Task 3's interface block and asserted in Tasks 3 and 4.
- **The two engine-parity tasks cannot be split** into Python-then-C++ commits: `tests/test_native_parity.py` runs in the standard gate, so a Python-only commit fails its own gates. This is stated in each task rather than left to be discovered.
- **The riskiest task is Task 1**, because the deficit sweep must produce bit-identical doubles in two languages. The mitigation is written into Step 10: identical arithmetic order, not algebraic equivalents. The sort key is safer than it looks — `AssetId` ordering already equals Python's `(product_id, sub_type)` ordering by construction (`cpp/src/pkmn_engine/types.hpp:11-13`), so only `n_prints` and `spread` have to agree, and `spread` is one subtraction and one division of values both sides read from the same source row.
- **Verified against the real tree while writing this plan:** the test modules are `tests/strategies/test_buy_and_hold.py`, `tests/engine/test_costs.py`, and `tests/engine/test_execution.py` (there is no top-level `tests/test_strategies.py` or `tests/test_costs.py`); `tests/research/__init__.py` already exists, so the two new `tests/research/` modules need no package scaffolding; and `make_ctx` in the buy-and-hold tests passes an **empty** `history`, which is why Task 1 Step 5 requires the screen to keep history-less assets rather than drop them.
- **Task 5 can invalidate Task 6 before it starts.** If parity fails at Step 1 or deployment is under 90% at Step 2, the plan stops there rather than documenting numbers built on a broken instrument.
