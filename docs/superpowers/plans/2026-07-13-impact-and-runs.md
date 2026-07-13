# Plan 9: Walk-the-Spread Impact Costs + Experiment Tracking — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Order size moves fill prices against you (walk-the-spread market impact, scaled by each product's observed listing spread), and every backtest/walk-forward run is recorded in an append-only registry keyed by config hash + data fingerprint.

**Architecture:** Impact lives in the frozen `CostModel` (engine default OFF so all goldens survive; CLI default ON). The executor resolves `mid`/`low` quotes lazily for ordered assets only (never regressing the Plan 8 marks-cursor perf win) and records impact as an explicit new `Fill.impact` field — `Fill.price` stays the observable market print (auditable-ledger convention). Experiment tracking is a new `research/runs.py` JSONL registry hooked into the `backtest`/`walkforward` CLI commands, with `pkmn runs list/show`.

**Tech Stack:** Python 3.13, polars, DuckDB (via `Warehouse.query`), typer, pytest. `uv` for everything — never pip.

**Spec:** `docs/superpowers/specs/2026-07-13-impact-costs-experiment-tracking-design.md` (read it first).

## Global Constraints

- All four gates before EVERY commit: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`
- Branch: `feat/impact-and-runs` off `main` (Task 1 creates it).
- Frozen dataclasses for value objects; copy mutable containers at trust boundaries.
- Golden regression tests pin exact numbers; if a deliberate change shifts results, update goldens in the same commit with a hand-derivation in the docstring.
- Never touch `data/` contents (874 ingested days; re-ingest is ~40 min). Tests use `tmp_path` warehouses only.
- Impact formula (total $ for `q` units after `used` units already filled today, daily cap `Q = max_daily_qty(market)`):
  - buy: `max(mid − market, 0) · q · (2·used + q) / (2Q)`
  - sell: `max(market − low, 0) · q · (2·used + q) / (2Q)`
  - `impact_enabled=False`, `qty ≤ 0`, missing (`None`) or crossed quote ⇒ `0.0`. Never negative.
- Workflow: STOP after each completed task, explain what/why at intern level, wait for explicit green light before the next task.

---

### Task 1: Branch + CostModel impact methods

**Files:**
- Modify: `src/pkmn_quant/engine/costs.py`
- Test: `tests/engine/test_costs.py`

**Interfaces:**
- Produces: `CostModel.impact_enabled: bool = False` (new frozen field), `CostModel.buy_impact(market: float, mid: float | None, qty: int, used: int = 0) -> float`, `CostModel.sell_impact(market: float, low: float | None, qty: int, used: int = 0) -> float`, `as_dict()` gains key `"impact_enabled"`. Later tasks call these exact signatures.

- [ ] **Step 1: Create the branch**

```bash
git checkout -b feat/impact-and-runs
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/engine/test_costs.py` (match the file's existing import style; it already imports `CostModel`):

```python
IMPACT_MODEL = CostModel(impact_enabled=True)


def test_impact_disabled_by_default_is_zero() -> None:
    m = CostModel()
    assert m.buy_impact(25.57, 29.64, 8) == 0.0
    assert m.sell_impact(25.57, 21.50, 8) == 0.0


def test_buy_impact_full_cap_is_half_spread_per_unit() -> None:
    # market 25.57, mid 29.64 -> spread 4.07; Q=8 (tier: <50 -> 8).
    # q=8, used=0: 4.07 * 8 * 8 / 16 = 16.28 total = half spread per unit.
    assert IMPACT_MODEL.buy_impact(25.57, 29.64, 8) == pytest.approx(16.28)


def test_sell_impact_walks_toward_low() -> None:
    # spread 25.57-21.50 = 4.07; same arithmetic as the buy side.
    assert IMPACT_MODEL.sell_impact(25.57, 21.50, 8) == pytest.approx(16.28)


def test_impact_monotone_in_qty() -> None:
    impacts = [IMPACT_MODEL.buy_impact(25.57, 29.64, q) for q in range(9)]
    assert impacts[0] == 0.0
    assert all(a < b for a, b in zip(impacts, impacts[1:]))


def test_impact_split_invariance() -> None:
    # Splitting one order into two must cost exactly the same total impact:
    # the second order walks the book from where the first stopped.
    whole = IMPACT_MODEL.buy_impact(25.57, 29.64, 8)
    split = IMPACT_MODEL.buy_impact(25.57, 29.64, 3) + IMPACT_MODEL.buy_impact(
        25.57, 29.64, 5, used=3
    )
    assert split == pytest.approx(whole)


def test_impact_crossed_or_missing_quote_is_zero() -> None:
    assert IMPACT_MODEL.buy_impact(25.57, 25.57, 8) == 0.0  # flat quote
    assert IMPACT_MODEL.buy_impact(25.57, 20.00, 8) == 0.0  # crossed (mid < market)
    assert IMPACT_MODEL.buy_impact(25.57, None, 8) == 0.0  # missing mid
    assert IMPACT_MODEL.sell_impact(25.57, 30.00, 8) == 0.0  # crossed (low > market)
    assert IMPACT_MODEL.sell_impact(25.57, None, 8) == 0.0  # missing low


def test_impact_q1_tier_single_unit_pays_half_spread() -> None:
    # $250 product -> fallback tier Q=1. One unit pays half the spread:
    # (300-250) * 1 * 1 / 2 = 25. Deliberate: one-sale-a-day markets do not
    # hand you the ideal market price (spec, "Formula" section).
    assert IMPACT_MODEL.buy_impact(250.0, 300.0, 1) == pytest.approx(25.0)


def test_as_dict_includes_impact_flag() -> None:
    assert CostModel().as_dict()["impact_enabled"] is False
    assert IMPACT_MODEL.as_dict()["impact_enabled"] is True
```

If the file does not already import `pytest`, add `import pytest`.

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/engine/test_costs.py -v`
Expected: FAIL — `TypeError: CostModel.__init__() got an unexpected keyword argument 'impact_enabled'`

- [ ] **Step 4: Implement**

In `src/pkmn_quant/engine/costs.py`, add the field after `fallback_max_qty`:

```python
    fallback_max_qty: int = DEFAULT_MAX_QTY
    # Walk-the-spread market impact (spec 2026-07-13). OFF at the engine
    # level so existing goldens/backtests are bit-identical; the CLI turns
    # it on by default.
    impact_enabled: bool = False
```

Add two methods after `max_daily_qty` and extend `as_dict`:

```python
    def buy_impact(self, market: float, mid: float | None, qty: int, used: int = 0) -> float:
        """Total $ impact for buying qty units after `used` already filled today.

        Marginal price ramps linearly from market (front of the book) to mid
        (median listing) at the daily cap Q; units used+1..used+qty cost
        spread * qty * (2*used + qty) / (2Q) extra in total. Zero when
        disabled, when mid is missing, or when the quote is crossed — never
        negative, never invented from missing data.
        """
        return self._impact(market, mid, market, qty, used)

    def sell_impact(self, market: float, low: float | None, qty: int, used: int = 0) -> float:
        """Total $ impact for selling: undercut from market toward low."""
        return self._impact(market, market, low, qty, used)

    def _impact(
        self, market: float, upper: float | None, lower: float | None, qty: int, used: int
    ) -> float:
        if not self.impact_enabled or qty <= 0 or upper is None or lower is None:
            return 0.0
        spread = upper - lower
        if spread <= 0:
            return 0.0
        q_cap = self.max_daily_qty(market)
        return spread * qty * (2 * used + qty) / (2 * q_cap)
```

