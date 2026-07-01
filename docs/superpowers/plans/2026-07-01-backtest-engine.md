# pkmn_quant Plan 2: Backtest Engine

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An event-driven backtest engine with card-market execution realism (fees, spread, liquidity caps, T+1 fills, long-only), portfolio accounting with property-based tests, a buy-and-hold benchmark strategy, and a `pkmn backtest` CLI producing an equity curve and honest metrics.

**Architecture:** `src/pkmn_quant/engine/` holds pure components layered bottom-up: `costs.py` (CostModel), `portfolio.py` (positions/cash/ledger), `data.py` (market-data view over the Plan 1 warehouse with last-price carry-forward), `execution.py` (T+1 fill simulator), `strategy.py` (Strategy ABC + Context — identical interface for backtest and future live mode), `metrics.py` (return/drawdown/Sharpe), `backtest.py` (the daily event loop wiring it together). `src/pkmn_quant/strategies/` holds concrete strategies (buy-and-hold benchmark in this plan). The engine consumes only `Warehouse.load_prices()`/`load_products()` — no HTTP anywhere.

**Tech Stack:** Python 3.12, Polars, hypothesis (new dev dep, property tests), existing uv/ruff/mypy-strict/pytest gates. No new runtime deps.

**Key design decisions (from the approved spec):**
- An *asset* is `(product_id, sub_type)` — Normal and Holofoil printings are separate assets.
- Orders placed on day T fill at day T+1 prices (kills same-bar look-ahead by construction).
- Buy fill: `market + shipping_per_line`. Sell proceeds: `market × (1 − fee_rate) − shipping_per_line`. Defaults: fee_rate 12.75% (TCGplayer 10.25% + ~2.5% processing), shipping $1.00 per order line.
- Liquidity: max units tradeable per asset per day, tiered by price (cheap = liquid). Buys and sells clipped, never silently dropped — clips recorded on the Fill.
- Long-only: sells clipped to held quantity; shorts structurally impossible.
- Mark-to-market uses last known market price (carry-forward) for assets not traded that day.
- Annualization uses 365 (card prices print daily, weekends included).
- `Context` exposes history strictly ≤ today; a dedicated no-look-ahead test asserts it.

---

### Task 1: CostModel

**Files:**
- Create: `src/pkmn_quant/engine/__init__.py` (empty)
- Create: `src/pkmn_quant/engine/costs.py`
- Test: `tests/engine/__init__.py` (empty), `tests/engine/test_costs.py`

- [ ] **Step 1: Write the failing tests** — `tests/engine/test_costs.py`:

