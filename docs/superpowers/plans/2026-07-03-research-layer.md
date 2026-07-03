# pkmn_quant Plan 3: Research Layer (Strategies + Walk-Forward)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Three real strategies (sealed accumulation, dip-buying mean reversion, cross-sectional momentum), a walk-forward harness with optuna parameter search, stitched out-of-sample reporting, and a `pkmn walkforward` CLI — the machinery that turns the engine into evidence.

**Architecture:** `src/pkmn_quant/strategies/` gains three parameterized strategies (each a pure `on_bar` over Context, ~60-80 lines, reset-safe per the Strategy.reset contract). `src/pkmn_quant/research/` is new: `folds.py` (rolling window date math), `search.py` (optuna optimization of a strategy's params over an in-sample window), `walkforward.py` (per fold: optimize IS → freeze → run OOS → stitch OOS segments into one honest equity curve), `report.py` (markdown fold table + stitched metrics + parquet artifacts). CLI grows `pkmn walkforward`. Plan 4 (live signals + dashboard) builds on this.

**Tech Stack:** existing stack + **optuna** (runtime dep — industry-standard hyperparameter search). No pandas/quantstats: tearsheet metrics are extended natively in `engine/metrics.py` (Sortino, Calmar) — deviation from spec's "quantstats" note, rationale: quantstats drags pandas+matplotlib+scipy and fights our polars-native stack; the formulas are standard either way. (Flagged to the user at plan presentation.)

**Key design decisions:**
- Walk-forward folds: optimize on `is_days` in-sample, test on the following `oos_days` out-of-sample, step forward by `oos_days` (non-overlapping OOS). Stitched curve = OOS segments chained by compounding (each segment's returns applied to the prior terminal value).
- Optimization objective: configurable, default `total_return` (Sharpe is inflated by mark smoothing — documented in Plan 2 Task 10 findings — so it's available but not the default).
- Every walk-forward uses a **fresh strategy instance per Backtest run** via a factory `Callable[[dict], Strategy]`; `Backtest.run()` additionally calls `reset()` (belt and suspenders).
- Strategies must never emit buys and sells such that buys depend on sell proceeds ordering accidentally: emit sells FIRST in the order list (the simulator processes sequentially, so sell proceeds are available to later buys in the same batch — that is intended and documented).
- Determinism: optuna sampler seeded (`TPESampler(seed=...)`); all strategy candidate rankings tie-broken by `product_id`.

---

### Task 1: Extend metrics — Sortino and Calmar

**Files:**
- Modify: `src/pkmn_quant/engine/metrics.py`
- Modify: `tests/engine/test_metrics.py`

- [ ] **Step 1: Add failing tests** (append to `tests/engine/test_metrics.py`):

```python
def test_sortino_positive_for_up_curve_with_dips() -> None:
    # Net-up curve with some down days: downside deviation exists, mean > 0.
    s = summarize(curve([100.0, 102.0, 101.0, 104.0, 103.0, 106.0]))
    assert s["sortino"] > 0
    assert "calmar" in s


def test_sortino_zero_when_no_downside() -> None:
    s = summarize(curve([100.0, 101.0, 102.0]))
    # No negative daily returns: downside deviation is 0 -> sortino reported 0.0
    assert s["sortino"] == 0.0


def test_calmar_is_cagr_over_abs_drawdown() -> None:
    s = summarize(curve([100.0, 120.0, 90.0, 108.0]))
    assert s["calmar"] == pytest.approx(s["cagr"] / 0.25)


def test_calmar_zero_when_no_drawdown() -> None:
    s = summarize(curve([100.0, 101.0, 102.0]))
    assert s["calmar"] == 0.0
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/engine/test_metrics.py -v` → KeyError "sortino".

- [ ] **Step 3: Implement** — in `summarize`, after the sharpe block, add:

```python
    downside = daily.filter(daily < 0)
    if len(downside) == 0:
        sortino = 0.0
    else:
        downside_dev = float((downside**2).mean()) ** 0.5
        mean_ret = float(cast(float, mean_val)) if mean_val is not None else 0.0
        sortino = mean_ret / downside_dev * math.sqrt(TRADING_DAYS_PER_YEAR)

    calmar = cagr / abs(max_drawdown) if max_drawdown < 0 else 0.0
```

and extend the returned dict (and both early-return dicts) with `"sortino"` and `"calmar"` keys. Update the docstring: Sortino uses downside deviation vs a 0% target; Calmar = CAGR / |max drawdown|.

- [ ] **Step 4: Gates and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
git add src/pkmn_quant/engine/metrics.py tests/engine/test_metrics.py
git commit -m "feat: add Sortino and Calmar to metrics"
```

---

### Task 2: SealedAccumulation strategy

**Files:**
- Create: `src/pkmn_quant/strategies/sealed_accumulation.py`
- Test: `tests/strategies/test_sealed_accumulation.py`

The thesis from the spec: sealed products spike at release, crash over ~2-3 months, then grind up as supply dries. Buy sealed products aged between `min_age_days` and `max_age_days` that are down at least `min_drawdown` from their post-release peak; take profit at `take_profit` multiple of avg cost.

- [ ] **Step 1: Write the failing tests** — `tests/strategies/test_sealed_accumulation.py`:

```python
from datetime import date, timedelta

import polars as pl

from pkmn_quant.engine.portfolio import Asset, Position
from pkmn_quant.engine.strategy import Context
from pkmn_quant.strategies.sealed_accumulation import SealedAccumulation

TODAY = date(2025, 6, 1)
BOX = Asset(product_id=1, sub_type="Normal")
FRESH = Asset(product_id=2, sub_type="Normal")  # too young to buy

PRODUCTS = pl.DataFrame(
    {
        "product_id": [1, 2],
        "group_id": [10, 11],
        "name": ["Old Box", "Fresh Box"],
        "rarity": [None, None],
        "kind": ["sealed", "sealed"],
        "released_on": [TODAY - timedelta(days=120), TODAY - timedelta(days=10)],
    }
)


def history_for(asset: Asset, prices: list[tuple[date, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "date": [d for d, _ in prices],
            "product_id": [asset.product_id] * len(prices),
            "sub_type": [asset.sub_type] * len(prices),
            "low": [1.0] * len(prices),
            "mid": [1.0] * len(prices),
            "high": [1.0] * len(prices),
            "market": [p for _, p in prices],
        }
    )


def make_ctx(
    history: pl.DataFrame,
    marks: dict[Asset, float],
    cash: float = 1000.0,
    positions: dict[Asset, Position] | None = None,
) -> Context:
    return Context(
        today=TODAY,
        history=history,
        products=PRODUCTS,
        positions=positions or {},
        cash=cash,
        marks=marks,
    )


def test_buys_aged_drawdown_sealed() -> None:
    # BOX peaked at 100, now 70: 30% drawdown, age 120d -> qualifies.
    hist = history_for(BOX, [(TODAY - timedelta(days=100), 100.0), (TODAY, 70.0)])
    strat = SealedAccumulation(min_drawdown=0.25, budget_frac=0.5)
    orders = strat.on_bar(make_ctx(hist, {BOX: 70.0}))
    assert len(orders) == 1
    assert orders[0].asset == BOX
    assert orders[0].quantity == 7  # floor(1000*0.5 / 70)


def test_ignores_shallow_drawdown() -> None:
    hist = history_for(BOX, [(TODAY - timedelta(days=100), 100.0), (TODAY, 90.0)])
    strat = SealedAccumulation(min_drawdown=0.25)
    assert strat.on_bar(make_ctx(hist, {BOX: 90.0})) == []


def test_ignores_too_young_product() -> None:
    hist = history_for(FRESH, [(TODAY - timedelta(days=5), 100.0), (TODAY, 60.0)])
    strat = SealedAccumulation(min_drawdown=0.25, min_age_days=60)
    assert strat.on_bar(make_ctx(hist, {FRESH: 60.0})) == []


def test_takes_profit_on_held_position() -> None:
    from pkmn_quant.engine.execution import Order

    hist = history_for(BOX, [(TODAY - timedelta(days=100), 100.0), (TODAY, 120.0)])
    strat = SealedAccumulation(take_profit=1.5)
    positions = {BOX: Position(quantity=3, avg_cost=70.0)}
    orders = strat.on_bar(make_ctx(hist, {BOX: 120.0}, positions=positions))
    assert orders == [Order(asset=BOX, quantity=-3)]  # 120 >= 70*1.5


def test_does_not_rebuy_held_asset() -> None:
    hist = history_for(BOX, [(TODAY - timedelta(days=100), 100.0), (TODAY, 70.0)])
    strat = SealedAccumulation(min_drawdown=0.25)
    positions = {BOX: Position(quantity=1, avg_cost=70.0)}
    assert strat.on_bar(make_ctx(hist, {BOX: 70.0}, positions=positions)) == []


def test_max_positions_respected() -> None:
    strat = SealedAccumulation(max_positions=0)
    hist = history_for(BOX, [(TODAY - timedelta(days=100), 100.0), (TODAY, 70.0)])
    assert strat.on_bar(make_ctx(hist, {BOX: 70.0})) == []


def test_reset_clears_nothing_but_is_safe() -> None:
    strat = SealedAccumulation()
    strat.reset()  # stateless besides params; must not raise
```

- [ ] **Step 2: Run to verify failure** — ModuleNotFoundError.

- [ ] **Step 3: Implement** — `src/pkmn_quant/strategies/sealed_accumulation.py`:

```python
"""Buy sealed product after the post-release crash; sell at a target multiple."""

from __future__ import annotations

import math
from datetime import timedelta

import polars as pl

from pkmn_quant.engine.execution import Order
from pkmn_quant.engine.portfolio import Asset
from pkmn_quant.engine.strategy import Context, Strategy


class SealedAccumulation(Strategy):
    """Entry: sealed, aged [min_age_days, max_age_days], down >= min_drawdown
    from its peak-to-date. Exit: mark >= avg_cost * take_profit. Long-only,
    sells emitted before buys.
    """

    def __init__(
        self,
        min_age_days: int = 60,
        max_age_days: int = 365,
        min_drawdown: float = 0.25,
        take_profit: float = 1.5,
        max_positions: int = 10,
        budget_frac: float = 0.10,
    ) -> None:
        self.min_age_days = min_age_days
        self.max_age_days = max_age_days
        self.min_drawdown = min_drawdown
        self.take_profit = take_profit
        self.max_positions = max_positions
        self.budget_frac = budget_frac
        self.name = "sealed-accumulation"

    def on_bar(self, ctx: Context) -> list[Order]:
        orders: list[Order] = []

        # Exits first: proceeds are available to later buys in the same batch.
        for asset, pos in sorted(ctx.positions.items(), key=lambda kv: kv[0].product_id):
            mark = ctx.marks.get(asset)
            if mark is not None and mark >= pos.avg_cost * self.take_profit:
                orders.append(Order(asset=asset, quantity=-pos.quantity))

        open_slots = self.max_positions - (len(ctx.positions) - len(orders))
        if open_slots <= 0:
            return orders

        aged = ctx.products.filter(
            (pl.col("kind") == "sealed")
            & (pl.col("released_on") <= ctx.today - timedelta(days=self.min_age_days))
            & (pl.col("released_on") >= ctx.today - timedelta(days=self.max_age_days))
        )
        aged_ids = set(aged["product_id"].to_list())
        if not aged_ids:
            return orders

        peaks = (
            ctx.history.filter(pl.col("product_id").is_in(sorted(aged_ids)))
            .group_by(["product_id", "sub_type"])
            .agg(pl.col("market").max().alias("peak"))
        )
        peak_by_asset = {
            Asset(product_id=int(r["product_id"]), sub_type=str(r["sub_type"])): float(r["peak"])
            for r in peaks.iter_rows(named=True)
        }

        candidates: list[tuple[float, Asset, float]] = []  # (drawdown, asset, mark)
        for asset, peak in peak_by_asset.items():
            if asset in ctx.positions or peak <= 0:
                continue
            mark = ctx.marks.get(asset)
            if mark is None:
                continue
            drawdown = 1.0 - mark / peak
            if drawdown >= self.min_drawdown:
                candidates.append((drawdown, asset, mark))

        # Deepest discounts first; deterministic tie-break by product_id.
        candidates.sort(key=lambda c: (-c[0], c[1].product_id))
        budget = ctx.cash * self.budget_frac
        for _, asset, mark in candidates[:open_slots]:
            qty = math.floor(budget / mark)
            if qty > 0:
                orders.append(Order(asset=asset, quantity=qty))
        return orders
```

- [ ] **Step 4: Run tests (7 PASSED), gates, commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
git add src/pkmn_quant/strategies/sealed_accumulation.py tests/strategies/test_sealed_accumulation.py
git commit -m "feat: sealed accumulation strategy"
```

---

### Task 3: DipBuyer strategy (long-only mean reversion)

**Files:**
- Create: `src/pkmn_quant/strategies/dip_buyer.py`
- Test: `tests/strategies/test_dip_buyer.py`

The spec's "fade hype spikes" is unimplementable long-only (no shorting); its long-only mirror: buy sharp dips expecting reversion, exit on time limit or profit target. Stateful (entry dates) → must implement reset().

- [ ] **Step 1: Write the failing tests** — `tests/strategies/test_dip_buyer.py`:

```python
from datetime import date, timedelta

import polars as pl

from pkmn_quant.engine.portfolio import Asset, Position
from pkmn_quant.engine.strategy import Context
from pkmn_quant.strategies.dip_buyer import DipBuyer

TODAY = date(2025, 6, 10)
CARD = Asset(product_id=1, sub_type="Holofoil")

PRODUCTS = pl.DataFrame(
    {
        "product_id": [1],
        "group_id": [10],
        "name": ["Chase Card"],
        "rarity": ["Special Illustration Rare"],
        "kind": ["single"],
        "released_on": [TODAY - timedelta(days=200)],
    }
)


def history_for(prices: list[tuple[date, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "date": [d for d, _ in prices],
            "product_id": [1] * len(prices),
            "sub_type": ["Holofoil"] * len(prices),
            "low": [1.0] * len(prices),
            "mid": [1.0] * len(prices),
            "high": [1.0] * len(prices),
            "market": [p for _, p in prices],
        }
    )


def make_ctx(history: pl.DataFrame, marks: dict[Asset, float], cash: float = 1000.0,
             positions: dict[Asset, Position] | None = None, today: date = TODAY) -> Context:
    return Context(today=today, history=history, products=PRODUCTS,
                   positions=positions or {}, cash=cash, marks=marks)


def test_buys_sharp_dip() -> None:
    hist = history_for([(TODAY - timedelta(days=7), 100.0), (TODAY, 60.0)])  # -40%
    strat = DipBuyer(dip_window_days=7, dip_threshold=0.30, budget_frac=0.5)
    orders = strat.on_bar(make_ctx(hist, {CARD: 60.0}))
    assert [(o.asset, o.quantity) for o in orders] == [(CARD, 8)]  # floor(500/60)


def test_ignores_small_dip() -> None:
    hist = history_for([(TODAY - timedelta(days=7), 100.0), (TODAY, 85.0)])  # -15%
    strat = DipBuyer(dip_threshold=0.30)
    assert strat.on_bar(make_ctx(hist, {CARD: 85.0})) == []


def test_exits_after_hold_days() -> None:
    hist = history_for([(TODAY - timedelta(days=7), 100.0), (TODAY, 60.0)])
    strat = DipBuyer(dip_threshold=0.30, hold_days=10, budget_frac=0.5)
    strat.on_bar(make_ctx(hist, {CARD: 60.0}))  # records entry intent today
    later = TODAY + timedelta(days=11)
    positions = {CARD: Position(quantity=8, avg_cost=60.0)}
    orders = strat.on_bar(make_ctx(hist, {CARD: 61.0}, positions=positions, today=later))
    assert [(o.asset, o.quantity) for o in orders] == [(CARD, -8)]


def test_exits_on_take_profit_before_hold_days() -> None:
    hist = history_for([(TODAY - timedelta(days=7), 100.0), (TODAY, 60.0)])
    strat = DipBuyer(dip_threshold=0.30, hold_days=30, take_profit=1.2, budget_frac=0.5)
    strat.on_bar(make_ctx(hist, {CARD: 60.0}))
    positions = {CARD: Position(quantity=8, avg_cost=60.0)}
    soon = TODAY + timedelta(days=2)
    orders = strat.on_bar(make_ctx(hist, {CARD: 75.0}, positions=positions, today=soon))
    assert [(o.asset, o.quantity) for o in orders] == [(CARD, -8)]  # 75 >= 60*1.2


def test_reset_clears_entry_dates() -> None:
    hist = history_for([(TODAY - timedelta(days=7), 100.0), (TODAY, 60.0)])
    strat = DipBuyer(dip_threshold=0.30, budget_frac=0.5)
    strat.on_bar(make_ctx(hist, {CARD: 60.0}))
    assert strat._entries  # noqa: SLF001 - white-box check
    strat.reset()
    assert not strat._entries  # noqa: SLF001
```

(If ruff complains about SLF001 despite noqa or the rule isn't enabled, drop the noqa comments.)

- [ ] **Step 2: Run to verify failure**, then **Step 3: Implement** — `src/pkmn_quant/strategies/dip_buyer.py`:

```python
"""Long-only mean reversion: buy sharp dips, exit on time or profit target."""

from __future__ import annotations

import math
from datetime import date, timedelta

import polars as pl

from pkmn_quant.engine.execution import Order
from pkmn_quant.engine.portfolio import Asset
from pkmn_quant.engine.strategy import Context, Strategy


class DipBuyer(Strategy):
    """Entry: singles down >= dip_threshold over dip_window_days.
    Exit: held >= hold_days, or mark >= avg_cost * take_profit.
    Stateful (_entries: asset -> entry-intent date); reset() clears it.
    """

    def __init__(
        self,
        dip_window_days: int = 7,
        dip_threshold: float = 0.30,
        hold_days: int = 30,
        take_profit: float = 1.25,
        max_positions: int = 10,
        budget_frac: float = 0.10,
        min_price: float = 3.0,
    ) -> None:
        self.dip_window_days = dip_window_days
        self.dip_threshold = dip_threshold
        self.hold_days = hold_days
        self.take_profit = take_profit
        self.max_positions = max_positions
        self.budget_frac = budget_frac
        self.min_price = min_price
        self.name = "dip-buyer"
        self._entries: dict[Asset, date] = {}

    def reset(self) -> None:
        self._entries = {}

    def on_bar(self, ctx: Context) -> list[Order]:
        orders: list[Order] = []

        # Exits first (sell proceeds fund later buys in the same batch).
        for asset, pos in sorted(ctx.positions.items(), key=lambda kv: kv[0].product_id):
            mark = ctx.marks.get(asset)
            entered = self._entries.get(asset)
            too_old = entered is not None and (ctx.today - entered).days >= self.hold_days
            hit_target = mark is not None and mark >= pos.avg_cost * self.take_profit
            if too_old or hit_target:
                orders.append(Order(asset=asset, quantity=-pos.quantity))
                self._entries.pop(asset, None)

        open_slots = self.max_positions - (len(ctx.positions) - len(orders))
        if open_slots <= 0:
            return orders

        single_ids = set(
            ctx.products.filter(pl.col("kind") == "single")["product_id"].to_list()
        )
        window_start = ctx.today - timedelta(days=self.dip_window_days)
        past = (
            ctx.history.filter(
                (pl.col("date") <= window_start)
                & pl.col("product_id").is_in(sorted(single_ids))
            )
            .group_by(["product_id", "sub_type"])
            .agg(pl.col("market").sort_by(pl.col("date")).last().alias("past"))
        )
        past_by_asset = {
            Asset(product_id=int(r["product_id"]), sub_type=str(r["sub_type"])): float(r["past"])
            for r in past.iter_rows(named=True)
        }

        candidates: list[tuple[float, Asset, float]] = []
        for asset, past_price in past_by_asset.items():
            if asset in ctx.positions or asset in self._entries or past_price <= 0:
                continue
            mark = ctx.marks.get(asset)
            if mark is None or mark < self.min_price:
                continue
            ret = mark / past_price - 1.0
            if ret <= -self.dip_threshold:
                candidates.append((ret, asset, mark))

        candidates.sort(key=lambda c: (c[0], c[1].product_id))  # deepest dip first
        budget = ctx.cash * self.budget_frac
        for _, asset, mark in candidates[:open_slots]:
            qty = math.floor(budget / mark)
            if qty > 0:
                orders.append(Order(asset=asset, quantity=qty))
                self._entries[asset] = ctx.today
        return orders
```

Note the known imprecision, acceptable for research code: `_entries` records order-EMISSION date, not fill date, and an emitted buy that never fills leaves a stale entry (blocking re-entry for that asset until reset). Document this in the docstring if not already.

- [ ] **Step 4: Run tests (5 PASSED), gates, commit** — `git commit -m "feat: dip-buyer mean reversion strategy"`

---

### Task 4: CrossSectionalMomentum strategy

**Files:**
- Create: `src/pkmn_quant/strategies/momentum.py`
- Test: `tests/strategies/test_momentum.py`

- [ ] **Step 1: Write the failing tests** — `tests/strategies/test_momentum.py`:

```python
from datetime import date, timedelta

import polars as pl

from pkmn_quant.engine.portfolio import Asset, Position
from pkmn_quant.engine.strategy import Context
from pkmn_quant.strategies.momentum import CrossSectionalMomentum

TODAY = date(2025, 6, 10)
HOT = Asset(product_id=1, sub_type="Holofoil")
COLD = Asset(product_id=2, sub_type="Holofoil")

PRODUCTS = pl.DataFrame(
    {
        "product_id": [1, 2],
        "group_id": [10, 10],
        "name": ["Hot", "Cold"],
        "rarity": ["Rare", "Rare"],
        "kind": ["single", "single"],
        "released_on": [TODAY - timedelta(days=200)] * 2,
    }
)


def two_asset_history(lookback_days: int) -> pl.DataFrame:
    past = TODAY - timedelta(days=lookback_days)
    rows = []
    for pid, past_px, now_px in [(1, 10.0, 20.0), (2, 10.0, 9.0)]:
        for d, px in [(past, past_px), (TODAY, now_px)]:
            rows.append(
                {"date": d, "product_id": pid, "sub_type": "Holofoil",
                 "low": 1.0, "mid": 1.0, "high": 1.0, "market": px}
            )
    return pl.DataFrame(rows)


def make_ctx(cash: float = 1000.0, positions: dict[Asset, Position] | None = None,
             today: date = TODAY) -> Context:
    return Context(today=today, history=two_asset_history(30), products=PRODUCTS,
                   positions=positions or {}, cash=cash,
                   marks={HOT: 20.0, COLD: 9.0})


def test_buys_top_momentum_only() -> None:
    strat = CrossSectionalMomentum(lookback_days=30, top_n=1, rebalance_days=30)
    orders = strat.on_bar(make_ctx())
    assert [(o.asset, o.quantity) for o in orders] == [(HOT, 50)]  # floor(1000/1/20)


def test_no_action_between_rebalances() -> None:
    strat = CrossSectionalMomentum(lookback_days=30, top_n=1, rebalance_days=30)
    strat.on_bar(make_ctx())
    assert strat.on_bar(make_ctx(today=TODAY + timedelta(days=5))) == []


def test_rebalance_sells_dropped_names_first() -> None:
    strat = CrossSectionalMomentum(lookback_days=30, top_n=1, rebalance_days=1)
    positions = {COLD: Position(quantity=10, avg_cost=10.0)}
    orders = strat.on_bar(make_ctx(positions=positions))
    assert orders[0].asset == COLD and orders[0].quantity == -10  # sell first
    assert orders[1].asset == HOT and orders[1].quantity > 0


def test_reset_clears_rebalance_clock() -> None:
    strat = CrossSectionalMomentum(rebalance_days=30)
    strat.on_bar(make_ctx())
    strat.reset()
    assert strat.on_bar(make_ctx(today=TODAY + timedelta(days=1))) != []
```

- [ ] **Step 2: verify failure**, then **Step 3: Implement** — `src/pkmn_quant/strategies/momentum.py`:

```python
"""Cross-sectional momentum: hold the top-N trailing performers among singles."""

from __future__ import annotations

import math
from datetime import date, timedelta

import polars as pl

from pkmn_quant.engine.execution import Order
from pkmn_quant.engine.portfolio import Asset
from pkmn_quant.engine.strategy import Context, Strategy


class CrossSectionalMomentum(Strategy):
    """Every rebalance_days: rank singles by trailing lookback return, target
    the top_n equally weighted, sell everything that dropped out (sells
    emitted first). Stateful (_last_rebalance); reset() clears it.
    """

    def __init__(
        self,
        lookback_days: int = 60,
        top_n: int = 10,
        rebalance_days: int = 30,
        min_price: float = 3.0,
    ) -> None:
        self.lookback_days = lookback_days
        self.top_n = top_n
        self.rebalance_days = rebalance_days
        self.min_price = min_price
        self.name = "xs-momentum"
        self._last_rebalance: date | None = None

    def reset(self) -> None:
        self._last_rebalance = None

    def on_bar(self, ctx: Context) -> list[Order]:
        if (
            self._last_rebalance is not None
            and (ctx.today - self._last_rebalance).days < self.rebalance_days
        ):
            return []
        self._last_rebalance = ctx.today

        single_ids = set(
            ctx.products.filter(pl.col("kind") == "single")["product_id"].to_list()
        )
        window_start = ctx.today - timedelta(days=self.lookback_days)
        past = (
            ctx.history.filter(
                (pl.col("date") <= window_start)
                & pl.col("product_id").is_in(sorted(single_ids))
            )
            .group_by(["product_id", "sub_type"])
            .agg(pl.col("market").sort_by(pl.col("date")).last().alias("past"))
        )
        momentum: list[tuple[float, Asset, float]] = []
        for r in past.iter_rows(named=True):
            asset = Asset(product_id=int(r["product_id"]), sub_type=str(r["sub_type"]))
            past_price = float(r["past"])
            mark = ctx.marks.get(asset)
            if mark is None or mark < self.min_price or past_price <= 0:
                continue
            momentum.append((mark / past_price - 1.0, asset, mark))

        momentum.sort(key=lambda m: (-m[0], m[1].product_id))
        target = {asset: mark for _, asset, mark in momentum[: self.top_n]}

        orders: list[Order] = []
        # Sells first: names that dropped out fund the incoming names.
        for asset, pos in sorted(ctx.positions.items(), key=lambda kv: kv[0].product_id):
            if asset not in target:
                orders.append(Order(asset=asset, quantity=-pos.quantity))

        if not target:
            return orders

        equity = ctx.cash + sum(
            pos.quantity * ctx.marks.get(a, pos.avg_cost) for a, pos in ctx.positions.items()
        )
        per_name = equity / len(target)
        for asset, mark in sorted(target.items(), key=lambda kv: kv[0].product_id):
            held = ctx.positions.get(asset)
            held_value = held.quantity * mark if held else 0.0
            qty = math.floor((per_name - held_value) / mark)
            if qty > 0:
                orders.append(Order(asset=asset, quantity=qty))
        return orders
```

- [ ] **Step 4: Run tests (4 PASSED), gates, commit** — `git commit -m "feat: cross-sectional momentum strategy"`

---

### Task 5: Fold generation

**Files:**
- Create: `src/pkmn_quant/research/__init__.py` (empty)
- Create: `src/pkmn_quant/research/folds.py`
- Test: `tests/research/__init__.py` (empty), `tests/research/test_folds.py`

- [ ] **Step 1: Tests** — `tests/research/test_folds.py`:

```python
from datetime import date

import pytest

from pkmn_quant.research.folds import Fold, make_folds


def test_basic_folds() -> None:
    folds = make_folds(
        start=date(2024, 1, 1), end=date(2024, 12, 31), is_days=180, oos_days=60
    )
    f0 = folds[0]
    assert f0 == Fold(
        is_start=date(2024, 1, 1), is_end=date(2024, 6, 28),
        oos_start=date(2024, 6, 29), oos_end=date(2024, 8, 27),
    )
    # folds step by oos_days; every OOS day is covered exactly once
    for a, b in zip(folds, folds[1:], strict=False):
        assert (b.oos_start - a.oos_start).days == 60
    assert folds[-1].oos_end <= date(2024, 12, 31)


def test_no_fold_when_range_too_short() -> None:
    assert make_folds(date(2024, 1, 1), date(2024, 3, 1), is_days=180, oos_days=60) == []


def test_invalid_params_raise() -> None:
    with pytest.raises(ValueError):
        make_folds(date(2024, 1, 1), date(2024, 12, 31), is_days=0, oos_days=60)
```

- [ ] **Step 2: verify failure**, then **Step 3: Implement** — `src/pkmn_quant/research/folds.py`:

```python
"""Rolling walk-forward windows: optimize in-sample, test out-of-sample."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class Fold:
    is_start: date
    is_end: date
    oos_start: date
    oos_end: date


def make_folds(start: date, end: date, is_days: int, oos_days: int) -> list[Fold]:
    """Non-overlapping OOS segments; each fold's IS window precedes its OOS.

    Fold k: IS = [start + k*oos_days, +is_days), OOS = the following oos_days.
    Folds are generated while the full OOS segment fits inside [start, end].
    """
    if is_days <= 0 or oos_days <= 0:
        raise ValueError("is_days and oos_days must be positive")
    folds: list[Fold] = []
    k = 0
    while True:
        is_start = start + timedelta(days=k * oos_days)
        is_end = is_start + timedelta(days=is_days - 1)
        oos_start = is_end + timedelta(days=1)
        oos_end = oos_start + timedelta(days=oos_days - 1)
        if oos_end > end:
            return folds
        folds.append(Fold(is_start=is_start, is_end=is_end, oos_start=oos_start, oos_end=oos_end))
        k += 1
```

- [ ] **Step 4: Run tests (3 PASSED), gates, commit** — `git commit -m "feat: walk-forward fold generation"`

---

### Task 6: Parameter search (optuna)

**Files:**
- Modify: `pyproject.toml` (`uv add "optuna>=4.0"`)
- Create: `src/pkmn_quant/research/search.py`
- Test: `tests/research/test_search.py`

- [ ] **Step 1: Add dep** — `uv add "optuna>=4.0"`. If mypy lacks stubs for optuna, add a targeted `[[tool.mypy.overrides]] module = ["optuna.*"] ignore_missing_imports = true` and report it.

- [ ] **Step 2: Tests** — `tests/research/test_search.py`:

```python
from collections.abc import Callable

import optuna

from pkmn_quant.research.search import SearchSpec, optimize_params


def quadratic_eval(params: dict[str, float | int]) -> float:
    # Max at x=3: deterministic stand-in for "run a backtest, return the metric".
    x = float(params["x"])
    return -((x - 3.0) ** 2)


def space(trial: optuna.Trial) -> dict[str, float | int]:
    return {"x": trial.suggest_float("x", 0.0, 10.0)}


def test_optimize_finds_maximum_deterministically() -> None:
    spec = SearchSpec(space=space, n_trials=40, seed=7)
    best_a = optimize_params(spec, quadratic_eval)
    best_b = optimize_params(spec, quadratic_eval)
    assert best_a == best_b  # seeded -> reproducible
    assert abs(float(best_a["x"]) - 3.0) < 1.0


def test_zero_trials_raises() -> None:
    import pytest

    with pytest.raises(ValueError):
        optimize_params(SearchSpec(space=space, n_trials=0, seed=1), quadratic_eval)
```

- [ ] **Step 3: Implement** — `src/pkmn_quant/research/search.py`:

```python
"""Seeded optuna search over a strategy's parameter space."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import optuna

Params = dict[str, float | int]


@dataclass(frozen=True)
class SearchSpec:
    space: Callable[[optuna.Trial], Params]
    n_trials: int
    seed: int


def optimize_params(spec: SearchSpec, evaluate: Callable[[Params], float]) -> Params:
    """Maximize evaluate(params) over the space; deterministic under the seed."""
    if spec.n_trials <= 0:
        raise ValueError("n_trials must be positive")
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(
        direction="maximize", sampler=optuna.samplers.TPESampler(seed=spec.seed)
    )

    def objective(trial: optuna.Trial) -> float:
        return evaluate(spec.space(trial))

    study.optimize(objective, n_trials=spec.n_trials)
    return dict(study.best_params)
```

Note: `study.best_params` returns the raw suggest values keyed by name — for our spaces (every param suggested with its own name) this equals the params dict; keep spaces flat and 1:1.

- [ ] **Step 4: Run tests (2 PASSED), gates, commit** — `git commit -m "feat: seeded optuna parameter search"`

---

### Task 7: Walk-forward runner + stitcher

**Files:**
- Create: `src/pkmn_quant/research/walkforward.py`
- Test: `tests/research/test_walkforward.py`

- [ ] **Step 1: Tests** — `tests/research/test_walkforward.py` (uses a synthetic warehouse and a trivial strategy factory; no optuna in the loop — inject a fake optimizer for speed):

```python
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA
from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.costs import CostModel
from pkmn_quant.engine.strategy import Strategy
from pkmn_quant.research.folds import Fold
from pkmn_quant.research.walkforward import WalkForwardResult, run_walkforward
from pkmn_quant.strategies.buy_and_hold import BuyAndHold
from tests.helpers import price_row

START = date(2025, 1, 1)


@pytest.fixture
def warehouse(tmp_path: Path) -> Warehouse:
    w = Warehouse(Paths(root=tmp_path))
    # 40 days of a single sealed product drifting upward.
    for i in range(40):
        d = START + timedelta(days=i)
        w.write_prices(d, pl.DataFrame([price_row(d, 1, 100.0 + i)], schema=PRICE_SCHEMA))
    w.write_products(pl.DataFrame({
        "product_id": [1], "group_id": [1], "name": ["Box"],
        "rarity": [None], "kind": ["sealed"], "released_on": [START],
    }))
    return w


def make_strategy(params: dict[str, float | int]) -> Strategy:
    return BuyAndHold(kind="sealed")


def fake_optimizer(fold: Fold, evaluate) -> dict[str, float | int]:
    return {}  # no params to tune; skips optuna entirely


def test_walkforward_stitches_oos_segments(warehouse: Warehouse) -> None:
    result = run_walkforward(
        warehouse=warehouse,
        strategy_factory=make_strategy,
        optimizer=fake_optimizer,
        cost_model=CostModel(fee_rate=0.0, shipping_per_line=0.0),
        start=START, end=START + timedelta(days=39),
        is_days=10, oos_days=10, initial_cash=1000.0,
    )
    assert isinstance(result, WalkForwardResult)
    assert len(result.folds) == 3  # days 0-39: IS 10 + 3 full OOS decades fit
    stitched = result.stitched_curve
    # Stitched curve is continuous: each segment starts where the last ended.
    assert stitched["equity"][0] == pytest.approx(1000.0)
    diffs = stitched.with_columns(
        (pl.col("equity") / pl.col("equity").shift(1) - 1).alias("r")
    )["r"].drop_nulls()
    assert float(diffs.abs().max()) < 0.10  # no discontinuity spikes at seams
    # Each fold records params and both IS and OOS summaries.
    f = result.folds[0]
    assert f.params == {}
    assert "total_return" in f.oos_summary and "total_return" in f.is_summary


def test_overfitting_gap_computed(warehouse: Warehouse) -> None:
    result = run_walkforward(
        warehouse=warehouse, strategy_factory=make_strategy, optimizer=fake_optimizer,
        cost_model=CostModel(fee_rate=0.0, shipping_per_line=0.0),
        start=START, end=START + timedelta(days=39),
        is_days=10, oos_days=10, initial_cash=1000.0,
    )
    assert "is_total_return_mean" in result.summary
    assert "oos_total_return_mean" in result.summary
    assert "overfitting_gap" in result.summary
```

- [ ] **Step 2: verify failure**, then **Step 3: Implement** — `src/pkmn_quant/research/walkforward.py`:

```python
"""Walk-forward: per fold, optimize in-sample, freeze, run out-of-sample, stitch.

The stitched curve is built ONLY from out-of-sample segments - it is the
closest a backtest gets to 'how this would actually have gone'. The gap
between mean IS and mean OOS return measures overfitting.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import polars as pl

from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.backtest import Backtest
from pkmn_quant.engine.costs import CostModel
from pkmn_quant.engine.metrics import summarize
from pkmn_quant.engine.strategy import Strategy
from pkmn_quant.research.folds import Fold, make_folds

Params = dict[str, float | int]
StrategyFactory = Callable[[Params], Strategy]
# optimizer(fold, evaluate) -> best params; evaluate(params) -> IS metric.
Optimizer = Callable[[Fold, Callable[[Params], float]], Params]


@dataclass(frozen=True)
class FoldResult:
    fold: Fold
    params: Params
    is_summary: dict[str, float]
    oos_summary: dict[str, float]
    oos_curve: pl.DataFrame


@dataclass(frozen=True)
class WalkForwardResult:
    folds: list[FoldResult]
    stitched_curve: pl.DataFrame
    summary: dict[str, float]


def run_walkforward(
    warehouse: Warehouse,
    strategy_factory: StrategyFactory,
    optimizer: Optimizer,
    cost_model: CostModel,
    start: date,
    end: date,
    is_days: int,
    oos_days: int,
    initial_cash: float,
    objective_metric: str = "total_return",
) -> WalkForwardResult:
    fold_results: list[FoldResult] = []

    for fold in make_folds(start, end, is_days=is_days, oos_days=oos_days):

        def evaluate(params: Params, fold: Fold = fold) -> float:
            result = Backtest(
                warehouse=warehouse, strategy=strategy_factory(params),
                cost_model=cost_model, start=fold.is_start, end=fold.is_end,
                initial_cash=initial_cash,
            ).run()
            return result.summary[objective_metric]

        best = optimizer(fold, evaluate)
        is_result = Backtest(
            warehouse=warehouse, strategy=strategy_factory(best),
            cost_model=cost_model, start=fold.is_start, end=fold.is_end,
            initial_cash=initial_cash,
        ).run()
        oos_result = Backtest(
            warehouse=warehouse, strategy=strategy_factory(best),
            cost_model=cost_model, start=fold.oos_start, end=fold.oos_end,
            initial_cash=initial_cash,
        ).run()
        fold_results.append(
            FoldResult(
                fold=fold, params=best,
                is_summary=is_result.summary, oos_summary=oos_result.summary,
                oos_curve=oos_result.equity_curve,
            )
        )

    stitched = _stitch([f.oos_curve for f in fold_results], initial_cash)
    summary = _summarize_folds(fold_results, stitched)
    return WalkForwardResult(folds=fold_results, stitched_curve=stitched, summary=summary)


def _stitch(curves: list[pl.DataFrame], initial_cash: float) -> pl.DataFrame:
    """Chain OOS segments: each segment's returns compound on the prior terminal."""
    days: list[date] = []
    equity: list[float] = []
    level = initial_cash
    for curve in curves:
        eq = curve.sort("date")
        base = float(eq["equity"][0])
        if base <= 0:
            continue
        for d, e in zip(eq["date"].to_list(), eq["equity"].to_list(), strict=True):
            days.append(d)
            equity.append(level * float(e) / base)
        level = equity[-1]
    return pl.DataFrame({"date": days, "equity": equity})


def _summarize_folds(
    folds: list[FoldResult], stitched: pl.DataFrame
) -> dict[str, float]:
    def mean(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    is_mean = mean([f.is_summary["total_return"] for f in folds])
    oos_mean = mean([f.oos_summary["total_return"] for f in folds])
    summary = {f"stitched_{k}": v for k, v in summarize(stitched).items()}
    summary["is_total_return_mean"] = is_mean
    summary["oos_total_return_mean"] = oos_mean
    summary["overfitting_gap"] = is_mean - oos_mean
    return summary
```

- [ ] **Step 4: Run tests (2 PASSED), gates, commit** — `git commit -m "feat: walk-forward runner with OOS stitching and overfitting gap"`

---

### Task 8: Strategy registry + report writer

**Files:**
- Create: `src/pkmn_quant/research/registry.py`
- Create: `src/pkmn_quant/research/report.py`
- Test: `tests/research/test_registry.py`, `tests/research/test_report.py`

- [ ] **Step 1: Tests** — `tests/research/test_registry.py`:

```python
import optuna

from pkmn_quant.research.registry import REGISTRY


def test_registry_has_all_tunable_strategies() -> None:
    assert set(REGISTRY) == {"sealed-accumulation", "dip-buyer", "xs-momentum"}


def test_factories_build_with_sampled_params() -> None:
    for name, entry in REGISTRY.items():
        study = optuna.create_study(sampler=optuna.samplers.RandomSampler(seed=1))
        trial = study.ask()
        params = entry.space(trial)
        strategy = entry.factory(params)
        assert strategy.name == name
```

`tests/research/test_report.py`:

```python
from datetime import date

import polars as pl

from pkmn_quant.research.folds import Fold
from pkmn_quant.research.report import render_markdown
from pkmn_quant.research.walkforward import FoldResult, WalkForwardResult


def test_render_markdown_contains_fold_table_and_summary() -> None:
    fold = Fold(date(2024, 1, 1), date(2024, 6, 28), date(2024, 6, 29), date(2024, 8, 27))
    fr = FoldResult(
        fold=fold, params={"x": 1},
        is_summary={"total_return": 0.5, "sharpe": 2.0},
        oos_summary={"total_return": 0.1, "sharpe": 0.8},
        oos_curve=pl.DataFrame({"date": [date(2024, 6, 29)], "equity": [1000.0]}),
    )
    wf = WalkForwardResult(
        folds=[fr],
        stitched_curve=pl.DataFrame({"date": [date(2024, 6, 29)], "equity": [1000.0]}),
        summary={"stitched_total_return": 0.1, "is_total_return_mean": 0.5,
                 "oos_total_return_mean": 0.1, "overfitting_gap": 0.4},
    )
    md = render_markdown(wf, strategy_name="dip-buyer")
    assert "dip-buyer" in md
    assert "2024-06-29" in md          # fold OOS start appears in table
    assert "overfitting_gap" in md
    assert "0.4" in md
```

- [ ] **Step 2: verify failure**, then **Step 3: Implement**:

`src/pkmn_quant/research/registry.py`:

```python
"""Tunable strategies: factory + optuna search space, keyed by CLI name."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import optuna

from pkmn_quant.engine.strategy import Strategy
from pkmn_quant.strategies.dip_buyer import DipBuyer
from pkmn_quant.strategies.momentum import CrossSectionalMomentum
from pkmn_quant.strategies.sealed_accumulation import SealedAccumulation

Params = dict[str, float | int]


@dataclass(frozen=True)
class RegistryEntry:
    factory: Callable[[Params], Strategy]
    space: Callable[[optuna.Trial], Params]


def _sealed_space(trial: optuna.Trial) -> Params:
    return {
        "min_drawdown": trial.suggest_float("min_drawdown", 0.10, 0.50),
        "take_profit": trial.suggest_float("take_profit", 1.2, 2.5),
        "min_age_days": trial.suggest_int("min_age_days", 30, 180),
    }


def _sealed_factory(p: Params) -> Strategy:
    return SealedAccumulation(
        min_drawdown=float(p["min_drawdown"]),
        take_profit=float(p["take_profit"]),
        min_age_days=int(p["min_age_days"]),
    )


def _dip_space(trial: optuna.Trial) -> Params:
    return {
        "dip_threshold": trial.suggest_float("dip_threshold", 0.10, 0.50),
        "hold_days": trial.suggest_int("hold_days", 7, 90),
        "take_profit": trial.suggest_float("take_profit", 1.05, 1.6),
    }


def _dip_factory(p: Params) -> Strategy:
    return DipBuyer(
        dip_threshold=float(p["dip_threshold"]),
        hold_days=int(p["hold_days"]),
        take_profit=float(p["take_profit"]),
    )


def _momentum_space(trial: optuna.Trial) -> Params:
    return {
        "lookback_days": trial.suggest_int("lookback_days", 14, 120),
        "top_n": trial.suggest_int("top_n", 5, 25),
        "rebalance_days": trial.suggest_int("rebalance_days", 7, 60),
    }


def _momentum_factory(p: Params) -> Strategy:
    return CrossSectionalMomentum(
        lookback_days=int(p["lookback_days"]),
        top_n=int(p["top_n"]),
        rebalance_days=int(p["rebalance_days"]),
    )


REGISTRY: dict[str, RegistryEntry] = {
    "sealed-accumulation": RegistryEntry(factory=_sealed_factory, space=_sealed_space),
    "dip-buyer": RegistryEntry(factory=_dip_factory, space=_dip_space),
    "xs-momentum": RegistryEntry(factory=_momentum_factory, space=_momentum_space),
}
```

`src/pkmn_quant/research/report.py`:

```python
"""Markdown report for a walk-forward run: fold table + honest summary."""

from __future__ import annotations

from pkmn_quant.research.walkforward import WalkForwardResult


def render_markdown(result: WalkForwardResult, strategy_name: str) -> str:
    lines = [
        f"# Walk-forward report: {strategy_name}",
        "",
        "Out-of-sample segments only; the stitched curve is the honest track record.",
        "Note: Sharpe/Sortino are inflated by mark smoothing (thin markets,",
        "carry-forward marks) - compare strategies against each other and the",
        "buy-and-hold benchmark, not against equities numbers.",
        "",
        "## Folds",
        "",
        "| # | IS window | OOS window | params | IS ret | OOS ret |",
        "|---|-----------|------------|--------|--------|---------|",
    ]
    for i, f in enumerate(result.folds):
        lines.append(
            f"| {i} | {f.fold.is_start} .. {f.fold.is_end} "
            f"| {f.fold.oos_start} .. {f.fold.oos_end} "
            f"| {f.params} "
            f"| {f.is_summary['total_return']:.2%} "
            f"| {f.oos_summary['total_return']:.2%} |"
        )
    lines += ["", "## Summary", ""]
    for key, value in result.summary.items():
        lines.append(f"- {key}: {value:.4f}")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run tests, gates, commit** — `git commit -m "feat: strategy registry and walk-forward markdown report"`

---

### Task 9: `pkmn walkforward` CLI

**Files:**
- Modify: `src/pkmn_quant/cli.py`
- Test: `tests/test_cli_walkforward.py`

- [ ] **Step 1: Test** — `tests/test_cli_walkforward.py`:

```python
from datetime import date, timedelta
from pathlib import Path

import polars as pl
from typer.testing import CliRunner

from pkmn_quant.cli import app
from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA
from pkmn_quant.data.warehouse import Warehouse
from tests.helpers import price_row


def seed_forty_days(root: Path) -> None:
    w = Warehouse(Paths(root=root))
    start = date(2025, 1, 1)
    for i in range(40):
        d = start + timedelta(days=i)
        w.write_prices(d, pl.DataFrame([price_row(d, 1, 100.0 + i)], schema=PRICE_SCHEMA))
    w.write_products(pl.DataFrame({
        "product_id": [1], "group_id": [1], "name": ["Box"],
        "rarity": [None], "kind": ["sealed"], "released_on": [start],
    }))


def test_walkforward_cli_runs_and_writes_report(tmp_path: Path) -> None:
    seed_forty_days(tmp_path)
    result = CliRunner().invoke(
        app,
        [
            "walkforward", "--strategy", "sealed-accumulation",
            "--start", "2025-01-01", "--end", "2025-02-09",
            "--is-days", "10", "--oos-days", "10",
            "--trials", "2", "--cash", "1000", "--root", str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    out = tmp_path / "data" / "results"
    run_dir = next(iter(out.iterdir()))
    assert (run_dir / "report.md").exists()
    assert (run_dir / "stitched_equity.parquet").exists()
    assert "overfitting_gap" in (run_dir / "report.md").read_text()


def test_walkforward_unknown_strategy_clean_error(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app, ["walkforward", "--strategy", "nope", "--start", "2025-01-01",
              "--end", "2025-02-09", "--root", str(tmp_path)]
    )
    assert result.exit_code != 0
    assert "nope" in result.output and "Traceback" not in result.output
```

- [ ] **Step 2: Implement** — add to cli.py:

```python
@app.command()
def walkforward(
    strategy: str = typer.Option(..., help="Strategy name: see pkmn_quant.research.registry."),
    start: str = typer.Option(..., help="Range start (YYYY-MM-DD)."),
    end: str = typer.Option(..., help="Range end (YYYY-MM-DD)."),
    is_days: int = typer.Option(180, help="In-sample window length in days."),
    oos_days: int = typer.Option(60, help="Out-of-sample window length in days."),
    trials: int = typer.Option(25, help="Optuna trials per fold."),
    seed: int = typer.Option(42, help="Sampler seed for reproducibility."),
    cash: float = typer.Option(10_000.0, help="Initial cash per fold."),
    root: Path = typer.Option(Path("."), help="Project root holding the data/ directory."),
) -> None:
    """Walk-forward analysis: optimize in-sample, evaluate out-of-sample."""
    from pkmn_quant.data.warehouse import Warehouse
    from pkmn_quant.engine.costs import CostModel
    from pkmn_quant.research.folds import Fold
    from pkmn_quant.research.registry import REGISTRY
    from pkmn_quant.research.report import render_markdown
    from pkmn_quant.research.search import Params, SearchSpec, optimize_params
    from pkmn_quant.research.walkforward import run_walkforward

    entry = REGISTRY.get(strategy)
    if entry is None:
        raise typer.BadParameter(f"unknown strategy {strategy!r}; known: {sorted(REGISTRY)}")
    try:
        start_date = dt.date.fromisoformat(start)
        end_date = dt.date.fromisoformat(end)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    def optimizer(fold: Fold, evaluate: Callable[[Params], float]) -> Params:
        return optimize_params(SearchSpec(space=entry.space, n_trials=trials, seed=seed), evaluate)

    result = run_walkforward(
        warehouse=Warehouse(Paths(root=root)),
        strategy_factory=entry.factory,
        optimizer=optimizer,
        cost_model=CostModel(),
        start=start_date, end=end_date,
        is_days=is_days, oos_days=oos_days, initial_cash=cash,
    )

    run_dir = root / "data" / "results" / f"wf-{strategy}-{start}-{end}"
    if run_dir.exists():
        typer.echo(f"warning: overwriting existing results in {run_dir}", err=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    result.stitched_curve.write_parquet(run_dir / "stitched_equity.parquet")
    (run_dir / "report.md").write_text(render_markdown(result, strategy_name=strategy))

    typer.echo(f"strategy: {strategy}  folds: {len(result.folds)}")
    for key, value in result.summary.items():
        typer.echo(f"{key}: {value:.4f}")
    typer.echo(f"report written to {run_dir / 'report.md'}")
```

Add `from collections.abc import Callable` to cli.py imports if needed.

- [ ] **Step 3: Run tests, gates, commit** — `git commit -m "feat: pkmn walkforward CLI"`

---

### Task 10: Real-data walk-forward runs (manual)

- [ ] **Step 1:** For each of the three strategies, run (expect minutes each — each fold's optuna trials each run a full IS backtest; start with the cheapest settings):

```bash
uv run pkmn walkforward --strategy sealed-accumulation --start 2024-03-01 --end 2026-06-30 --is-days 180 --oos-days 60 --trials 15
uv run pkmn walkforward --strategy dip-buyer          --start 2024-03-01 --end 2026-06-30 --is-days 180 --oos-days 60 --trials 15
uv run pkmn walkforward --strategy xs-momentum        --start 2024-03-01 --end 2026-06-30 --is-days 180 --oos-days 60 --trials 15
```

If a single run exceeds ~30 min, reduce `--trials` and note it. Run the buy-and-hold benchmark over the stitched OOS period for comparison:

```bash
uv run pkmn backtest --start 2024-08-28 --end 2026-06-30 --cash 10000 --kind sealed
```

(2024-08-28 = first OOS day with is_days=180.)

- [ ] **Step 2: Sanity checks** — for each report: fold count ≈ (874 - 180) / 60 ≈ 11; OOS returns should be worse than IS returns on average (positive overfitting_gap is EXPECTED — that's the point of measuring); no fold with absurd OOS return (>10x in 60 days = investigate). Compare each stitched OOS total return against the benchmark's.

- [ ] **Step 3:** Record the honest findings (which strategies beat buy-and-hold OOS, if any; typical gap size) — these go verbatim into the README (Plan 4). Commit any fixes made along the way.

**Done criteria (Plan 3):** all gates green; three strategies with registry entries; walk-forward produces stitched OOS reports on real data; overfitting gap measured and reported; findings recorded for the README.