In `as_dict()` add `"impact_enabled": self.impact_enabled,` to the returned dict.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/engine/test_costs.py -v`
Expected: all PASS

- [ ] **Step 6: All four gates, then commit**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy
git add src/pkmn_quant/engine/costs.py tests/engine/test_costs.py
git commit -m "feat: CostModel walk-the-spread impact methods (engine default off)"
```

---

### Task 2: `Fill.impact` + Portfolio accounting

**Files:**
- Modify: `src/pkmn_quant/engine/portfolio.py`
- Test: `tests/engine/test_portfolio.py`

**Interfaces:**
- Produces: `Fill.impact: float = 0.0` (validated `>= 0`); `Portfolio._buy`/`_sell` treat impact exactly like fees in cash and realized-P&L accounting. Default `0.0` keeps every existing constructor call and pinned number bit-identical.

- [ ] **Step 1: Write the failing tests**

Append to `tests/engine/test_portfolio.py` (reuse the file's existing `Asset`/`Fill`/`Portfolio` imports and any date constants; use `date(2025, 6, 1)` if none exist):

```python
def test_fill_negative_impact_rejected() -> None:
    with pytest.raises(ValueError, match="impact"):
        Fill(
            day=date(2025, 6, 1),
            asset=Asset(product_id=1, sub_type="Normal"),
            quantity=1,
            price=10.0,
            fees=0.0,
            impact=-0.01,
        )


def test_buy_impact_reduces_cash_and_realized_pnl() -> None:
    pf = Portfolio(cash=100.0)
    pf.apply(
        Fill(
            day=date(2025, 6, 1),
            asset=Asset(product_id=1, sub_type="Normal"),
            quantity=2,
            price=10.0,
            fees=1.0,
            impact=3.0,
        )
    )
    # cash: 100 - 2*10 - 1 - 3 = 76; impact expensed like a fee.
    assert pf.cash == pytest.approx(76.0)
    assert pf.realized_pnl == pytest.approx(-4.0)
    # avg_cost stays the print: impact is explicit, not smeared into basis.
    assert pf.positions[Asset(product_id=1, sub_type="Normal")].avg_cost == pytest.approx(10.0)


def test_sell_impact_reduces_proceeds() -> None:
    a = Asset(product_id=1, sub_type="Normal")
    pf = Portfolio(cash=0.0)
    pf.apply(Fill(day=date(2025, 6, 1), asset=a, quantity=2, price=10.0, fees=0.0))
    pf.apply(Fill(day=date(2025, 6, 2), asset=a, quantity=-2, price=12.0, fees=1.0, impact=2.0))
    # sell cash: +2*12 - 1 - 2 = 21; started 0 - 20 buy = -20 -> 1.0
    assert pf.cash == pytest.approx(1.0)
    # realized: proceeds 24 - basis 20 - fees 1 - impact 2 = +1
    assert pf.realized_pnl == pytest.approx(1.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/engine/test_portfolio.py -v`
Expected: FAIL — `TypeError: Fill.__init__() got an unexpected keyword argument 'impact'`

- [ ] **Step 3: Implement**

In `src/pkmn_quant/engine/portfolio.py`:

`Fill` gains a defaulted field and validation (docstring: extend the `fees` sentence with "; `impact` is the walk-the-spread cost of demanding size, expensed like a fee but reported separately"):

```python
    day: date
    asset: Asset
    quantity: int
    price: float
    fees: float
    impact: float = 0.0

    def __post_init__(self) -> None:
        if self.price <= 0:
            raise ValueError(f"Fill.price must be positive, got {self.price}")
        if self.fees < 0:
            raise ValueError(f"Fill.fees must be non-negative, got {self.fees}")
        if self.impact < 0:
            raise ValueError(f"Fill.impact must be non-negative, got {self.impact}")
```

In `Portfolio._buy` replace the two accounting lines:

```python
        cost = f.quantity * f.price
        self.cash -= cost + f.fees + f.impact
        self.realized_pnl -= f.fees + f.impact
```

In `Portfolio._sell` replace the two accounting lines:

```python
        proceeds = qty * f.price
        self.cash += proceeds - f.fees - f.impact
        self.realized_pnl += proceeds - qty * pos.avg_cost - f.fees - f.impact
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/engine/test_portfolio.py -v`
Expected: all PASS

- [ ] **Step 5: All four gates, then commit**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy
git add src/pkmn_quant/engine/portfolio.py tests/engine/test_portfolio.py
git commit -m "feat: Fill.impact field, expensed like fees in Portfolio accounting"
```

---

### Task 3: `Quote` value object + `MarketData.quotes_on`

**Files:**
- Create: `src/pkmn_quant/engine/quotes.py`
- Modify: `src/pkmn_quant/engine/data.py`
- Test: `tests/engine/test_data.py`

**Interfaces:**
- Produces: `Quote(mid: float | None, low: float | None)` frozen dataclass in `pkmn_quant.engine.quotes`; `MarketData.quotes_on(day: date, assets: Collection[Asset]) -> dict[Asset, Quote]` returning entries only for assets that actually printed on `day`.
- Perf constraint: `prices_on`/`marks_on` hot paths are untouched; `quotes_on` cost is paid only on days with pending orders.

- [ ] **Step 1: Write the failing tests**

Append to `tests/engine/test_data.py` (the `market` fixture at the top of the file already seeds D1–D3; `tests/helpers.price_row` hardcodes `low=1.0, mid=2.0, high=3.0`):

Add `from pkmn_quant.engine.quotes import Quote` to the file's top-level imports, then:

```python
def test_quotes_on_returns_requested_assets_only(market: MarketData) -> None:
    quotes = market.quotes_on(D1, [A1])
    assert quotes == {A1: Quote(mid=2.0, low=1.0)}


def test_quotes_on_no_print_no_entry(market: MarketData) -> None:
    # A2 does not trade on D2: no quote — impact must fall back to zero
    # rather than a stale or invented number.
    assert market.quotes_on(D2, [A1, A2]) == {A1: Quote(mid=2.0, low=1.0)}


def test_quotes_on_empty_assets_is_empty(market: MarketData) -> None:
    assert market.quotes_on(D1, []) == {}


def test_quotes_on_null_mid_gives_none(tmp_path: Path) -> None:
    w = Warehouse(Paths(root=tmp_path))
    r = row(D1, 1, 10.0)
    r["mid"] = None
    w.write_prices(D1, pl.DataFrame([r], schema=PRICE_SCHEMA))
    md = MarketData.from_warehouse(w, start=D1, end=D1)
    assert md.quotes_on(D1, [A1]) == {A1: Quote(mid=None, low=1.0)}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/engine/test_data.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pkmn_quant.engine.quotes'`

- [ ] **Step 3: Implement**

Create `src/pkmn_quant/engine/quotes.py`:

```python
"""Per-day quote fields the impact model needs beyond the market print."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Quote:
    """As-printed mid (median listing) and low (lowest listing) for one asset-day.

    None when the source row has no value; consumers treat missing fields as
    zero impact — never fill them in (spec: no invented numbers).
    """

    mid: float | None
    low: float | None
```

In `src/pkmn_quant/engine/data.py`:

1. Imports: add `from collections.abc import Collection` and `from pkmn_quant.engine.quotes import Quote`; add `field` to the dataclasses import.
2. New dataclass field after `_cursor` (defaulted so hand-built instances in tests keep working):

```python
    # date -> (date, product_id, sub_type, mid, low) frame; resolved lazily
    # by quotes_on for ordered assets only, so the hot prices_on/marks_on
    # paths (Plan 8 perf) never pay for it.
    _quotes_by_day: dict[date, pl.DataFrame] = field(default_factory=dict)
```

3. In `from_warehouse`, right after the `frame_by_day` construction:

```python
        quotes_by_day_raw = (
            frame.select("date", "product_id", "sub_type", "mid", "low").partition_by(
                "date", as_dict=True, include_key=True
            )
            if frame.height
            else {}
        )
        quotes_by_day: dict[date, pl.DataFrame] = {
            k[0]: v for k, v in quotes_by_day_raw.items()
        }
```

and pass `_quotes_by_day=quotes_by_day` to the `cls(...)` call.

4. New method after `prices_on`:

```python
    def quotes_on(self, day: date, assets: Collection[Asset]) -> dict[Asset, Quote]:
        """mid/low actually printed on `day`, for the requested assets only.

        No carry-forward (same rule as prices_on): a stale quote must not
        price today's impact. Assets that did not print get no entry.
        """
        part = self._quotes_by_day.get(day)
        if part is None or not assets:
            return {}
        wanted = set(assets)
        out: dict[Asset, Quote] = {}
        for _, pid, st, mid, low in part.iter_rows():
            asset = Asset(product_id=int(pid), sub_type=str(st))
            if asset in wanted:
                out[asset] = Quote(
                    mid=float(mid) if mid is not None else None,
                    low=float(low) if low is not None else None,
                )
        return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/engine/test_data.py -v`
Expected: all PASS

- [ ] **Step 5: All four gates, then commit**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy
git add src/pkmn_quant/engine/quotes.py src/pkmn_quant/engine/data.py tests/engine/test_data.py
git commit -m "feat: Quote value object + MarketData.quotes_on (lazy, ordered assets only)"
```

---

### Task 4: Executor applies impact

**Files:**
- Modify: `src/pkmn_quant/engine/execution.py`
- Test: `tests/engine/test_execution.py`

**Interfaces:**
- Consumes: `CostModel.buy_impact/sell_impact` (Task 1), `Fill.impact` (Task 2), `Quote` (Task 3).
- Produces: `ExecutionSimulator.execute(orders, prices, portfolio, day, quotes: dict[Asset, Quote] | None = None)` — the keyword default keeps every existing call site and test working with zero impact.

- [ ] **Step 1: Write the failing tests**

Append to `tests/engine/test_execution.py` (reuse its existing helpers/imports for `ExecutionSimulator`, `Order`, `Portfolio`, `Asset`; add `from pkmn_quant.engine.quotes import Quote` and `from pkmn_quant.engine.costs import CostModel` if absent; use the file's date constant or `date(2025, 6, 1)` as `D`):

```python
IMPACT_SIM = ExecutionSimulator(CostModel(impact_enabled=True))
A = Asset(product_id=1, sub_type="Normal")


def test_buy_fill_carries_impact() -> None:
    pf = Portfolio(cash=1000.0)
    fills = IMPACT_SIM.execute(
        [Order(asset=A, quantity=4)],
        {A: 25.57},
        pf,
        date(2025, 6, 2),
        quotes={A: Quote(mid=29.64, low=21.50)},
    )
    # Q=8; impact = 4.07 * 4 * 4 / 16 = 4.07
    assert len(fills) == 1
    assert fills[0].price == pytest.approx(25.57)  # print unchanged
    assert fills[0].impact == pytest.approx(4.07)
    assert pf.cash == pytest.approx(1000.0 - 4 * 25.57 - 1.0 - 4.07)


def test_buy_without_quote_has_zero_impact() -> None:
    pf = Portfolio(cash=1000.0)
    fills = IMPACT_SIM.execute([Order(asset=A, quantity=4)], {A: 25.57}, pf, date(2025, 6, 2))
    assert fills[0].impact == 0.0


def test_buy_shrinks_qty_until_impact_affordable() -> None:
    # cash 100, market 12 (Q=8), mid 16: flat afford = floor(99/12) = 8, but
    # 8*12 + 1 + impact(8)=16 -> 113 > 100. Shrink: q=7 costs 84+1+12.25 =
    # 97.25 <= 100. Executor must fill 7, impact 12.25.
    pf = Portfolio(cash=100.0)
    fills = IMPACT_SIM.execute(
        [Order(asset=A, quantity=10)],
        {A: 12.0},
        pf,
        date(2025, 6, 2),
        quotes={A: Quote(mid=16.0, low=None)},
    )
    assert fills[0].quantity == 7
    assert fills[0].impact == pytest.approx(12.25)
    assert pf.cash == pytest.approx(100.0 - 97.25)


def test_split_orders_pay_same_impact_as_one() -> None:
    quotes = {A: Quote(mid=29.64, low=21.50)}
    pf1 = Portfolio(cash=10_000.0)
    IMPACT_SIM.execute([Order(asset=A, quantity=8)], {A: 25.57}, pf1, date(2025, 6, 2), quotes=quotes)
    pf2 = Portfolio(cash=10_000.0)
    IMPACT_SIM.execute(
        [Order(asset=A, quantity=3), Order(asset=A, quantity=5)],
        {A: 25.57},
        pf2,
        date(2025, 6, 2),
        quotes=quotes,
    )
    # Two shipping lines differ; strip that out and compare pure impact+price.
    assert pf2.cash == pytest.approx(pf1.cash - 1.0)


def test_sell_fill_carries_impact() -> None:
    pf = Portfolio(cash=0.0)
    pf.apply(Fill(day=date(2025, 6, 1), asset=A, quantity=8, price=20.0, fees=0.0))
    fills = IMPACT_SIM.execute(
        [Order(asset=A, quantity=-8)],
        {A: 25.57},
        pf,
        date(2025, 6, 2),
        quotes={A: Quote(mid=None, low=21.50)},
    )
    assert fills[0].impact == pytest.approx(16.28)  # 4.07 * 8 * 8 / 16
```

Add `from pkmn_quant.engine.portfolio import Fill` if the file lacks it.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/engine/test_execution.py -v`
Expected: FAIL — `TypeError: ExecutionSimulator.execute() got an unexpected keyword argument 'quotes'`

- [ ] **Step 3: Implement**

In `src/pkmn_quant/engine/execution.py`:

1. Module docstring, replace the design-note sentence with: "Design note: Fill.price is always the observable market print; ALL costs are explicit in fees (buy side: shipping; sell side: marketplace fee + shipping) and impact (walk-the-spread cost of demanding size). This keeps the ledger auditable."
2. Import `Quote`: `from pkmn_quant.engine.quotes import Quote`.
3. New `execute` signature and body (only the changed parts shown; the loop structure stays):

```python
    def execute(
        self,
        orders: list[Order],
        prices: dict[Asset, float],
        portfolio: Portfolio,
        day: date,
        quotes: dict[Asset, Quote] | None = None,
    ) -> list[Fill]:
```

Inside the loop, thread depth and quote into the fill helpers:

```python
            fill = (
                self._fill_buy(order, market, portfolio, day, cap_left, used, quotes_map.get(order.asset))
                if order.quantity > 0
                else self._fill_sell(order, market, portfolio, day, cap_left, used, quotes_map.get(order.asset))
            )
```

with `quotes_map = quotes or {}` bound once before the loop.

4. `_fill_buy` — impact-aware affordability (shrink loop; `Q <= 20` so it is tiny and exact — no closed form for the quadratic):

```python
    def _fill_buy(
        self,
        order: Order,
        market: float,
        portfolio: Portfolio,
        day: date,
        cap_left: int,
        used: int,
        quote: Quote | None,
    ) -> Fill | None:
        qty = min(order.quantity, cap_left)
        # afford: qty * market + shipping_per_line + impact(qty) <= cash
        affordable = math.floor((portfolio.cash - self.cost_model.shipping_per_line) / market)
        qty = min(qty, max(affordable, 0))
        mid = quote.mid if quote is not None else None
        impact = self.cost_model.buy_impact(market, mid, qty, used)
        while qty > 0 and qty * market + self.cost_model.shipping_per_line + impact > portfolio.cash:
            qty -= 1
            impact = self.cost_model.buy_impact(market, mid, qty, used)
        if qty <= 0:
            return None
        return Fill(
            day=day,
            asset=order.asset,
            quantity=qty,
            price=market,
            fees=self.cost_model.shipping_per_line,
            impact=impact,
        )
```

5. `_fill_sell`:

```python
    def _fill_sell(
        self,
        order: Order,
        market: float,
        portfolio: Portfolio,
        day: date,
        cap_left: int,
        used: int,
        quote: Quote | None,
    ) -> Fill | None:
        pos = portfolio.positions.get(order.asset)
        if pos is None:
            return None
        qty = min(-order.quantity, pos.quantity, cap_left)
        if qty <= 0:
            return None
        fees = qty * market * self.cost_model.fee_rate + self.cost_model.shipping_per_line
        low = quote.low if quote is not None else None
        impact = self.cost_model.sell_impact(market, low, qty, used)
        return Fill(day=day, asset=order.asset, quantity=-qty, price=market, fees=fees, impact=impact)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/engine/test_execution.py -v`
Expected: all PASS (old executor tests too — no `quotes` means zero impact)

- [ ] **Step 5: All four gates, then commit**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy
git add src/pkmn_quant/engine/execution.py tests/engine/test_execution.py
git commit -m "feat: executor applies depth-aware walk-the-spread impact"
```

---

### Task 5: Backtest loop wiring + CLI flags + goldens

**Files:**
- Modify: `src/pkmn_quant/engine/backtest.py`, `src/pkmn_quant/cli.py` (`backtest` and `walkforward` commands)
- Test: `tests/test_cli_backtest.py`

**Interfaces:**
- Consumes: `MarketData.quotes_on` (Task 3), executor `quotes=` kwarg (Task 4).
- Produces: `pkmn backtest`/`pkmn walkforward` accept `--impact/--no-impact` (default `--impact`); `fills.parquet` gains an `impact: Float64` column. The `cost_model` variables in both CLI commands are bound to names (`cm = CostModel(impact_enabled=impact)`) that Task 9's run-recording hook reuses.

- [ ] **Step 1: Write the failing tests**

In `tests/test_cli_backtest.py`:

1. Change `run_cli` to accept extra args:

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

2. In `test_backtest_golden_numbers`, change the invocation to `run_cli(tmp_path, "--no-impact")` and add one sentence to the docstring: "Runs --no-impact: these numbers pin the flat-cost engine." Keep every pinned number unchanged, and add one assertion after the `fees` check: `assert f["impact"] == pytest.approx(0.0)`.

3. Add the impact-on golden:

```python
def seed_impact(root: Path) -> None:
    """Like seed(), but with a real (uncrossed) mid so impact is nonzero.

    price_row hardcodes mid=2.0 which is crossed against market>2 (impact
    clamps to zero); override mid per day.
    """
    w = Warehouse(Paths(root=root))
    for day, market, mid in ((D1, 10.0, 13.0), (D2, 12.0, 16.0), (D3, 15.0, 18.0)):
        r = row(day, 1, market)
        r["mid"] = mid
        w.write_prices(day, pl.DataFrame([r], schema=PRICE_SCHEMA))
    w.write_products(
        pl.DataFrame(
            {
                "product_id": [1],
                "group_id": [1],
                "name": ["Box"],
                "rarity": [None],
                "kind": ["sealed"],
                "released_on": [D1],
            }
        )
    )


def test_backtest_golden_numbers_with_impact(tmp_path: Path) -> None:
    """Golden regression for the impact-on engine (CLI default).

    Hand-verified arithmetic (CostModel defaults + impact_enabled; $12 price
    -> liquidity cap Q=8):
      D1: no fills; BuyAndHold sees mark 10.0, budget 100 -> orders 10 units.
          Equity = 100 (all cash).
      D2: print 12.0, mid 16.0 -> spread 4. Flat clip: min(10, cap 8,
          floor((100-1)/12)=8) = 8. Impact(8) = 4*8*8/(2*8) = 16;
          8*12+1+16 = 113 > 100 -> shrink. Impact(7) = 4*7*7/16 = 12.25;
          7*12+1+12.25 = 97.25 <= 100 -> fill 7 @ print 12, fees 1,
          impact 12.25. Cash = 2.75. Equity = 2.75 + 7*12 = 86.75.
      D3: holding -> no orders. Equity = 2.75 + 7*15 = 107.75.
    """
    seed_impact(tmp_path)
    result = run_cli(tmp_path)
    assert result.exit_code == 0, result.output
    out_dir = tmp_path / "data" / "results"
    run_dir = next(iter(out_dir.iterdir()))
    equity = pl.read_parquet(run_dir / "equity.parquet")["equity"].to_list()
    assert equity == pytest.approx([100.0, 86.75, 107.75])
    fills = pl.read_parquet(run_dir / "fills.parquet")
    assert fills.height == 1
    f = fills.row(0, named=True)
    assert f["quantity"] == 7
    assert f["price"] == pytest.approx(12.0)
    assert f["fees"] == pytest.approx(1.0)
    assert f["impact"] == pytest.approx(12.25)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli_backtest.py -v`
Expected: FAIL — `--no-impact` is an unknown option (exit code 2), and the impact golden gets flat-cost numbers.

- [ ] **Step 3: Implement**

`src/pkmn_quant/engine/backtest.py`, in the event loop, replace step 1:

```python
            # 1. Yesterday's orders fill at today's actually-printed prices.
            #    Quotes (mid/low for impact) are resolved lazily for the
            #    ordered assets only — the hot per-day dict paths stay as
            #    Plan 8 tuned them.
            quotes = market.quotes_on(day, [o.asset for o in pending]) if pending else {}
            fills.extend(
                simulator.execute(pending, market.prices_on(day), portfolio, day, quotes=quotes)
            )
            pending = []
```

`src/pkmn_quant/cli.py` — `backtest` command:

1. Add the option after `kind`:

```python
    impact: bool = typer.Option(
        True,
        "--impact/--no-impact",
        help="Walk-the-spread market impact on fills (see Plan 9 spec).",
    ),
```

2. Bind the warehouse and cost model to names (Task 9 reuses both):

```python
    wh = Warehouse(Paths(root=root))
    cm = CostModel(impact_enabled=impact)
    result = Backtest(
        warehouse=wh,
        strategy=BuyAndHold(kind=kind),
        cost_model=cm,
        ...
    ).run()
```

3. In the `fills_df` construction add `"impact": f.impact,` to the row dict and `"impact": pl.Float64,` to the schema.

`walkforward` command: add the same `impact` option, then replace `cost_model=CostModel(),` with a bound name:

```python
    cm = CostModel(impact_enabled=impact)
    result = run_walkforward(
        ...,
        cost_model=cm,
        ...
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli_backtest.py tests/test_cli_walkforward.py tests/engine/test_backtest.py -v`
Expected: all PASS. If `tests/test_cli_walkforward.py` pins fold numbers that shift because its seed data has uncrossed mids, inspect: seed rows built with `price_row` have `mid=2.0` (crossed for market > 2) so impact is zero and numbers must NOT shift — a failure there means a bug, not a golden update.

- [ ] **Step 5: All four gates, then commit**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy
git add src/pkmn_quant/engine/backtest.py src/pkmn_quant/cli.py tests/test_cli_backtest.py
git commit -m "feat: impact wired through backtest loop; --impact/--no-impact CLI flags; impact-on golden"
```

---

### Task 6: Ledger accepts `impact`

**Files:**
- Modify: `src/pkmn_quant/live/ledger.py`
- Test: `tests/live/test_ledger.py`

**Interfaces:**
- Consumes: `Fill.impact` (Task 2).
- Produces: trade events may carry optional `"impact"` (float ≥ 0, default 0.0); `LedgerEvent.impact: float | None`; replay passes it into `Fill`. Old ledger files (no key) replay unchanged.

- [ ] **Step 1: Write the failing tests**

Append to `tests/live/test_ledger.py` (reuse the file's existing helpers for writing ledger lines and a products frame; the patterns below assume a `products` fixture/frame with `product_id=1` — mirror whatever the file's existing replay tests use):

```python
def test_trade_with_impact_reduces_cash() -> None:
    events = _parse_lines(
        [
            '{"date": "2025-06-01", "kind": "deposit", "amount": 100.0}',
            '{"date": "2025-06-02", "kind": "buy", "product_id": 1, "sub_type": "Normal",'
            ' "qty": 2, "price": 10.0, "fees": 1.0, "impact": 3.0}',
        ]
    )
    pf = replay(events, PRODUCTS)
    assert pf.cash == pytest.approx(100.0 - 20.0 - 1.0 - 3.0)


def test_trade_without_impact_key_is_backward_compatible() -> None:
    events = _parse_lines(
        [
            '{"date": "2025-06-01", "kind": "deposit", "amount": 100.0}',
            '{"date": "2025-06-02", "kind": "buy", "product_id": 1, "sub_type": "Normal",'
            ' "qty": 2, "price": 10.0, "fees": 1.0}',
        ]
    )
    pf = replay(events, PRODUCTS)
    assert pf.cash == pytest.approx(79.0)


def test_negative_impact_rejected() -> None:
    with pytest.raises(LedgerError, match="impact"):
        _parse_lines(
            [
                '{"date": "2025-06-02", "kind": "buy", "product_id": 1, "sub_type": "Normal",'
                ' "qty": 1, "price": 10.0, "fees": 0.0, "impact": -1.0}'
            ]
        )
```

(`PRODUCTS`: reuse the existing module-level products frame if one exists; otherwise build `pl.DataFrame({"product_id": [1], "name": ["Box"]})` — match the columns the file's other replay tests use. `_parse_lines` and `replay` import from `pkmn_quant.live.ledger`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/live/test_ledger.py -v`
Expected: FAIL — `unexpected key(s) for 'buy': impact`

- [ ] **Step 3: Implement**

In `src/pkmn_quant/live/ledger.py`:

1. `_TRADE_KEYS` gains the optional key:

```python
_TRADE_KEYS = frozenset({"date", "kind", "product_id", "sub_type", "qty", "price", "fees", "impact"})
```

2. `LedgerEvent` gains `impact: float | None = None` (after `fees`).
3. In `_parse_line`'s trade branch, parse and validate after `fees`:

```python
        impact = float(obj.get("impact", 0.0))
```

(inside the existing `try` block), and after the `fees` validations:

```python
    if not math.isfinite(impact):
        raise fail(f"impact must be finite, got {impact}")
    if impact < 0:
        raise fail(f"impact must be non-negative, got {impact}")
```

and pass `impact=impact` to the returned `LedgerEvent`.
4. In `replay`, the trade `Fill` gains the field:

```python
            fill = Fill(
                day=e.day,
                asset=e.asset,
                quantity=signed,
                price=e.price,
                fees=e.fees or 0.0,
                impact=e.impact or 0.0,
            )
```

5. Module docstring: extend the fees sentence with "; optional `impact` is the walk-the-spread cost recorded by paper fills (default 0)".

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/live/test_ledger.py tests/test_cli_portfolio.py -v`
Expected: all PASS

- [ ] **Step 5: All four gates, then commit**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy
git add src/pkmn_quant/live/ledger.py tests/live/test_ledger.py
git commit -m "feat: ledger accepts optional impact key, backward compatible"
```

---

### Task 7: Live path — Recommendation quotes, paper impact, daily flag

**Files:**
- Modify: `src/pkmn_quant/live/signals.py`, `src/pkmn_quant/live/paper.py`, `src/pkmn_quant/cli.py` (`daily` command)
- Test: `tests/live/test_signals.py`, `tests/live/test_paper.py`, `tests/test_cli_daily.py` (and `tests/live/test_report.py` if serialization assertions pin exact fields)

**Interfaces:**
- Consumes: `MarketData.quotes_on` (Task 3), `CostModel.buy_impact/sell_impact` (Task 1), ledger `impact` key (Task 6).
- Produces: `Recommendation.mid: float | None = None` and `Recommendation.low: float | None = None` (populated from the as-of day's actual prints); `plan_paper_fills` events carry `"impact"`; `pkmn daily` gets `--impact/--no-impact` (default on) controlling the planner's CostModel.

- [ ] **Step 1: Write the failing tests**

`tests/live/test_paper.py` — append (mirror the file's existing `Recommendation` construction helper/style; the new kwargs are the only addition):

```python
def test_buy_fill_records_impact_and_respects_cash() -> None:
    costs = CostModel(impact_enabled=True)
    rec = Recommendation(
        action="BUY",
        product_id=1,
        sub_type="Normal",
        name="Box",
        quantity=10,
        market_price=12.0,
        notional=120.0,
        mid=16.0,
        low=None,
    )
    batch = plan_paper_fills([rec], cash=100.0, day=date(2026, 7, 13), costs=costs)
    # Same arithmetic as the executor golden: fill 7, impact 12.25.
    assert len(batch) == 1
    assert batch[0]["qty"] == 7
    assert batch[0]["impact"] == pytest.approx(12.25)


def test_sell_fill_records_impact() -> None:
    costs = CostModel(impact_enabled=True)
    rec = Recommendation(
        action="SELL",
        product_id=1,
        sub_type="Normal",
        name="Box",
        quantity=8,
        market_price=25.57,
        notional=204.56,
        mid=None,
        low=21.50,
    )
    batch = plan_paper_fills([rec], cash=0.0, day=date(2026, 7, 13), costs=costs)
    assert batch[0]["impact"] == pytest.approx(16.28)


def test_no_quote_zero_impact_matches_old_numbers() -> None:
    costs = CostModel(impact_enabled=True)
    rec = Recommendation(
        action="BUY",
        product_id=1,
        sub_type="Normal",
        name="Box",
        quantity=8,
        market_price=12.0,
        notional=96.0,
    )
    batch = plan_paper_fills([rec], cash=100.0, day=date(2026, 7, 13), costs=costs)
    assert batch[0]["qty"] == 8
    assert batch[0]["impact"] == pytest.approx(0.0)
```

`tests/live/test_signals.py` — append (the file's `warehouse` fixture and `seed_wf_artifact` helper already exist at the top of the file; the fixture prints a row every day, so the as-of day has a real quote):

```python
def test_recommendations_carry_mid_low_quotes(warehouse: Warehouse, tmp_path: Path) -> None:
    results_dir = tmp_path / "data" / "results"
    seed_wf_artifact(results_dir)
    report = generate_signals(
        warehouse=warehouse,
        strategy_name="sealed-accumulation",
        cash=1000.0,
        results_dir=results_dir,
    )
    [rec] = report.recommendations
    # tests/helpers.price_row seeds mid=2.0, low=1.0 on every printed row.
    assert rec.mid == 2.0
    assert rec.low == 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/live/test_paper.py tests/live/test_signals.py -v`
Expected: FAIL — `Recommendation.__init__() got an unexpected keyword argument 'mid'`

- [ ] **Step 3: Implement**

`src/pkmn_quant/live/signals.py`:

1. `Recommendation` gains two fields after `gain_pct`:

```python
    # As-printed quotes on the as-of day (None when the asset did not print
    # that day) — the paper planner's impact inputs.
    mid: float | None = None
    low: float | None = None
```

2. In `generate_signals`, after `orders = strategy.on_bar(ctx)`:

```python
    quotes = market.quotes_on(latest, [o.asset for o in orders]) if orders else {}
```

and in the recommendation loop, bind the quote just before the constructor (next to the existing `avg_cost` binding):

```python
        quote = quotes.get(order.asset)
```

then add to the `Recommendation(...)` construction:

```python
                mid=quote.mid if quote is not None else None,
                low=quote.low if quote is not None else None,
```

`src/pkmn_quant/live/paper.py` — inside the loop:

SELL branch:

```python
        if rec.action == "SELL":
            qty = min(rec.quantity, cap)
            if qty <= 0:
                continue
            impact = costs.sell_impact(mark, rec.low, qty)
            fees = round(qty * mark * costs.fee_rate + costs.shipping_per_line, 2)
            cash_remaining += qty * mark * (1 - costs.fee_rate) - costs.shipping_per_line - impact
```

BUY branch (mirrors the executor's shrink loop):

```python
        else:  # BUY
            affordable = math.floor((cash_remaining - costs.shipping_per_line) / mark)
            qty = min(rec.quantity, cap, max(affordable, 0))
            impact = costs.buy_impact(mark, rec.mid, qty)
            while qty > 0 and qty * mark + costs.shipping_per_line + impact > cash_remaining:
                qty -= 1
                impact = costs.buy_impact(mark, rec.mid, qty)
            if qty <= 0:
                continue
            fees = costs.shipping_per_line
            cash_remaining -= qty * mark + costs.shipping_per_line + impact
```

Event dict gains `"impact": round(impact, 2),` after `"fees"`. Update the module docstring's "Mirrors the backtest executor's clipping" sentence to mention impact-aware affordability.

`src/pkmn_quant/cli.py` — `daily` command: add the option after `paper`:

```python
    impact: bool = typer.Option(
        True,
        "--impact/--no-impact",
        help="Walk-the-spread market impact on paper fills.",
    ),
```

and in the paper-fills block replace `CostModel()` with `CostModel(impact_enabled=impact)`.

- [ ] **Step 4: Run the affected suites; fix serialization pins**

Run: `uv run pytest tests/live/ tests/test_cli_daily.py tests/test_cli_signals.py tests/test_cli_paper.py -v`
Expected: paper/signals tests PASS. If `tests/live/test_report.py` (or CLI tests) pin exact `signals.json` payloads, they now include `"mid"`/`"low"` — update those assertions in the same commit (a serialization-shape change, not an engine-number change; no golden hand-derivation needed).

- [ ] **Step 5: All four gates, then commit**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy
git add src/pkmn_quant/live/signals.py src/pkmn_quant/live/paper.py src/pkmn_quant/cli.py tests/live/ tests/test_cli_daily.py
git commit -m "feat: live path carries quotes; paper fills pay walk-the-spread impact"
```

---

### Task 8: `research/runs.py` — the run registry

**Files:**
- Create: `src/pkmn_quant/research/runs.py`
- Test: `tests/research/test_runs.py`

**Interfaces:**
- Produces (Task 9 consumes these exact signatures):
  - `registry_path(root: Path) -> Path` → `root / "data" / "runs" / "registry.jsonl"`
  - `config_hash(config: dict[str, Any]) -> str`
  - `data_fingerprint(warehouse: Warehouse) -> dict[str, Any]`
  - `git_info(root: Path) -> tuple[str | None, bool]`
  - `record_run(root: Path, command: str, strategy: str, config: dict[str, Any], results: dict[str, float], artifact_path: Path, warehouse: Warehouse) -> str | None` — returns `run_id`, or `None` after a stderr warning; NEVER raises.
  - `load_runs(root: Path) -> list[RunRecord]`
  - `RunRecord` frozen dataclass: `run_id, recorded_at, command, strategy, git_sha (str | None), git_dirty (bool), config_hash, config (dict), data_fingerprint (dict), results (dict), artifact_path (str)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/research/test_runs.py`:

```python
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA
from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.research.runs import (
    config_hash,
    load_runs,
    record_run,
    registry_path,
)
from tests.helpers import price_row

D1 = date(2025, 6, 1)


@pytest.fixture
def warehouse(tmp_path: Path) -> Warehouse:
    w = Warehouse(Paths(root=tmp_path))
    w.write_prices(D1, pl.DataFrame([price_row(D1, 1, 10.0)], schema=PRICE_SCHEMA))
    return w


def test_config_hash_is_key_order_independent() -> None:
    a = {"start": "2024-03-01", "end": "2026-06-30", "trials": 15}
    b = {"trials": 15, "end": "2026-06-30", "start": "2024-03-01"}
    assert config_hash(a) == config_hash(b)
    assert config_hash(a) != config_hash({**a, "trials": 16})


def test_record_and_load_round_trip(tmp_path: Path, warehouse: Warehouse) -> None:
    run_id = record_run(
        root=tmp_path,
        command="backtest",
        strategy="buy-and-hold-sealed",
        config={"start": "2025-06-01", "end": "2025-06-03"},
        results={"total_return": 0.23},
        artifact_path=tmp_path / "data" / "results" / "x",
        warehouse=warehouse,
    )
    assert run_id is not None
    records = load_runs(tmp_path)
    assert len(records) == 1
    r = records[0]
    assert r.run_id == run_id
    assert r.command == "backtest"
    assert r.strategy == "buy-and-hold-sealed"
    assert r.results == {"total_return": 0.23}
    assert r.config_hash == config_hash({"start": "2025-06-01", "end": "2025-06-03"})
    assert r.data_fingerprint == {"min_date": "2025-06-01", "max_date": "2025-06-01", "rows": 1}
    # tmp_path is not a git repo -> unknown sha, treated as dirty.
    assert r.git_sha is None
    assert r.git_dirty is True


def test_recording_failure_warns_but_never_raises(
    tmp_path: Path, warehouse: Warehouse, capsys: pytest.CaptureFixture[str]
) -> None:
    # Make the registry path unwritable: create it as a DIRECTORY.
    registry_path(tmp_path).mkdir(parents=True)
    run_id = record_run(
        root=tmp_path,
        command="backtest",
        strategy="s",
        config={},
        results={},
        artifact_path=tmp_path,
        warehouse=warehouse,
    )
    assert run_id is None
    assert "run tracking failed" in capsys.readouterr().err


def test_load_runs_missing_file_is_empty(tmp_path: Path) -> None:
    assert load_runs(tmp_path) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/research/test_runs.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pkmn_quant.research.runs'`

- [ ] **Step 3: Implement**

Create `src/pkmn_quant/research/runs.py`:

```python
"""Experiment tracking: append-only JSONL registry of research runs.

Every completed `pkmn backtest` / `pkmn walkforward` appends one record so
any number in the findings doc is reproducible from its config hash + data
fingerprint (optuna is seeded, so same hash + same data => same results).
Recording never fails a run: bookkeeping errors warn on stderr and the
research result survives.

Naming note: research/registry.py is the STRATEGY registry; this module is
the RUN registry, named to match the `pkmn runs` CLI.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pkmn_quant.data.warehouse import Warehouse


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    recorded_at: str
    command: str
    strategy: str
    git_sha: str | None
    git_dirty: bool
    config_hash: str
    config: dict[str, Any]
    data_fingerprint: dict[str, Any]
    results: dict[str, float]
    artifact_path: str


def registry_path(root: Path) -> Path:
    return root / "data" / "runs" / "registry.jsonl"


def config_hash(config: dict[str, Any]) -> str:
    """SHA-256 of the canonical serialization: sorted keys, no whitespace."""
    canonical = json.dumps(config, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def git_info(root: Path) -> tuple[str | None, bool]:
    """(HEAD sha, dirty flag); (None, True) when git is unavailable."""
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, check=True
        ).stdout
        return sha, bool(status.strip())
    except (OSError, subprocess.CalledProcessError):
        return None, True


def data_fingerprint(warehouse: Warehouse) -> dict[str, Any]:
    """Cheap identity of the price data a run saw: date range + row count."""
    row = warehouse.query(
        'SELECT min("date") AS min_date, max("date") AS max_date, count(*) AS n_rows FROM prices'
    ).row(0, named=True)
    return {
        "min_date": str(row["min_date"]),
        "max_date": str(row["max_date"]),
        "rows": int(row["n_rows"]),
    }


def record_run(
    root: Path,
    command: str,
    strategy: str,
    config: dict[str, Any],
    results: dict[str, float],
    artifact_path: Path,
    warehouse: Warehouse,
) -> str | None:
    """Append one record; returns run_id, or None after warning. Never raises."""
    try:
        now = datetime.now(UTC)
        run_id = now.strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)
        sha, dirty = git_info(root)
        record = {
            "run_id": run_id,
            "recorded_at": now.isoformat(),
            "command": command,
            "strategy": strategy,
            "git_sha": sha,
            "git_dirty": dirty,
            "config_hash": config_hash(config),
            "config": config,
            "data_fingerprint": data_fingerprint(warehouse),
            "results": results,
            "artifact_path": str(artifact_path),
        }
        path = registry_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
        return run_id
    except Exception as exc:  # bookkeeping must never kill a research run
        print(f"warning: run tracking failed ({exc}); results are unaffected", file=sys.stderr)
        return None


def load_runs(root: Path) -> list[RunRecord]:
    """Parse the registry, oldest first. Missing file = []."""
    path = registry_path(root)
    if not path.is_file():
        return []
    records: list[RunRecord] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        records.append(RunRecord(**json.loads(line)))
    return records
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/research/test_runs.py -v`
Expected: all PASS

- [ ] **Step 5: All four gates, then commit**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy
git add src/pkmn_quant/research/runs.py tests/research/test_runs.py
git commit -m "feat: research/runs.py — append-only experiment registry"
```

---

### Task 9: `pkmn runs` CLI + recording hooks

**Files:**
- Modify: `src/pkmn_quant/cli.py`
- Test: `tests/test_cli_runs.py` (create)

**Interfaces:**
- Consumes: everything Task 8 produces; the `wh`/`cm` bindings Task 5 created in the `backtest` and `walkforward` commands.
- Produces: `pkmn runs list [--strategy X] [--root .]`, `pkmn runs show <id-prefix> [--root .]`; `backtest`/`walkforward` echo `run recorded: <run_id>` on success.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli_runs.py`:

```python
import json
from pathlib import Path

from typer.testing import CliRunner

from pkmn_quant.cli import app
from pkmn_quant.research.runs import load_runs, registry_path
from tests.test_cli_backtest import run_cli, seed


def test_backtest_records_a_run(tmp_path: Path) -> None:
    seed(tmp_path)
    result = run_cli(tmp_path)
    assert result.exit_code == 0, result.output
    records = load_runs(tmp_path)
    assert len(records) == 1
    assert records[0].command == "backtest"
    assert "run recorded: " + records[0].run_id in result.output


def test_runs_list_and_show(tmp_path: Path) -> None:
    seed(tmp_path)
    run_cli(tmp_path)
    run_id = load_runs(tmp_path)[0].run_id

    listed = CliRunner().invoke(app, ["runs", "list", "--root", str(tmp_path)])
    assert listed.exit_code == 0, listed.output
    assert run_id in listed.output

    shown = CliRunner().invoke(app, ["runs", "show", run_id[:8], "--root", str(tmp_path)])
    assert shown.exit_code == 0, shown.output
    payload = json.loads(shown.output)
    assert payload["run_id"] == run_id


def test_runs_show_unknown_id_clean_error(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["runs", "show", "nope", "--root", str(tmp_path)])
    assert result.exit_code != 0
    assert "no run matching" in result.output


def test_tracking_failure_does_not_fail_backtest(tmp_path: Path) -> None:
    seed(tmp_path)
    registry_path(tmp_path).mkdir(parents=True)  # unwritable: path is a dir
    result = run_cli(tmp_path)
    assert result.exit_code == 0, result.output
    assert "run tracking failed" in result.output
```

(`CliRunner` in this repo's typer version mixes stderr into `output`; if the warning assertion fails, check `result.stderr` instead.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli_runs.py -v`
Expected: FAIL — no run recorded / no `runs` command.

- [ ] **Step 3: Implement**

In `src/pkmn_quant/cli.py`:

1. New sub-app next to `portfolio_app`:

```python
runs_app = typer.Typer(no_args_is_help=True, help="Inspect the experiment run registry.")
app.add_typer(runs_app, name="runs")
```

2. Commands (deferred imports, matching house style):

```python
@runs_app.command("list")
def runs_list(
    strategy: str | None = typer.Option(None, help="Filter by strategy name."),
    root: Path = typer.Option(Path("."), help="Project root holding the data/ directory."),
) -> None:
    """Recorded research runs, newest first."""
    from pkmn_quant.research.runs import load_runs

    records = load_runs(root)
    if strategy:
        records = [r for r in records if r.strategy == strategy]
    if not records:
        typer.echo("no runs recorded")
        return
    for r in reversed(records):
        sha = (r.git_sha or "no-git")[:7] + ("*" if r.git_dirty else "")
        ret = r.results.get("total_return")
        ret_s = f"{ret:+.4f}" if ret is not None else "   -   "
        typer.echo(
            f"{r.run_id}  {r.command:<11}  {r.strategy:<24}  total_return {ret_s}  {sha}"
        )


@runs_app.command("show")
def runs_show(
    run_id: str = typer.Argument(..., help="Run id, or any unique prefix."),
    root: Path = typer.Option(Path("."), help="Project root holding the data/ directory."),
) -> None:
    """Full JSON record of one run."""
    import dataclasses

    from pkmn_quant.research.runs import load_runs

    matches = [r for r in load_runs(root) if r.run_id.startswith(run_id)]
    if not matches:
        raise typer.BadParameter(f"no run matching {run_id!r}; see `pkmn runs list`")
    if len(matches) > 1:
        ids = ", ".join(r.run_id for r in matches)
        raise typer.BadParameter(f"ambiguous run id {run_id!r}: matches {ids}")
    typer.echo(json.dumps(dataclasses.asdict(matches[0]), indent=2, sort_keys=True))
```

3. Hook in `backtest`, after `fills_df.write_parquet(...)` (uses Task 5's `wh`/`cm` bindings):

```python
    from pkmn_quant.research.runs import record_run

    run_id = record_run(
        root=root,
        command="backtest",
        strategy=result.strategy_name,
        config={
            "command": "backtest",
            "start": start,
            "end": end,
            "cash": cash,
            "kind": kind,
            "warmup_days": 0,
            "cost_model": cm.as_dict(),
        },
        results=result.summary,
        artifact_path=run_dir,
        warehouse=wh,
    )
    if run_id is not None:
        typer.echo(f"run recorded: {run_id}")
```

4. Hook in `walkforward`, after `write_walkforward_json(...)` — bind `wh = Warehouse(Paths(root=root))` once at the top of the command (replacing the inline construction in the `run_walkforward` call):

```python
    from pkmn_quant.research.runs import record_run

    run_id = record_run(
        root=root,
        command="walkforward",
        strategy=strategy,
        config={
            "command": "walkforward",
            "strategy": strategy,
            "start": start,
            "end": end,
            "is_days": is_days,
            "oos_days": oos_days,
            "trials": trials,
            "seed": seed,
            "cash": cash,
            "warmup_days": warmup_days,
            "objective_metric": objective_metric,
            "cost_model": cm.as_dict(),
        },
        results=result.summary,
        artifact_path=run_dir,
        warehouse=wh,
    )
    if run_id is not None:
        typer.echo(f"run recorded: {run_id}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli_runs.py tests/test_cli_backtest.py tests/test_cli_walkforward.py -v`
Expected: all PASS (note: `test_cli_backtest`'s golden asserts are unaffected — the extra `run recorded:` line is stdout only).

- [ ] **Step 5: All four gates, then commit**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy
git add src/pkmn_quant/cli.py tests/test_cli_runs.py
git commit -m "feat: pkmn runs list/show + run recording in backtest/walkforward"
```

---

### Task 10: Research re-run, findings, docs

**Files:**
- Modify: `docs/research-findings-2026-07.md`, `CLAUDE.md`, `README.md` (commands section if it lists CLI)

**Interfaces:**
- Consumes: the full impact-enabled pipeline. No new code.

- [ ] **Step 1: Re-run the headline research with impact on (the new CLI default)**

These run against the real 874-day warehouse; each walkforward takes minutes. Run sequentially:

```bash
uv run pkmn backtest --start 2024-03-01 --end 2026-06-30
uv run pkmn walkforward --strategy sealed-accumulation --start 2024-03-01 --end 2026-06-30 --trials 15
uv run pkmn walkforward --strategy ml-ranker --start 2024-03-01 --end 2026-06-30 --trials 15
uv run pkmn runs list
```

Expected: each command ends with `run recorded: <id>`; `pkmn runs list` shows all three.

- [ ] **Step 2: Write the findings section**

Add a "Plan 9: walk-the-spread impact" section to `docs/research-findings-2026-07.md`:
- Table: strategy × {OOS return without impact (prior sections' logged numbers), OOS return with impact}.
- State the hypothesis verdict: did impact hurt high-turnover strategies more and widen buy-and-hold's lead? Report what the numbers actually show, including if the hypothesis is wrong.
- Note the run_ids from `pkmn runs list` next to each number — first use of the registry as provenance.
- Repeat the standing caveats (mark smoothing, one bull regime, 15 trials).

- [ ] **Step 3: Update CLAUDE.md**

- Status section: add a Plan 9 bullet (impact model, registry, new test count from `uv run pytest`).
- Commands section: add `uv run pkmn runs list` and mention `--no-impact`.
- Layout section: mention `engine/quotes.py`, `research/runs.py`, `data/runs/` (gitignored).

- [ ] **Step 4: Gates, commit**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy
git add docs/research-findings-2026-07.md CLAUDE.md README.md
git commit -m "docs: Plan 9 findings — impact-adjusted walk-forward results"
```

- [ ] **Step 5: Finish the branch**

Use the superpowers:finishing-a-development-branch skill to merge `feat/impact-and-runs` per the repo's workflow (user green light required).