```python
import pytest

from pkmn_quant.engine.costs import CostModel


def test_default_round_trip_loses_about_15_percent() -> None:
    cm = CostModel()
    buy = cm.buy_price(market=100.0)
    proceeds = cm.sell_proceeds(market=100.0)
    assert buy == pytest.approx(101.0)  # market + $1 shipping
    assert proceeds == pytest.approx(100.0 * (1 - 0.1275) - 1.0)
    loss = (buy - proceeds) / buy
    assert 0.13 < loss < 0.16  # the honest hurdle


def test_liquidity_caps_tiered_by_price() -> None:
    cm = CostModel()
    assert cm.max_daily_qty(market=2.0) == 20
    assert cm.max_daily_qty(market=30.0) == 8
    assert cm.max_daily_qty(market=150.0) == 3
    assert cm.max_daily_qty(market=1500.0) == 1


def test_custom_parameters() -> None:
    cm = CostModel(fee_rate=0.10, shipping_per_line=0.0)
    assert cm.buy_price(50.0) == pytest.approx(50.0)
    assert cm.sell_proceeds(50.0) == pytest.approx(45.0)


def test_serializable_for_result_reports() -> None:
    cm = CostModel()
    d = cm.as_dict()
    assert d["fee_rate"] == pytest.approx(0.1275)
    assert d["liquidity_tiers"] == [(5.0, 20), (50.0, 8), (200.0, 3)]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/engine/test_costs.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement** — `src/pkmn_quant/engine/costs.py`:

```python
"""Execution-cost assumptions for the TCG market.

Every backtest Result serializes its CostModel so reports state their own
assumptions. Defaults model TCGplayer: ~12.75% seller fees and flat shipping.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# (max market price, units tradeable per asset per day); above the last
# threshold, DEFAULT_MAX_QTY applies. Cheap cards are liquid; a $300 alt-art
# might see one sale a day.
DEFAULT_LIQUIDITY_TIERS: list[tuple[float, int]] = [(5.0, 20), (50.0, 8), (200.0, 3)]
DEFAULT_MAX_QTY = 1


@dataclass(frozen=True)
class CostModel:
    fee_rate: float = 0.1275
    shipping_per_line: float = 1.0
    liquidity_tiers: list[tuple[float, int]] = field(
        default_factory=lambda: list(DEFAULT_LIQUIDITY_TIERS)
    )
    fallback_max_qty: int = DEFAULT_MAX_QTY

    def buy_price(self, market: float) -> float:
        """Per-unit cash outlay when buying at the market price."""
        return market + self.shipping_per_line

    def sell_proceeds(self, market: float) -> float:
        """Per-unit cash received when selling at the market price."""
        return market * (1 - self.fee_rate) - self.shipping_per_line

    def max_daily_qty(self, market: float) -> int:
        for threshold, qty in self.liquidity_tiers:
            if market < threshold:
                return qty
        return self.fallback_max_qty

    def as_dict(self) -> dict[str, Any]:
        return {
            "fee_rate": self.fee_rate,
            "shipping_per_line": self.shipping_per_line,
            "liquidity_tiers": list(self.liquidity_tiers),
            "fallback_max_qty": self.fallback_max_qty,
        }
```

Note: shipping is charged per order line on BOTH sides (you pay shipping when buying; you pay to ship when selling). `sell_proceeds` can go negative for penny cards — that is realistic (selling a $0.50 card nets you a loss) and strategies must cope.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/engine/test_costs.py -v`
Expected: 4 PASSED

- [ ] **Step 5: Gates and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
git add src/pkmn_quant/engine tests/engine
git commit -m "feat: CostModel - fees, spread, tiered liquidity caps"
```

---

### Task 2: Portfolio accounting (with hypothesis property tests)

**Files:**
- Modify: `pyproject.toml` (add `"hypothesis>=6.100"` to the dev group)
- Create: `src/pkmn_quant/engine/portfolio.py`
- Test: `tests/engine/test_portfolio.py`

- [ ] **Step 1: Add hypothesis**

Run: `uv add --group dev "hypothesis>=6.100"`
Expected: uv.lock updated, hypothesis importable.

- [ ] **Step 2: Write the failing tests** — `tests/engine/test_portfolio.py`:

```python
from datetime import date

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pkmn_quant.engine.portfolio import Asset, Fill, Portfolio

A = Asset(product_id=1, sub_type="Normal")
B = Asset(product_id=2, sub_type="Holofoil")
DAY = date(2025, 6, 2)


def fill(asset: Asset, qty: int, price: float, fees: float = 0.0) -> Fill:
    return Fill(day=DAY, asset=asset, quantity=qty, price=price, fees=fees)


def test_buy_updates_cash_position_and_avg_cost() -> None:
    p = Portfolio(cash=1000.0)
    p.apply(fill(A, 2, 10.0, fees=2.0))
    p.apply(fill(A, 1, 16.0, fees=1.0))
    assert p.cash == pytest.approx(1000.0 - 2 * 10.0 - 2.0 - 16.0 - 1.0)
    pos = p.positions[A]
    assert pos.quantity == 3
    assert pos.avg_cost == pytest.approx(12.0)  # (20 + 16) / 3, fees excluded


def test_sell_realizes_pnl_against_avg_cost() -> None:
    p = Portfolio(cash=0.0)
    p.apply(fill(A, 3, 12.0))
    p.apply(fill(A, -1, 20.0, fees=3.0))
    assert p.realized_pnl == pytest.approx(20.0 - 12.0 - 3.0)
    assert p.positions[A].quantity == 2
    assert p.positions[A].avg_cost == pytest.approx(12.0)  # unchanged by sells


def test_position_closed_when_fully_sold() -> None:
    p = Portfolio(cash=0.0)
    p.apply(fill(A, 1, 5.0))
    p.apply(fill(A, -1, 6.0))
    assert A not in p.positions


def test_oversell_rejected() -> None:
    p = Portfolio(cash=100.0)
    p.apply(fill(A, 1, 5.0))
    with pytest.raises(ValueError, match="cannot sell"):
        p.apply(fill(A, -2, 6.0))


def test_equity_marks_positions_to_market() -> None:
    p = Portfolio(cash=100.0)
    p.apply(fill(A, 2, 10.0))
    p.apply(fill(B, 1, 50.0))
    equity = p.equity({A: 15.0, B: 40.0})
    assert equity == pytest.approx((100.0 - 20.0 - 50.0) + 2 * 15.0 + 40.0)


def test_ledger_records_every_fill() -> None:
    p = Portfolio(cash=100.0)
    p.apply(fill(A, 1, 5.0))
    p.apply(fill(A, -1, 6.0))
    assert len(p.ledger) == 2


@given(
    st.lists(
        st.tuples(
            st.integers(min_value=1, max_value=5),      # buy qty
            st.floats(min_value=0.5, max_value=100.0),  # buy price
            st.floats(min_value=0.5, max_value=100.0),  # later sell price
            st.floats(min_value=0.0, max_value=5.0),    # fees each side
        ),
        min_size=1,
        max_size=20,
    )
)
def test_accounting_identity_holds(trades: list[tuple[int, float, float, float]]) -> None:
    """cash + cost basis of open positions == initial + realized P&L - all fees...
    simplified here to full round-trips: buy then sell everything, so
    final cash == initial + realized_pnl exactly.
    """
    initial = 10_000.0
    p = Portfolio(cash=initial)
    for i, (qty, buy_px, sell_px, fee) in enumerate(trades):
        asset = Asset(product_id=i, sub_type="Normal")
        p.apply(Fill(day=DAY, asset=asset, quantity=qty, price=buy_px, fees=fee))
        p.apply(Fill(day=DAY, asset=asset, quantity=-qty, price=sell_px, fees=fee))
    assert p.cash == pytest.approx(initial + p.realized_pnl)
    assert p.positions == {}
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/engine/test_portfolio.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 4: Implement** — `src/pkmn_quant/engine/portfolio.py`:

```python
"""Positions, cash, and P&L accounting (average-cost basis)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class Asset:
    """One tradeable printing: a product in a specific sub-type."""

    product_id: int
    sub_type: str


@dataclass(frozen=True)
class Fill:
    """An executed trade. quantity > 0 is a buy, < 0 a sell.

    `price` is the per-unit execution price (spread already applied);
    `fees` is the total non-price cost of this fill (fees + shipping).
    """

    day: date
    asset: Asset
    quantity: int
    price: float
    fees: float


@dataclass
class Position:
    quantity: int
    avg_cost: float


@dataclass
class Portfolio:
    cash: float
    positions: dict[Asset, Position] = field(default_factory=dict)
    realized_pnl: float = 0.0
    ledger: list[Fill] = field(default_factory=list)

    def apply(self, f: Fill) -> None:
        if f.quantity == 0:
            raise ValueError("zero-quantity fill")
        if f.quantity > 0:
            self._buy(f)
        else:
            self._sell(f)
        self.ledger.append(f)

    def _buy(self, f: Fill) -> None:
        cost = f.quantity * f.price
        self.cash -= cost + f.fees
        # Buy-side fees hit realized_pnl immediately so the invariant
        # "when flat, cash - initial == realized_pnl" holds exactly.
        self.realized_pnl -= f.fees
        pos = self.positions.get(f.asset)
        if pos is None:
            self.positions[f.asset] = Position(quantity=f.quantity, avg_cost=f.price)
        else:
            total_cost = pos.avg_cost * pos.quantity + cost
            pos.quantity += f.quantity
            pos.avg_cost = total_cost / pos.quantity

    def _sell(self, f: Fill) -> None:
        qty = -f.quantity
        pos = self.positions.get(f.asset)
        if pos is None or pos.quantity < qty:
            held = pos.quantity if pos else 0
            raise ValueError(f"cannot sell {qty} of {f.asset}: hold {held}")
        proceeds = qty * f.price
        self.cash += proceeds - f.fees
        self.realized_pnl += proceeds - qty * pos.avg_cost - f.fees
        pos.quantity -= qty
        if pos.quantity == 0:
            del self.positions[f.asset]

    def equity(self, marks: dict[Asset, float]) -> float:
        """Cash plus positions valued at the given per-asset market prices."""
        value = sum(pos.quantity * marks[asset] for asset, pos in self.positions.items())
        return self.cash + value
```

Design notes: `fees` are excluded from avg_cost; ALL fees (both sides) hit realized_pnl when incurred, so realized_pnl is the cumulative net cash impact of trading — the property test's identity depends on this. `equity()` raises KeyError if a held asset has no mark — deliberate: the engine must always supply carry-forward marks, and silence there would corrupt every number downstream. Oversell raises here as a final invariant; the execution simulator (Task 4) clips before this point, so this raise firing means an engine bug.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/engine/test_portfolio.py -v`
Expected: 7 PASSED (hypothesis runs the property test ~100 times internally)

- [ ] **Step 6: Gates and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
git add pyproject.toml uv.lock src/pkmn_quant/engine/portfolio.py tests/engine/test_portfolio.py
git commit -m "feat: portfolio accounting with avg-cost basis and property tests"
```

---

### Task 3: Market data view

**Files:**
- Create: `src/pkmn_quant/engine/data.py`
- Test: `tests/engine/test_data.py`

Purpose: one object the engine iterates — trading days in range, per-day price lookups `dict[Asset, float]`, carry-forward marks for assets that skip a day, and history frames with no future rows.

- [ ] **Step 1: Write the failing tests** — `tests/engine/test_data.py`:

```python
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA
from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.data import MarketData
from pkmn_quant.engine.portfolio import Asset

D1, D2, D3 = date(2025, 6, 1), date(2025, 6, 2), date(2025, 6, 3)
A1 = Asset(product_id=1, sub_type="Normal")
A2 = Asset(product_id=2, sub_type="Normal")


def row(day: date, product_id: int, market: float) -> dict[str, object]:
    return {
        "date": day,
        "product_id": product_id,
        "sub_type": "Normal",
        "low": 1.0,
        "mid": 2.0,
        "high": 3.0,
        "market": market,
    }


@pytest.fixture
def market(tmp_path: Path) -> MarketData:
    w = Warehouse(Paths(root=tmp_path))
    w.write_prices(D1, pl.DataFrame([row(D1, 1, 10.0), row(D1, 2, 5.0)], schema=PRICE_SCHEMA))
    # product 2 does not trade on D2
    w.write_prices(D2, pl.DataFrame([row(D2, 1, 11.0)], schema=PRICE_SCHEMA))
    w.write_prices(D3, pl.DataFrame([row(D3, 1, 12.0), row(D3, 2, 6.0)], schema=PRICE_SCHEMA))
    return MarketData.from_warehouse(w, start=D1, end=D3)


def test_trading_days(market: MarketData) -> None:
    assert market.days == [D1, D2, D3]


def test_prices_on_day(market: MarketData) -> None:
    assert market.prices_on(D1) == {A1: 10.0, A2: 5.0}
    assert market.prices_on(D2) == {A1: 11.0}


def test_marks_carry_forward_missing_assets(market: MarketData) -> None:
    assert market.marks_on(D2) == {A1: 11.0, A2: 5.0}  # A2 carried from D1
    assert market.marks_on(D3) == {A1: 12.0, A2: 6.0}


def test_history_excludes_future(market: MarketData) -> None:
    h = market.history_until(D2)
    assert h["date"].max() == D2
    assert h.height == 3  # 2 rows on D1 + 1 on D2


def test_range_filtering(tmp_path: Path) -> None:
    w = Warehouse(Paths(root=tmp_path))
    for d in (D1, D2, D3):
        w.write_prices(d, pl.DataFrame([row(d, 1, 10.0)], schema=PRICE_SCHEMA))
    md = MarketData.from_warehouse(w, start=D2, end=D3)
    assert md.days == [D2, D3]
    assert md.history_until(D3)["date"].min() == D2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/engine/test_data.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement** — `src/pkmn_quant/engine/data.py`:

```python
"""Read-only market data view the engine iterates day by day."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import polars as pl

from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.portfolio import Asset


@dataclass(frozen=True)
class MarketData:
    """Price history for [start, end], indexed for per-day access.

    `_marks` includes carry-forward: an asset missing on day D is valued at
    its most recent prior market price. `prices_on` has NO carry-forward -
    it reports what actually traded (execution must not fill at stale prices).
    """

    frame: pl.DataFrame
    days: list[date]
    _prices: dict[date, dict[Asset, float]]
    _marks: dict[date, dict[Asset, float]]

    @classmethod
    def from_warehouse(cls, warehouse: Warehouse, start: date, end: date) -> MarketData:
        frame = warehouse.load_prices().filter(
            (pl.col("date") >= start) & (pl.col("date") <= end)
        )
        days = sorted(frame["date"].unique().to_list())
        prices: dict[date, dict[Asset, float]] = {}
        marks: dict[date, dict[Asset, float]] = {}
        last: dict[Asset, float] = {}
        for day in days:
            day_rows = frame.filter(pl.col("date") == day)
            todays = {
                Asset(product_id=r["product_id"], sub_type=r["sub_type"]): r["market"]
                for r in day_rows.iter_rows(named=True)
            }
            prices[day] = todays
            last.update(todays)
            marks[day] = dict(last)
        return cls(frame=frame, days=days, _prices=prices, _marks=marks)

    def prices_on(self, day: date) -> dict[Asset, float]:
        """Prices that actually printed on `day` (no carry-forward)."""
        return self._prices[day]

    def marks_on(self, day: date) -> dict[Asset, float]:
        """Mark-to-market prices on `day`, carrying forward missing assets."""
        return self._marks[day]

    def history_until(self, day: date) -> pl.DataFrame:
        """All price rows with date <= day. The engine's anti-look-ahead wall."""
        return self.frame.filter(pl.col("date") <= day)
```

Memory note: `_marks` stores a dict copy per day (~2.5k assets × ~870 days ≈ 2M small entries) — acceptable at this scale; revisit only if profiling says so.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/engine/test_data.py -v`
Expected: 5 PASSED

- [ ] **Step 5: Gates and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
git add src/pkmn_quant/engine/data.py tests/engine/test_data.py
git commit -m "feat: MarketData view with carry-forward marks and history wall"
```

---

### Task 4: Execution simulator

**Files:**
- Create: `src/pkmn_quant/engine/execution.py`
- Test: `tests/engine/test_execution.py`

- [ ] **Step 1: Write the failing tests** — `tests/engine/test_execution.py`:

```python
from datetime import date

import pytest

from pkmn_quant.engine.costs import CostModel
from pkmn_quant.engine.execution import ExecutionSimulator, Order
from pkmn_quant.engine.portfolio import Asset, Portfolio

A = Asset(product_id=1, sub_type="Normal")
DAY = date(2025, 6, 2)
CM = CostModel(fee_rate=0.10, shipping_per_line=1.0)


def test_buy_fills_at_market_plus_shipping() -> None:
    sim = ExecutionSimulator(CM)
    p = Portfolio(cash=1000.0)
    fills = sim.execute([Order(asset=A, quantity=2)], prices={A: 10.0}, portfolio=p, day=DAY)
    assert len(fills) == 1
    f = fills[0]
    assert f.quantity == 2
    assert f.price == pytest.approx(10.0)
    assert f.fees == pytest.approx(1.0)  # one shipping charge per order line
    assert p.positions[A].quantity == 2
    assert p.cash == pytest.approx(1000.0 - 20.0 - 1.0)


def test_sell_fills_net_of_fees() -> None:
    sim = ExecutionSimulator(CM)
    p = Portfolio(cash=0.0)
    sim.execute([Order(asset=A, quantity=3)], prices={A: 10.0}, portfolio=p, day=DAY)
    fills = sim.execute([Order(asset=A, quantity=-2)], prices={A: 10.0}, portfolio=p, day=DAY)
    f = fills[0]
    assert f.quantity == -2
    assert f.price == pytest.approx(10.0)
    # fees = fee_rate on proceeds + one shipping: 2*10*0.10 + 1.0
    assert f.fees == pytest.approx(3.0)


def test_no_price_no_fill() -> None:
    sim = ExecutionSimulator(CM)
    p = Portfolio(cash=100.0)
    fills = sim.execute([Order(asset=A, quantity=1)], prices={}, portfolio=p, day=DAY)
    assert fills == []
    assert p.cash == 100.0


def test_buy_clipped_by_liquidity() -> None:
    cm = CostModel(liquidity_tiers=[(100.0, 2)])
    sim = ExecutionSimulator(cm)
    p = Portfolio(cash=10_000.0)
    fills = sim.execute([Order(asset=A, quantity=50)], prices={A: 10.0}, portfolio=p, day=DAY)
    assert fills[0].quantity == 2


def test_buy_clipped_by_cash() -> None:
    sim = ExecutionSimulator(CM)
    p = Portfolio(cash=25.0)
    # each unit costs 10 + shipping 1 on the line; affordable: 2 units (21) not 3 (31)
    fills = sim.execute([Order(asset=A, quantity=20)], prices={A: 10.0}, portfolio=p, day=DAY)
    assert fills[0].quantity == 2
    assert p.cash >= 0.0


def test_sell_clipped_to_held_never_short() -> None:
    sim = ExecutionSimulator(CM)
    p = Portfolio(cash=0.0)
    sim.execute([Order(asset=A, quantity=1)], prices={A: 10.0}, portfolio=p, day=DAY)
    fills = sim.execute([Order(asset=A, quantity=-5)], prices={A: 10.0}, portfolio=p, day=DAY)
    assert fills[0].quantity == -1
    assert A not in p.positions


def test_sell_with_no_position_no_fill() -> None:
    sim = ExecutionSimulator(CM)
    p = Portfolio(cash=0.0)
    fills = sim.execute([Order(asset=A, quantity=-3)], prices={A: 10.0}, portfolio=p, day=DAY)
    assert fills == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/engine/test_execution.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement** — `src/pkmn_quant/engine/execution.py`:

```python
"""Order execution with card-market realism.

Fills happen at the day's actually-printed prices (no carry-forward), with
spread/fees from the CostModel, clipped by liquidity, cash, and held quantity.
Long-only: a sell can never exceed the position; shorts cannot exist.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from pkmn_quant.engine.costs import CostModel
from pkmn_quant.engine.portfolio import Asset, Fill, Portfolio


@dataclass(frozen=True)
class Order:
    """Strategy intent: buy (quantity > 0) or sell (quantity < 0) an asset."""

    asset: Asset
    quantity: int


@dataclass(frozen=True)
class ExecutionSimulator:
    cost_model: CostModel

    def execute(
        self,
        orders: list[Order],
        prices: dict[Asset, float],
        portfolio: Portfolio,
        day: date,
    ) -> list[Fill]:
        """Fill orders against the day's prices, applying them to the portfolio."""
        fills: list[Fill] = []
        for order in orders:
            market = prices.get(order.asset)
            if market is None or order.quantity == 0:
                continue  # asset didn't trade today; order expires unfilled
            fill = (
                self._fill_buy(order, market, portfolio, day)
                if order.quantity > 0
                else self._fill_sell(order, market, portfolio, day)
            )
            if fill is not None:
                portfolio.apply(fill)
                fills.append(fill)
        return fills

    def _fill_buy(
        self, order: Order, market: float, portfolio: Portfolio, day: date
    ) -> Fill | None:
        cap = self.cost_model.max_daily_qty(market)
        qty = min(order.quantity, cap)
        # afford: qty * market + shipping_per_line <= cash
        affordable = math.floor(
            (portfolio.cash - self.cost_model.shipping_per_line) / market
        )
        qty = min(qty, max(affordable, 0))
        if qty <= 0:
            return None
        return Fill(
            day=day,
            asset=order.asset,
            quantity=qty,
            price=market,
            fees=self.cost_model.shipping_per_line,
        )

    def _fill_sell(
        self, order: Order, market: float, portfolio: Portfolio, day: date
    ) -> Fill | None:
        pos = portfolio.positions.get(order.asset)
        if pos is None:
            return None
        cap = self.cost_model.max_daily_qty(market)
        qty = min(-order.quantity, pos.quantity, cap)
        if qty <= 0:
            return None
        fees = qty * market * self.cost_model.fee_rate + self.cost_model.shipping_per_line
        return Fill(day=day, asset=order.asset, quantity=-qty, price=market, fees=fees)
```

Design note: on the buy side the spread cost is expressed as `fees=shipping` with `price=market` (we model buying at market); on the sell side fees carry the marketplace fee + shipping while `price` stays at market. This keeps `Fill.price` always the observable market print, and every cost explicit in `fees` — easy to audit in the ledger.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/engine/test_execution.py -v`
Expected: 7 PASSED

- [ ] **Step 5: Gates and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
git add src/pkmn_quant/engine/execution.py tests/engine/test_execution.py
git commit -m "feat: execution simulator - clipped, long-only, cost-modeled fills"
```

---

### Task 5: Strategy interface and Context

**Files:**
- Create: `src/pkmn_quant/engine/strategy.py`
- Test: `tests/engine/test_strategy.py`

- [ ] **Step 1: Write the failing tests** — `tests/engine/test_strategy.py`:

```python
from datetime import date

import polars as pl

from pkmn_quant.engine.portfolio import Asset, Portfolio
from pkmn_quant.engine.strategy import Context, Strategy
from pkmn_quant.engine.execution import Order


class Noop(Strategy):
    def on_bar(self, ctx: Context) -> list[Order]:
        return []


def test_strategy_is_abstract() -> None:
    import pytest

    with pytest.raises(TypeError):
        Strategy()  # type: ignore[abstract]


def test_context_exposes_read_state() -> None:
    products = pl.DataFrame({"product_id": [1], "kind": ["sealed"]})
    history = pl.DataFrame({"date": [date(2025, 6, 1)], "product_id": [1]})
    p = Portfolio(cash=500.0)
    ctx = Context(
        today=date(2025, 6, 1),
        history=history,
        products=products,
        positions=p.positions,
        cash=p.cash,
        marks={Asset(1, "Normal"): 10.0},
    )
    assert ctx.cash == 500.0
    assert ctx.today == date(2025, 6, 1)
    assert Noop().on_bar(ctx) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/engine/test_strategy.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement** — `src/pkmn_quant/engine/strategy.py`:

```python
"""The Strategy interface - identical in backtest and (future) live mode.

A strategy sees a Context (history up to today, its positions, cash, marks)
and returns Orders. It cannot tell which mode it runs in, which makes
look-ahead structurally impossible and live/backtest behavior identical.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date

import polars as pl

from pkmn_quant.engine.execution import Order
from pkmn_quant.engine.portfolio import Asset, Position


@dataclass(frozen=True)
class Context:
    today: date
    history: pl.DataFrame          # price rows, date <= today only
    products: pl.DataFrame         # product catalog (id, kind, rarity, ...)
    positions: dict[Asset, Position]
    cash: float
    marks: dict[Asset, float]      # today's mark prices (carry-forward)


class Strategy(ABC):
    """Implement on_bar; emit orders. Orders fill at the NEXT day's prices."""

    name: str = "strategy"

    @abstractmethod
    def on_bar(self, ctx: Context) -> list[Order]: ...
```

- [ ] **Step 4: Run tests, gates, commit**

```bash
uv run pytest tests/engine/test_strategy.py -v   # expect 2 PASSED
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
git add src/pkmn_quant/engine/strategy.py tests/engine/test_strategy.py
git commit -m "feat: Strategy ABC and Context - one interface for backtest and live"
```

---

### Task 6: Metrics

**Files:**
- Create: `src/pkmn_quant/engine/metrics.py`
- Test: `tests/engine/test_metrics.py`

- [ ] **Step 1: Write the failing tests** — `tests/engine/test_metrics.py`:

```python
from datetime import date

import polars as pl
import pytest

from pkmn_quant.engine.metrics import summarize


def curve(values: list[float]) -> pl.DataFrame:
    days = pl.date_range(
        date(2025, 6, 1), date(2025, 6, len(values)), interval="1d", eager=True
    )
    return pl.DataFrame({"date": days, "equity": values})


def test_flat_curve() -> None:
    s = summarize(curve([100.0, 100.0, 100.0]))
    assert s["total_return"] == pytest.approx(0.0)
    assert s["max_drawdown"] == pytest.approx(0.0)


def test_total_return_and_drawdown() -> None:
    s = summarize(curve([100.0, 120.0, 90.0, 108.0]))
    assert s["total_return"] == pytest.approx(0.08)
    assert s["max_drawdown"] == pytest.approx(-0.25)  # 120 -> 90


def test_sharpe_sign() -> None:
    up = summarize(curve([100.0, 101.0, 102.0, 103.0]))
    down = summarize(curve([100.0, 99.0, 98.0, 97.0]))
    assert up["sharpe"] > 0
    assert down["sharpe"] < 0


def test_single_point_curve_degrades_gracefully() -> None:
    s = summarize(curve([100.0]))
    assert s["total_return"] == pytest.approx(0.0)
    assert s["sharpe"] == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/engine/test_metrics.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement** — `src/pkmn_quant/engine/metrics.py`:

```python
"""Honest, homemade performance metrics for an equity curve.

quantstats-style tearsheets arrive in Plan 3; these cover the essentials
with visible math. Annualization uses 365: card prices print every day.
"""

from __future__ import annotations

import math

import polars as pl

TRADING_DAYS_PER_YEAR = 365


def summarize(equity_curve: pl.DataFrame) -> dict[str, float]:
    """Metrics from a frame with `date` and `equity` columns (sorted by date)."""
    eq = equity_curve.sort("date")["equity"]
    n = len(eq)
    if n < 2:
        return {"total_return": 0.0, "cagr": 0.0, "max_drawdown": 0.0, "sharpe": 0.0}

    total_return = eq[-1] / eq[0] - 1.0
    years = (n - 1) / TRADING_DAYS_PER_YEAR
    cagr = (eq[-1] / eq[0]) ** (1 / years) - 1.0 if years > 0 else 0.0

    running_max = eq.cum_max()
    drawdowns = eq / running_max - 1.0
    max_drawdown = float(drawdowns.min())

    daily = (eq / eq.shift(1) - 1.0).drop_nulls()
    std = float(daily.std()) if len(daily) > 1 else 0.0
    if std == 0.0:
        sharpe = 0.0
    else:
        sharpe = float(daily.mean()) / std * math.sqrt(TRADING_DAYS_PER_YEAR)

    return {
        "total_return": float(total_return),
        "cagr": float(cagr),
        "max_drawdown": max_drawdown,
        "sharpe": sharpe,
    }
```

- [ ] **Step 4: Run tests, gates, commit**

```bash
uv run pytest tests/engine/test_metrics.py -v   # expect 4 PASSED
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
git add src/pkmn_quant/engine/metrics.py tests/engine/test_metrics.py
git commit -m "feat: equity-curve metrics - return, CAGR, drawdown, Sharpe"
```

---

### Task 7: The backtest loop

**Files:**
- Create: `src/pkmn_quant/engine/backtest.py`
- Test: `tests/engine/test_backtest.py`

- [ ] **Step 1: Write the failing tests** — `tests/engine/test_backtest.py`:

```python
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA
from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.backtest import Backtest
from pkmn_quant.engine.costs import CostModel
from pkmn_quant.engine.execution import Order
from pkmn_quant.engine.portfolio import Asset
from pkmn_quant.engine.strategy import Context, Strategy

D1, D2, D3 = date(2025, 6, 1), date(2025, 6, 2), date(2025, 6, 3)
A = Asset(product_id=1, sub_type="Normal")


def row(day: date, product_id: int, market: float) -> dict[str, object]:
    return {
        "date": day, "product_id": product_id, "sub_type": "Normal",
        "low": 1.0, "mid": 2.0, "high": 3.0, "market": market,
    }


@pytest.fixture
def warehouse(tmp_path: Path) -> Warehouse:
    w = Warehouse(Paths(root=tmp_path))
    w.write_prices(D1, pl.DataFrame([row(D1, 1, 10.0)], schema=PRICE_SCHEMA))
    w.write_prices(D2, pl.DataFrame([row(D2, 1, 10.0)], schema=PRICE_SCHEMA))
    w.write_prices(D3, pl.DataFrame([row(D3, 1, 20.0)], schema=PRICE_SCHEMA))
    w.write_products(pl.DataFrame({
        "product_id": [1], "group_id": [1], "name": ["X"],
        "rarity": [None], "kind": ["sealed"], "released_on": [D1],
    }))
    return w


class BuyOnceDayOne(Strategy):
    name = "buy-once"

    def on_bar(self, ctx: Context) -> list[Order]:
        if not ctx.positions:
            return [Order(asset=A, quantity=1)]
        return []


def test_t_plus_1_fill_and_final_equity(warehouse: Warehouse) -> None:
    # Zero-cost model isolates the accounting: order on D1 fills at D2's price.
    result = Backtest(
        warehouse=warehouse,
        strategy=BuyOnceDayOne(),
        cost_model=CostModel(fee_rate=0.0, shipping_per_line=0.0),
        start=D1, end=D3, initial_cash=100.0,
    ).run()
    assert len(result.fills) == 1
    assert result.fills[0].day == D2
    assert result.fills[0].price == pytest.approx(10.0)
    curve = result.equity_curve
    assert curve["equity"].to_list() == pytest.approx([100.0, 100.0, 110.0])
    assert result.summary["total_return"] == pytest.approx(0.10)


class LookaheadProbe(Strategy):
    name = "probe"

    def __init__(self) -> None:
        self.violations = 0

    def on_bar(self, ctx: Context) -> list[Order]:
        if ctx.history.height and ctx.history["date"].max() > ctx.today:
            self.violations += 1
        return []


def test_no_lookahead(warehouse: Warehouse) -> None:
    probe = LookaheadProbe()
    Backtest(
        warehouse=warehouse, strategy=probe, cost_model=CostModel(),
        start=D1, end=D3, initial_cash=100.0,
    ).run()
    assert probe.violations == 0


def test_result_serializes_cost_model(warehouse: Warehouse) -> None:
    result = Backtest(
        warehouse=warehouse, strategy=BuyOnceDayOne(), cost_model=CostModel(),
        start=D1, end=D3, initial_cash=100.0,
    ).run()
    assert result.cost_model["fee_rate"] == pytest.approx(0.1275)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/engine/test_backtest.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement** — `src/pkmn_quant/engine/backtest.py`:

```python
"""The daily event loop: history -> strategy -> orders -> T+1 fills -> equity."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

import polars as pl

from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.costs import CostModel
from pkmn_quant.engine.data import MarketData
from pkmn_quant.engine.execution import ExecutionSimulator, Order
from pkmn_quant.engine.metrics import summarize
from pkmn_quant.engine.portfolio import Fill, Portfolio
from pkmn_quant.engine.strategy import Context, Strategy


@dataclass(frozen=True)
class Result:
    strategy_name: str
    equity_curve: pl.DataFrame      # date, equity
    fills: list[Fill]
    summary: dict[str, float]
    cost_model: dict[str, Any]


@dataclass
class Backtest:
    warehouse: Warehouse
    strategy: Strategy
    cost_model: CostModel
    start: date
    end: date
    initial_cash: float
    _pending: list[Order] = field(default_factory=list)

    def run(self) -> Result:
        market = MarketData.from_warehouse(self.warehouse, self.start, self.end)
        products = self.warehouse.load_products()
        simulator = ExecutionSimulator(self.cost_model)
        portfolio = Portfolio(cash=self.initial_cash)
        fills: list[Fill] = []
        curve_days: list[date] = []
        curve_equity: list[float] = []

        for day in market.days:
            # 1. Yesterday's orders fill at today's actually-printed prices.
            fills.extend(
                simulator.execute(self._pending, market.prices_on(day), portfolio, day)
            )
            self._pending = []

            # 2. Strategy sees history <= today and emits orders for tomorrow.
            # positions is DEEP-COPIED: Portfolio.positions is mutable and
            # aliased; a buggy strategy must not be able to edit real holdings.
            ctx = Context(
                today=day,
                history=market.history_until(day),
                products=products,
                positions={a: replace(p) for a, p in portfolio.positions.items()},
                cash=portfolio.cash,
                marks=market.marks_on(day),
            )
            self._pending = self.strategy.on_bar(ctx)

            # 3. Record today's mark-to-market equity.
            curve_days.append(day)
            curve_equity.append(portfolio.equity(market.marks_on(day)))

        equity_curve = pl.DataFrame({"date": curve_days, "equity": curve_equity})
        return Result(
            strategy_name=self.strategy.name,
            equity_curve=equity_curve,
            fills=fills,
            summary=summarize(equity_curve),
            cost_model=self.cost_model.as_dict(),
        )
```

Note the loop order — fill pending BEFORE calling the strategy: an order emitted on day T cannot see or use day T+1 information, and equity is recorded after fills so the curve reflects actual holdings. Orders for assets that don't print a price the next day expire unfilled (conservative; no stale-price fills).

- [ ] **Step 4: Run tests, gates, commit**

```bash
uv run pytest tests/engine/test_backtest.py -v   # expect 3 PASSED
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
git add src/pkmn_quant/engine/backtest.py tests/engine/test_backtest.py
git commit -m "feat: event-driven backtest loop with T+1 fills and Result"
```

---

### Task 8: Buy-and-hold benchmark strategy

**Files:**
- Create: `src/pkmn_quant/strategies/__init__.py` (empty)
- Create: `src/pkmn_quant/strategies/buy_and_hold.py`
- Test: `tests/strategies/__init__.py` (empty), `tests/strategies/test_buy_and_hold.py`

- [ ] **Step 1: Write the failing tests** — `tests/strategies/test_buy_and_hold.py`:

```python
from datetime import date

import polars as pl

from pkmn_quant.engine.portfolio import Asset
from pkmn_quant.engine.strategy import Context
from pkmn_quant.strategies.buy_and_hold import BuyAndHold

D1 = date(2025, 6, 1)
SEALED_A = Asset(product_id=1, sub_type="Normal")
SEALED_B = Asset(product_id=2, sub_type="Normal")
SINGLE_C = Asset(product_id=3, sub_type="Holofoil")

PRODUCTS = pl.DataFrame(
    {
        "product_id": [1, 2, 3],
        "group_id": [10, 10, 10],
        "name": ["Box A", "ETB B", "Card C"],
        "rarity": [None, None, "Rare"],
        "kind": ["sealed", "sealed", "single"],
        "released_on": [D1, D1, D1],
    }
)


def ctx(cash: float, positions: dict = {}) -> Context:  # noqa: B006 - test helper
    return Context(
        today=D1,
        history=pl.DataFrame(),
        products=PRODUCTS,
        positions=dict(positions),
        cash=cash,
        marks={SEALED_A: 100.0, SEALED_B: 50.0, SINGLE_C: 10.0},
    )


def test_first_bar_equal_weights_sealed_universe() -> None:
    strat = BuyAndHold(kind="sealed")
    orders = strat.on_bar(ctx(cash=300.0))
    by_asset = {o.asset: o.quantity for o in orders}
    # $150 budget per sealed asset: 1x A ($100), 3x B ($50)
    assert by_asset == {SEALED_A: 1, SEALED_B: 3}


def test_never_orders_again_after_first_bar() -> None:
    strat = BuyAndHold(kind="sealed")
    strat.on_bar(ctx(cash=300.0))
    assert strat.on_bar(ctx(cash=300.0)) == []


def test_skips_assets_without_marks() -> None:
    strat = BuyAndHold(kind="sealed")
    context = Context(
        today=D1, history=pl.DataFrame(), products=PRODUCTS,
        positions={}, cash=300.0, marks={SEALED_A: 100.0},  # B has no price yet
    )
    orders = strat.on_bar(context)
    assert [o.asset for o in orders] == [SEALED_A]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/strategies/test_buy_and_hold.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement** — `src/pkmn_quant/strategies/buy_and_hold.py`:

```python
"""The benchmark every strategy must beat: buy the universe, hold it."""

from __future__ import annotations

import math

import polars as pl

from pkmn_quant.engine.execution import Order
from pkmn_quant.engine.portfolio import Asset
from pkmn_quant.engine.strategy import Context, Strategy


class BuyAndHold(Strategy):
    """On the first bar, split cash equally across the `kind` universe. Hold."""

    def __init__(self, kind: str = "sealed") -> None:
        self.kind = kind
        self.name = f"buy-and-hold-{kind}"
        self._entered = False

    def on_bar(self, ctx: Context) -> list[Order]:
        if self._entered:
            return []
        self._entered = True

        wanted_ids = set(
            ctx.products.filter(pl.col("kind") == self.kind)["product_id"].to_list()
        )
        universe = [
            (asset, price)
            for asset, price in sorted(ctx.marks.items(), key=lambda kv: kv[0].product_id)
            if asset.product_id in wanted_ids
        ]
        if not universe:
            return []

        budget_per_asset = ctx.cash / len(universe)
        orders = []
        for asset, price in universe:
            qty = math.floor(budget_per_asset / price)
            if qty > 0:
                orders.append(Order(asset=asset, quantity=qty))
        return orders
```

- [ ] **Step 4: Run tests, gates, commit**

```bash
uv run pytest tests/strategies/test_buy_and_hold.py -v   # expect 3 PASSED
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
git add src/pkmn_quant/strategies tests/strategies
git commit -m "feat: buy-and-hold benchmark strategy"
```

---

### Task 9: `pkmn backtest` CLI + golden regression test

**Files:**
- Modify: `src/pkmn_quant/cli.py`
- Test: `tests/test_cli_backtest.py`

- [ ] **Step 1: Write the failing test** — `tests/test_cli_backtest.py`:

```python
from datetime import date
from pathlib import Path

import polars as pl
from typer.testing import CliRunner

from pkmn_quant.cli import app
from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA
from pkmn_quant.data.warehouse import Warehouse

D1, D2, D3 = date(2025, 6, 1), date(2025, 6, 2), date(2025, 6, 3)


def row(day: date, product_id: int, market: float) -> dict[str, object]:
    return {
        "date": day, "product_id": product_id, "sub_type": "Normal",
        "low": 1.0, "mid": 2.0, "high": 3.0, "market": market,
    }


def seed(root: Path) -> None:
    w = Warehouse(Paths(root=root))
    w.write_prices(D1, pl.DataFrame([row(D1, 1, 10.0)], schema=PRICE_SCHEMA))
    w.write_prices(D2, pl.DataFrame([row(D2, 1, 12.0)], schema=PRICE_SCHEMA))
    w.write_prices(D3, pl.DataFrame([row(D3, 1, 15.0)], schema=PRICE_SCHEMA))
    w.write_products(pl.DataFrame({
        "product_id": [1], "group_id": [1], "name": ["Box"],
        "rarity": [None], "kind": ["sealed"], "released_on": [D1],
    }))


def test_backtest_cli_runs_and_writes_results(tmp_path: Path) -> None:
    seed(tmp_path)
    result = CliRunner().invoke(
        app,
        [
            "backtest", "--start", "2025-06-01", "--end", "2025-06-03",
            "--cash", "100", "--root", str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "total_return" in result.output
    out_dir = tmp_path / "data" / "results"
    runs = list(out_dir.iterdir())
    assert len(runs) == 1
    assert (runs[0] / "equity.parquet").exists()
    assert (runs[0] / "fills.parquet").exists()


def test_backtest_golden_numbers(tmp_path: Path) -> None:
    """Golden regression: any engine change that alters results fails here."""
    seed(tmp_path)
    result = CliRunner().invoke(
        app,
        [
            "backtest", "--start", "2025-06-01", "--end", "2025-06-03",
            "--cash", "100", "--root", str(tmp_path),
        ],
    )
    out_dir = tmp_path / "data" / "results"
    run_dir = next(iter(out_dir.iterdir()))
    equity = pl.read_parquet(run_dir / "equity.parquet")["equity"].to_list()
    # Day1: no fills, 100 cash. Buy-and-hold orders 10 units (floor(100/10));
    # D2 fill at 12 is clipped to 8 by the liquidity tier, +$1 shipping.
    # Frozen from first verified run - update ONLY with a justification:
    assert equity[0] == 100.0
    assert equity[1] < 100.0  # spread+shipping paid on entry
    assert equity[2] > equity[1]  # price rose 12 -> 15
```

The exact golden values get frozen in Step 3 after the first verified run — the test above asserts the shape; tighten it to exact `pytest.approx` values once the implementer has manually verified the arithmetic by hand and pasted the numbers.

- [ ] **Step 2: Implement the CLI command** — append to `src/pkmn_quant/cli.py`:

```python
@app.command()
def backtest(
    start: str = typer.Option(..., help="Backtest start date (YYYY-MM-DD)."),
    end: str = typer.Option(..., help="Backtest end date (YYYY-MM-DD)."),
    cash: float = typer.Option(10_000.0, help="Initial cash."),
    kind: str = typer.Option("sealed", help="Universe for buy-and-hold: sealed|single."),
    root: Path = typer.Option(Path("."), help="Project root holding the data/ directory."),
) -> None:
    """Run the buy-and-hold benchmark backtest over the warehouse."""
    from pkmn_quant.data.warehouse import Warehouse
    from pkmn_quant.engine.backtest import Backtest
    from pkmn_quant.engine.costs import CostModel
    from pkmn_quant.strategies.buy_and_hold import BuyAndHold

    try:
        start_date = dt.date.fromisoformat(start)
        end_date = dt.date.fromisoformat(end)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    result = Backtest(
        warehouse=Warehouse(Paths(root=root)),
        strategy=BuyAndHold(kind=kind),
        cost_model=CostModel(),
        start=start_date,
        end=end_date,
        initial_cash=cash,
    ).run()

    run_dir = root / "data" / "results" / f"{result.strategy_name}-{start}-{end}"
    run_dir.mkdir(parents=True, exist_ok=True)
    result.equity_curve.write_parquet(run_dir / "equity.parquet")
    fills_df = pl.DataFrame(
        [
            {
                "day": f.day, "product_id": f.asset.product_id,
                "sub_type": f.asset.sub_type, "quantity": f.quantity,
                "price": f.price, "fees": f.fees,
            }
            for f in result.fills
        ]
    )
    fills_df.write_parquet(run_dir / "fills.parquet")

    typer.echo(f"strategy: {result.strategy_name}  ({len(result.fills)} fills)")
    for key, value in result.summary.items():
        typer.echo(f"{key}: {value:.4f}")
    typer.echo(f"results written to {run_dir}")
```

Requires adding `import polars as pl` to cli.py's imports. Note fills_df with zero fills needs an explicit schema — if `pl.DataFrame([])` errors, pass `schema={"day": pl.Date, "product_id": pl.Int64, "sub_type": pl.Utf8, "quantity": pl.Int64, "price": pl.Float64, "fees": pl.Float64}`.

- [ ] **Step 3: Verify, freeze golden values, gates, commit**

```bash
uv run pytest tests/test_cli_backtest.py -v
# Hand-verify the small backtest's arithmetic, then tighten the golden test
# to exact values with pytest.approx and re-run.
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
git add src/pkmn_quant/cli.py tests/test_cli_backtest.py
git commit -m "feat: pkmn backtest CLI with golden regression test"
```

---

### Task 10: Real-data smoke test (manual)

- [ ] **Step 1: Run the benchmark on real data**

```bash
uv run pkmn backtest --start 2024-03-01 --end 2026-06-30 --cash 10000 --kind sealed
```

Expected: a summary with plausible numbers (sealed buy-and-hold 2024→2026 should be meaningfully positive — the era's sealed market rose; total_return in rough range +30% to +200%, NOT +5000% which would mean an accounting bug), a results directory with both parquet files.

- [ ] **Step 2: Sanity-check the equity curve and fills**

```bash
uv run python -c "
import polars as pl
from pathlib import Path
run = sorted(Path('data/results').iterdir())[-1]
eq = pl.read_parquet(run / 'equity.parquet')
print(eq.head(3)); print(eq.tail(3))
fills = pl.read_parquet(run / 'fills.parquet')
print(fills.height, 'fills'); print(fills.head(10))
"
```

Checks: equity starts at 10000; fills all on the second trading day (T+1); no fill quantity exceeds the liquidity cap for its price tier; equity curve has no absurd single-day jumps.

- [ ] **Step 3: Commit any fixes; done criteria**

Done criteria for Plan 2: all gates green; `pkmn backtest` produces a believable multi-year benchmark run on real data; the no-look-ahead probe test passes; golden regression test frozen.

---

## Notes for the implementer

- **Flagged from Plan 1 final review:** `Warehouse.load_prices()` is a full-scan glob — fine at current scale (~3M rows); MarketData filters after load. If the backfill makes this slow, push the date filter into a `warehouse.query()` WHERE clause instead — do not optimize preemptively.
- `tracked_groups(today=...)` convention: ingest passes range-end; live mode (Plan 3) must pass wall-clock today.
- `products` may contain kinds `single|sealed|excluded`; strategies filter — the engine itself is universe-agnostic.
