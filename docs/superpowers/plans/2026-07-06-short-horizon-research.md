# pkmn_quant Plan 6: Short-Horizon Research (opened_on + cost-aware strategies)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Positions know when they were opened, so hold-day exit rules run identically in backtests and against the real ledger; dip-buyer and xs-momentum leave the portfolio-mode blocklist; a new cost-aware mean-reversion strategy targets 1–6 month flips whose entries must clear the round-trip cost hurdle. Spec: `docs/superpowers/specs/2026-07-06-short-horizon-research-design.md`.

**Architecture:** `Position` (engine) gains `opened_on: date | None`, set from `Fill.day` when a position is created — no accounting change, goldens stay byte-identical. The ledger already replays real trade dates through `Portfolio.apply`, so live portfolios get authentic `opened_on` for free. Strategies read `ctx.positions[asset].opened_on` instead of internal clocks (dip-buyer's `_entries` dict, xs-momentum's `_last_rebalance`), which makes them near-stateless and live-safe. New `strategies/cost_aware_reversion.py` holds a `CostModel` and only enters when the expected rebound clears fees+shipping plus a margin.

**Tech Stack:** existing stack only (polars, optuna, typer). No new dependencies.

**Prerequisite:** Plan 5 (reinvest loop) fully merged — this plan modifies `PORTFOLIO_SAFE_STRATEGIES`, the signals trust-boundary copy, and Plan-5 tests. Do not start until `feat/reinvest-loop` is on main. Work on a new branch `feat/short-horizon-research`. "Full suite green" below means every test passes — do not pin counts, Plan 5's final count depends on its Tasks 6–9.

**Key design decisions (from the spec):**
- `opened_on` = date of the first fill of the current continuous holding; adding to a position keeps it (positions look older → exits fire sooner, conservative). Full close then re-open records a fresh date.
- Strategies that need `opened_on` raise `ValueError` on `None` (loud); `None` is only reachable from hand-built test portfolios.
- Golden regression test must stay **byte-identical**. Any drift is a bug in your change, not a golden to update.
- dip-buyer/xs-momentum backtest numbers WILL shift (documented bug fixes). Their walk-forwards are re-run in Task 8 and the findings doc explains why.
- The cost hurdle: `hurdle(price) = fee_rate + 2 * shipping_per_line / price` (conservative single-unit round trip, TCGplayer defaults 12.75% + $1).

---

### Task 1: Engine — `Position.opened_on`

**Files:**
- Modify: `src/pkmn_quant/engine/portfolio.py` (Position dataclass, `_buy`)
- Test: extend `tests/engine/test_portfolio.py`

- [ ] **Step 1: Write the failing tests.** Read `tests/engine/test_portfolio.py` first and match its conventions. Append:

```python
def test_opened_on_set_when_position_created() -> None:
    pf = Portfolio(cash=1000.0)
    pf.apply(Fill(day=date(2026, 1, 5), asset=Asset(1, "Normal"), quantity=2, price=10.0, fees=0.0))
    assert pf.positions[Asset(1, "Normal")].opened_on == date(2026, 1, 5)


def test_opened_on_kept_when_adding_to_position() -> None:
    """Age = first fill of the continuous holding (conservative for time exits)."""
    pf = Portfolio(cash=1000.0)
    a = Asset(1, "Normal")
    pf.apply(Fill(day=date(2026, 1, 5), asset=a, quantity=1, price=10.0, fees=0.0))
    pf.apply(Fill(day=date(2026, 2, 1), asset=a, quantity=1, price=20.0, fees=0.0))
    assert pf.positions[a].opened_on == date(2026, 1, 5)
    assert pf.positions[a].avg_cost == pytest.approx(15.0)  # accounting unchanged


def test_opened_on_survives_partial_sell() -> None:
    pf = Portfolio(cash=1000.0)
    a = Asset(1, "Normal")
    pf.apply(Fill(day=date(2026, 1, 5), asset=a, quantity=2, price=10.0, fees=0.0))
    pf.apply(Fill(day=date(2026, 3, 1), asset=a, quantity=-1, price=12.0, fees=0.0))
    assert pf.positions[a].opened_on == date(2026, 1, 5)


def test_opened_on_fresh_after_close_and_reopen() -> None:
    pf = Portfolio(cash=1000.0)
    a = Asset(1, "Normal")
    pf.apply(Fill(day=date(2026, 1, 5), asset=a, quantity=1, price=10.0, fees=0.0))
    pf.apply(Fill(day=date(2026, 2, 1), asset=a, quantity=-1, price=12.0, fees=0.0))
    pf.apply(Fill(day=date(2026, 4, 1), asset=a, quantity=1, price=8.0, fees=0.0))
    assert pf.positions[a].opened_on == date(2026, 4, 1)
```

(If the file lacks `date`/`pytest` imports, add them to its import block.)

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/engine/test_portfolio.py -q` → AttributeError/TypeError on `opened_on`.

- [ ] **Step 3: Implement.** In `src/pkmn_quant/engine/portfolio.py`:

```python
@dataclass
class Position:
    quantity: int
    avg_cost: float
    # Date of the first fill of the current continuous holding. Adding to a
    # position keeps it; a full close removes the Position, so re-opening
    # records a fresh date. None only when hand-built (tests) — engine fills
    # always set it.
    opened_on: date | None = None
```

and in `_buy`, the new-position branch becomes:

```python
        if pos is None:
            self.positions[f.asset] = Position(
                quantity=f.quantity, avg_cost=f.price, opened_on=f.day
            )
```

Nothing else changes — no accounting math touched.

- [ ] **Step 4: Run tests, then the full suite (golden byte-identity check), then all four gates:**

```bash
uv run pytest tests/engine/test_portfolio.py -q
uv run pytest tests/test_cli_backtest.py -q     # goldens MUST pass unmodified
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

If the golden test fails, STOP — you changed accounting; do not update goldens.

- [ ] **Step 5: Commit**

```bash
git add src/pkmn_quant/engine/portfolio.py tests/engine/test_portfolio.py
git commit -m "feat: Position.opened_on — engine records entry dates"
```

---

### Task 2: `opened_on` flows to the ledger and the live Context

**Files:**
- Modify: `src/pkmn_quant/live/signals.py` (trust-boundary copy)
- Test: extend `tests/live/test_ledger.py`, `tests/live/test_signals.py`

The backtest loop already copies positions with `dataclasses.replace(p)` (`engine/backtest.py:78`), which carries every field including `opened_on`. The signals copy (Plan 5 Task 3) builds `Position(quantity=..., avg_cost=...)` explicitly and would DROP `opened_on` — switch it to the same `replace` idiom.

- [ ] **Step 1: Write the failing tests.** Append to `tests/live/test_ledger.py` (uses that file's `write_lines` + `PRODUCTS` helpers):

```python
def test_replay_sets_opened_on_from_buy_date(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    write_lines(
        path,
        [
            '{"date": "2026-07-01", "kind": "deposit", "amount": 1000.0}',
            '{"date": "2026-07-03", "kind": "buy", "product_id": 1, "sub_type": "Normal",'
            ' "qty": 2, "price": 100.0, "fees": 0.0}',
        ],
    )
    pf = load_portfolio(path, PRODUCTS)
    [(asset, pos)] = list(pf.positions.items())
    assert pos.opened_on == date(2026, 7, 3)
```

Append to `tests/live/test_signals.py` (uses that file's `warehouse` fixture and `seed_wf_artifact`):

```python
def test_portfolio_mode_context_copy_carries_opened_on(
    warehouse: Warehouse, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The trust-boundary copy must not drop opened_on (strategies need it).

    Pin it by capturing the Context a strategy actually receives: swap the
    sealed-accumulation factory for one returning a recording strategy."""
    from pkmn_quant.engine.execution import Order
    from pkmn_quant.engine.portfolio import Portfolio, Position
    from pkmn_quant.engine.portfolio import Asset as EAsset
    from pkmn_quant.engine.strategy import Context, Strategy
    from pkmn_quant.research.registry import REGISTRY, RegistryEntry

    captured: list[Context] = []

    class Recorder(Strategy):
        name = "sealed-accumulation"

        def on_bar(self, ctx: Context) -> list[Order]:
            captured.append(ctx)
            return []

    old = REGISTRY["sealed-accumulation"]
    monkeypatch.setitem(
        REGISTRY,
        "sealed-accumulation",
        RegistryEntry(factory=lambda p: Recorder(), space=old.space),
    )
    results_dir = tmp_path / "data" / "results"
    seed_wf_artifact(results_dir)
    pf = Portfolio(cash=500.0)
    original = Position(quantity=2, avg_cost=60.0, opened_on=date(2025, 1, 10))
    pf.positions[EAsset(1, "Normal")] = original
    generate_signals(
        warehouse=warehouse,
        strategy_name="sealed-accumulation",
        results_dir=results_dir,
        portfolio=pf,
    )
    [ctx] = captured
    copied = ctx.positions[EAsset(1, "Normal")]
    assert copied.opened_on == date(2025, 1, 10)  # field carried
    assert copied is not original  # trust boundary: a copy, not an alias
```

(If `generate_signals` looks the strategy up somewhere other than `REGISTRY`, adapt the monkeypatch target to the real lookup — but the two final assertions are the requirement.)

- [ ] **Step 2: verify the ledger test fails** (opened_on is None until Task 1's engine sets it — if Task 1 is merged it may already pass; then verify the signals copy test fails by checking the current explicit-constructor copy drops the field: temporarily assert on the copy in a scratch run, or just confirm signals.py still builds `Position(quantity=..., avg_cost=...)`).

- [ ] **Step 3: Implement.** In `src/pkmn_quant/live/signals.py`, add `from dataclasses import replace` to imports and change the portfolio-mode Context block:

```python
    if portfolio is not None:
        ctx_cash = portfolio.cash
        # Same trust-boundary idiom as the backtest loop (backtest.py):
        # replace() copies every field, including opened_on.
        ctx_positions = {a: replace(p) for a, p in portfolio.positions.items()}
```

(The `Position` import may become unused — remove it if so; ruff will tell you.)

- [ ] **Step 4: Run tests, all four gates:**

```bash
uv run pytest tests/live/ -q
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

- [ ] **Step 5: Commit**

```bash
git add src/pkmn_quant/live/signals.py tests/live/test_ledger.py tests/live/test_signals.py
git commit -m "feat: opened_on flows from ledger replay into the live Context"
```

---

### Task 3: Retrofit dip-buyer onto `opened_on`

**Files:**
- Modify: `src/pkmn_quant/strategies/dip_buyer.py`
- Test: `tests/strategies/test_dip_buyer.py`

Read both files in full first. The retrofit deletes `_entries` and the `reset()` override; the hold clock reads `pos.opened_on`. Two documented imprecisions disappear (update the class docstring accordingly): the clock now starts at the actual T+1 fill, and partially-filled positions keep a real entry date instead of being dumped every bar. Existing tests that construct `Position(...)` for held assets must now pass `opened_on=` (they'll fail loudly otherwise — that's the designed behavior); tests that poke `strategy._entries` are replaced by the tests below.

- [ ] **Step 1: Write the failing tests.** Append (self-contained; adapt only if the file already has an equivalent Context helper — then use it):

```python
def _mk_ctx(
    today: date,
    positions: dict[Asset, Position],
    cash: float,
    marks: dict[Asset, float],
) -> Context:
    """Minimal Context for exit-rule tests: entries need history, exits don't."""
    empty_prices = pl.DataFrame(
        schema={"date": pl.Date, "product_id": pl.Int64, "sub_type": pl.Utf8, "market": pl.Float64}
    )
    products = pl.DataFrame(
        {"product_id": [1], "group_id": [1], "name": ["X"], "rarity": [None],
         "kind": ["single"], "released_on": [date(2024, 1, 1)]}
    )
    return Context(
        today=today, history=empty_prices, products=products,
        positions=positions, cash=cash, marks=marks,
    )


def test_time_exit_uses_opened_on() -> None:
    s = DipBuyer(hold_days=30, take_profit=10.0)  # take_profit unreachable
    a = Asset(1, "Normal")
    held_29 = _mk_ctx(
        date(2026, 2, 3),
        {a: Position(quantity=2, avg_cost=10.0, opened_on=date(2026, 1, 5))},
        100.0, {a: 10.0},
    )
    assert s.on_bar(held_29) == []  # 29 days held: no exit
    held_30 = _mk_ctx(
        date(2026, 2, 4),
        {a: Position(quantity=2, avg_cost=10.0, opened_on=date(2026, 1, 5))},
        100.0, {a: 10.0},
    )
    [order] = s.on_bar(held_30)
    assert order.quantity == -2  # 30 days held: full exit


def test_none_opened_on_raises() -> None:
    s = DipBuyer()
    a = Asset(1, "Normal")
    ctx = _mk_ctx(
        date(2026, 2, 4), {a: Position(quantity=1, avg_cost=10.0)}, 100.0, {a: 10.0}
    )
    with pytest.raises(ValueError, match="opened_on"):
        s.on_bar(ctx)


def test_dip_buyer_is_stateless_across_bars() -> None:
    """No _entries: the same instance gives identical answers for identical
    Contexts — the property that makes single-bar live invocation safe."""
    s = DipBuyer(hold_days=30, take_profit=10.0)
    a = Asset(1, "Normal")
    ctx = _mk_ctx(
        date(2026, 2, 4),
        {a: Position(quantity=2, avg_cost=10.0, opened_on=date(2026, 1, 5))},
        100.0, {a: 10.0},
    )
    first = s.on_bar(ctx)
    ctx2 = _mk_ctx(
        date(2026, 2, 4),
        {a: Position(quantity=2, avg_cost=10.0, opened_on=date(2026, 1, 5))},
        100.0, {a: 10.0},
    )
    assert s.on_bar(ctx2) == first
    assert not hasattr(s, "_entries")
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/strategies/test_dip_buyer.py -q`.

- [ ] **Step 3: Implement.** In `dip_buyer.py`:

1. Delete `self._entries` from `__init__` and delete the `reset()` override (the ABC default is a no-op).
2. Replace the sell loop:

```python
        for asset, pos in sorted(ctx.positions.items(), key=lambda kv: kv[0].product_id):
            if pos.opened_on is None:
                raise ValueError(
                    f"{self.name}: position {asset} has no opened_on; "
                    "engine fills and ledger replay always set it"
                )
            mark = ctx.marks.get(asset)
            too_old = (ctx.today - pos.opened_on).days >= self.hold_days
            hit_target = mark is not None and mark >= pos.avg_cost * self.take_profit
            if too_old or hit_target:
                orders.append(Order(asset=asset, quantity=-pos.quantity))
```

3. In the entry loop, the candidate filter `if asset in ctx.positions or asset in self._entries or past_price <= 0:` loses the `_entries` clause, and delete `self._entries[asset] = ctx.today` after order emission.
4. Rewrite the class docstring: entry/exit rules unchanged in intent; state removed; hold clock starts at the T+1 fill (via `Position.opened_on`); partial fills keep their entry date; an emitted-but-unfilled buy no longer blocks re-entry (the next bar may re-emit while the dip persists).

- [ ] **Step 4: Fix collateral tests.** Run `uv run pytest tests/strategies/test_dip_buyer.py -q`; any pre-existing test constructing held `Position`s now needs `opened_on=<a date consistent with the scenario>`. Update those constructor calls only — if a test's *expected orders* change, re-derive the expectation by hand in its docstring (the emission-vs-fill fix can shift hold-day boundaries by one day) and note it in the commit message. Then all four gates:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

The golden test is buy-and-hold and must be untouched by this.

- [ ] **Step 5: Commit**

```bash
git add src/pkmn_quant/strategies/dip_buyer.py tests/strategies/test_dip_buyer.py
git commit -m "feat: dip-buyer reads opened_on — stateless, live-safe hold clock"
```

---

### Task 4: Retrofit xs-momentum onto `opened_on`

**Files:**
- Modify: `src/pkmn_quant/strategies/momentum.py`
- Test: `tests/strategies/test_momentum.py`

`_last_rebalance` is a portfolio-level clock. Derived replacement: rebalance is due when flat, or when `(today - newest opened_on).days >= rebalance_days` (last buy date ≈ last rebalance date — reconstructible from the ledger). Behavior deltas to document in the docstring: when flat, the strategy now evaluates every bar until a buy fills (previously it waited out `rebalance_days` even with no holdings); a rebalance whose buys don't fill retries next bar.

- [ ] **Step 1: Write the failing tests.** Append to `tests/strategies/test_momentum.py`. If the file already has a Context-building helper, use it; otherwise include this one (same shape as Task 3's, repeated here so tasks stand alone):

```python
def _mk_ctx(
    today: date,
    positions: dict[Asset, Position],
    cash: float,
    marks: dict[Asset, float],
) -> Context:
    """Minimal Context: rebalance-clock tests need no price history."""
    empty_prices = pl.DataFrame(
        schema={"date": pl.Date, "product_id": pl.Int64, "sub_type": pl.Utf8, "market": pl.Float64}
    )
    products = pl.DataFrame(
        {"product_id": [1], "group_id": [1], "name": ["X"], "rarity": [None],
         "kind": ["single"], "released_on": [date(2024, 1, 1)]}
    )
    return Context(
        today=today, history=empty_prices, products=products,
        positions=positions, cash=cash, marks=marks,
    )


def test_rebalance_clock_derived_from_newest_opened_on() -> None:
    s = CrossSectionalMomentum(rebalance_days=30)
    a, b = Asset(1, "Normal"), Asset(2, "Normal")
    positions = {
        a: Position(quantity=1, avg_cost=10.0, opened_on=date(2026, 1, 1)),
        b: Position(quantity=1, avg_cost=10.0, opened_on=date(2026, 1, 20)),
    }
    # 29 days after the NEWEST buy: not due, no orders at all.
    ctx = _mk_ctx(date(2026, 2, 18), positions, 100.0, {a: 10.0, b: 10.0})
    assert s.on_bar(ctx) == []
    # 30 days after the newest buy: due — with no candidates in empty history,
    # everything held is sold (dropped out of the empty target).
    positions2 = {
        a: Position(quantity=1, avg_cost=10.0, opened_on=date(2026, 1, 1)),
        b: Position(quantity=1, avg_cost=10.0, opened_on=date(2026, 1, 20)),
    }
    ctx2 = _mk_ctx(date(2026, 2, 19), positions2, 100.0, {a: 10.0, b: 10.0})
    orders = s.on_bar(ctx2)
    assert sorted(o.quantity for o in orders) == [-1, -1]


def test_flat_portfolio_is_always_due() -> None:
    s = CrossSectionalMomentum(rebalance_days=30)
    ctx = _mk_ctx(date(2026, 2, 18), {}, 100.0, {})
    # Flat + no candidates -> no orders, but it must not raise and must not
    # depend on any internal clock.
    assert s.on_bar(ctx) == []
    assert not hasattr(s, "_last_rebalance")


def test_momentum_none_opened_on_raises() -> None:
    s = CrossSectionalMomentum(rebalance_days=30)
    a = Asset(1, "Normal")
    ctx = _mk_ctx(
        date(2026, 2, 18), {a: Position(quantity=1, avg_cost=10.0)}, 100.0, {a: 10.0}
    )
    with pytest.raises(ValueError, match="opened_on"):
        s.on_bar(ctx)
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** In `momentum.py`:

1. Delete `self._last_rebalance` and the `reset()` override.
2. Replace the top of `on_bar`:

```python
    def _rebalance_due(self, ctx: Context) -> bool:
        if not ctx.positions:
            return True
        newest: date | None = None
        for asset, pos in ctx.positions.items():
            if pos.opened_on is None:
                raise ValueError(
                    f"{self.name}: position {asset} has no opened_on; "
                    "engine fills and ledger replay always set it"
                )
            newest = pos.opened_on if newest is None else max(newest, pos.opened_on)
        assert newest is not None
        return (ctx.today - newest).days >= self.rebalance_days

    def on_bar(self, ctx: Context) -> list[Order]:
        if not self._rebalance_due(ctx):
            return []
```

3. Update the class docstring per the behavior deltas above.

- [ ] **Step 4: Fix collateral tests** (same policy as Task 3 Step 4: add `opened_on=` to Position constructions; re-derive shifted expectations by hand in docstrings). Then all four gates.

- [ ] **Step 5: Commit**

```bash
git add src/pkmn_quant/strategies/momentum.py tests/strategies/test_momentum.py
git commit -m "feat: xs-momentum derives rebalance clock from opened_on"
```

---

### Task 5: `cost-aware-reversion` strategy

**Files:**
- Create: `src/pkmn_quant/strategies/cost_aware_reversion.py`
- Test: `tests/strategies/test_cost_aware_reversion.py`

- [ ] **Step 1: Write the failing tests** — `tests/strategies/test_cost_aware_reversion.py`:

```python
from datetime import date, timedelta

import polars as pl
import pytest

from pkmn_quant.engine.portfolio import Asset, Position
from pkmn_quant.engine.strategy import Context
from pkmn_quant.strategies.cost_aware_reversion import CostAwareReversion


def _history(rows: list[tuple[date, int, str, float]]) -> pl.DataFrame:
    # Explicit schema so an EMPTY history is still date/int/str/float typed —
    # an untyped empty frame would break the strategy's date filter.
    return pl.DataFrame(
        {
            "date": [r[0] for r in rows],
            "product_id": [r[1] for r in rows],
            "sub_type": [r[2] for r in rows],
            "market": [r[3] for r in rows],
        },
        schema={"date": pl.Date, "product_id": pl.Int64, "sub_type": pl.Utf8, "market": pl.Float64},
    )


def _products(ids_kinds: list[tuple[int, str]]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "product_id": [i for i, _ in ids_kinds],
            "group_id": [1] * len(ids_kinds),
            "name": [f"P{i}" for i, _ in ids_kinds],
            "rarity": [None] * len(ids_kinds),
            "kind": [k for _, k in ids_kinds],
            "released_on": [date(2024, 1, 1)] * len(ids_kinds),
        }
    )


def _ctx(today, history, products, positions, cash, marks) -> Context:
    return Context(
        today=today, history=history, products=products,
        positions=positions, cash=cash, marks=marks,
    )


def test_entry_fires_when_rebound_clears_hurdle() -> None:
    """high 100, mark 70: dip 0.30 >= 0.25; rebound 100/70-1 = 0.4286;
    hurdle = 0.1275 + 2*1/70 = 0.1561; 0.1561 + 0.05 = 0.2061 <= 0.4286 -> BUY.
    Sized: budget = 1000 * 0.10 = 100 -> floor(100/70) = 1 unit."""
    s = CostAwareReversion(
        dip_window_days=30, dip_threshold=0.25, min_edge=0.05, budget_frac=0.10
    )
    today = date(2026, 3, 1)
    hist = _history(
        [(today - timedelta(days=20), 1, "Normal", 100.0), (today, 1, "Normal", 70.0)]
    )
    a = Asset(1, "Normal")
    [order] = s.on_bar(_ctx(today, hist, _products([(1, "single")]), {}, 1000.0, {a: 70.0}))
    assert (order.asset, order.quantity) == (a, 1)


def test_cheap_card_rejected_by_shipping_hurdle() -> None:
    """high 6, mark 4: dip 0.333, rebound 0.50 — but hurdle = 0.1275 + 2*1/4
    = 0.6275; + 0.05 = 0.6775 > 0.50 -> shipping kills the trade, no order."""
    s = CostAwareReversion(dip_window_days=30, dip_threshold=0.25, min_edge=0.05, min_price=3.0)
    today = date(2026, 3, 1)
    hist = _history(
        [(today - timedelta(days=20), 1, "Normal", 6.0), (today, 1, "Normal", 4.0)]
    )
    a = Asset(1, "Normal")
    assert s.on_bar(_ctx(today, hist, _products([(1, "single")]), {}, 1000.0, {a: 4.0})) == []


def test_sealed_products_are_candidates_too() -> None:
    """Universe is both kinds: an identical dip on a sealed product enters."""
    s = CostAwareReversion(dip_window_days=30, dip_threshold=0.25, min_edge=0.05)
    today = date(2026, 3, 1)
    hist = _history(
        [(today - timedelta(days=20), 1, "Normal", 100.0), (today, 1, "Normal", 70.0)]
    )
    a = Asset(1, "Normal")
    [order] = s.on_bar(_ctx(today, hist, _products([(1, "sealed")]), {}, 1000.0, {a: 70.0}))
    assert order.quantity > 0


def test_time_exit_at_max_hold_days() -> None:
    s = CostAwareReversion(max_hold_days=120, take_profit=10.0)
    a = Asset(1, "Normal")
    pos = {a: Position(quantity=2, avg_cost=50.0, opened_on=date(2026, 1, 1))}
    empty = _history([])
    ctx = _ctx(date(2026, 5, 1), empty, _products([(1, "single")]), pos, 0.0, {a: 50.0})
    [order] = s.on_bar(ctx)  # 2026-05-01 - 2026-01-01 = 120 days
    assert order.quantity == -2


def test_take_profit_exit() -> None:
    s = CostAwareReversion(max_hold_days=9999, take_profit=1.25)
    a = Asset(1, "Normal")
    pos = {a: Position(quantity=1, avg_cost=40.0, opened_on=date(2026, 4, 30))}
    empty = _history([])
    ctx = _ctx(date(2026, 5, 1), empty, _products([(1, "single")]), pos, 0.0, {a: 50.0})
    [order] = s.on_bar(ctx)  # 50 >= 40 * 1.25
    assert order.quantity == -1


def test_none_opened_on_raises() -> None:
    s = CostAwareReversion()
    a = Asset(1, "Normal")
    pos = {a: Position(quantity=1, avg_cost=40.0)}
    ctx = _ctx(date(2026, 5, 1), _history([]), _products([(1, "single")]), pos, 0.0, {a: 50.0})
    with pytest.raises(ValueError, match="opened_on"):
        s.on_bar(ctx)
```

- [ ] **Step 2: Run to verify failure** — ModuleNotFoundError.

- [ ] **Step 3: Implement** — `src/pkmn_quant/strategies/cost_aware_reversion.py`:

```python
"""Long-only mean reversion gated by the round-trip cost hurdle.

Thesis: a card (or box) trading well below its recent high tends to revert
within months — but on TCGplayer the round trip costs ~12.75% in fees plus
shipping both ways, so a dip is only tradeable when the expected rebound
clears that hurdle with margin. The hurdle does the universe filtering:
cheap cards are excluded not by fiat but because 2 * shipping / price
swamps any plausible rebound.

Entry: mark is >= dip_threshold below the dip_window_days high AND
window_high / mark - 1 >= fee_rate + 2 * shipping / mark + min_edge.
Exit: mark >= avg_cost * take_profit, or held >= max_hold_days
(Position.opened_on — set by engine fills and ledger replay alike).
Stateless: single-bar live invocation behaves exactly like a backtest bar.
"""

from __future__ import annotations

import math
from datetime import timedelta

import polars as pl

from pkmn_quant.engine.costs import CostModel
from pkmn_quant.engine.execution import Order
from pkmn_quant.engine.portfolio import Asset
from pkmn_quant.engine.strategy import Context, Strategy


class CostAwareReversion(Strategy):
    def __init__(
        self,
        dip_window_days: int = 30,
        dip_threshold: float = 0.25,
        min_edge: float = 0.05,
        take_profit: float = 1.25,
        max_hold_days: int = 120,
        max_positions: int = 10,
        budget_frac: float = 0.10,
        min_price: float = 3.0,
        costs: CostModel | None = None,
    ) -> None:
        self.dip_window_days = dip_window_days
        self.dip_threshold = dip_threshold
        self.min_edge = min_edge
        self.take_profit = take_profit
        self.max_hold_days = max_hold_days
        self.max_positions = max_positions
        self.budget_frac = budget_frac
        self.min_price = min_price
        self.costs = costs if costs is not None else CostModel()
        self.name = "cost-aware-reversion"

    def on_bar(self, ctx: Context) -> list[Order]:
        orders: list[Order] = []

        # Sells first: the executor fills sequentially on T+1, so sell
        # proceeds are in cash before any buy fill is attempted.
        for asset, pos in sorted(ctx.positions.items(), key=lambda kv: kv[0].product_id):
            if pos.opened_on is None:
                raise ValueError(
                    f"{self.name}: position {asset} has no opened_on; "
                    "engine fills and ledger replay always set it"
                )
            mark = ctx.marks.get(asset)
            too_old = (ctx.today - pos.opened_on).days >= self.max_hold_days
            hit_target = mark is not None and mark >= pos.avg_cost * self.take_profit
            if too_old or hit_target:
                orders.append(Order(asset=asset, quantity=-pos.quantity))

        open_slots = self.max_positions - (len(ctx.positions) - len(orders))
        if open_slots <= 0:
            return orders

        window_start = ctx.today - timedelta(days=self.dip_window_days)
        highs = (
            ctx.history.filter(pl.col("date") >= window_start)
            .group_by(["product_id", "sub_type"])
            .agg(pl.col("market").max().alias("high"))
        )
        candidates: list[tuple[float, Asset, float]] = []
        for r in highs.iter_rows(named=True):
            asset = Asset(product_id=int(r["product_id"]), sub_type=str(r["sub_type"]))
            if asset in ctx.positions:
                continue
            high = float(r["high"])
            mark = ctx.marks.get(asset)
            if mark is None or mark < self.min_price or high <= 0:
                continue
            dip = 1.0 - mark / high
            if dip < self.dip_threshold:
                continue
            rebound = high / mark - 1.0
            hurdle = self.costs.fee_rate + 2 * self.costs.shipping_per_line / mark
            if rebound < hurdle + self.min_edge:
                continue
            candidates.append((-dip, asset, mark))  # deepest dip first

        candidates.sort(key=lambda c: (c[0], c[1].product_id))
        budget = ctx.cash * self.budget_frac
        affordable = [
            (asset, qty)
            for _, asset, mark in candidates
            if (qty := math.floor(budget / mark)) > 0
        ]
        for asset, qty in affordable[:open_slots]:
            orders.append(Order(asset=asset, quantity=qty))
        return orders
```

- [ ] **Step 4: Run tests, all four gates:**

```bash
uv run pytest tests/strategies/test_cost_aware_reversion.py -q
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

- [ ] **Step 5: Commit**

```bash
git add src/pkmn_quant/strategies/cost_aware_reversion.py tests/strategies/test_cost_aware_reversion.py
git commit -m "feat: cost-aware-reversion strategy (round-trip hurdle entries)"
```

---

### Task 6: Register cost-aware-reversion for research

**Files:**
- Modify: `src/pkmn_quant/research/registry.py`
- Test: extend `tests/research/test_registry.py`

- [ ] **Step 1: Write the failing test.** Read `tests/research/test_registry.py` first — if it parametrizes over `REGISTRY`, the new entry may be covered automatically; still add the explicit space-bounds test:

```python
def test_reversion_registered_and_buildable() -> None:
    entry = REGISTRY["cost-aware-reversion"]
    study = optuna.create_study(sampler=optuna.samplers.RandomSampler(seed=1))
    trial = study.ask()
    params = entry.space(trial)
    assert set(params) == {
        "dip_window_days", "dip_threshold", "min_edge", "take_profit", "max_hold_days"
    }
    assert 30 <= int(params["max_hold_days"]) <= 180
    strategy = entry.factory(params)
    assert strategy.name == "cost-aware-reversion"
```

- [ ] **Step 2: verify failure**, then **Step 3: Implement.** In `registry.py`:

```python
from pkmn_quant.strategies.cost_aware_reversion import CostAwareReversion


def _reversion_space(trial: optuna.Trial) -> Params:
    return {
        "dip_window_days": trial.suggest_int("dip_window_days", 14, 90),
        "dip_threshold": trial.suggest_float("dip_threshold", 0.15, 0.50),
        "min_edge": trial.suggest_float("min_edge", 0.02, 0.15),
        "take_profit": trial.suggest_float("take_profit", 1.1, 1.6),
        "max_hold_days": trial.suggest_int("max_hold_days", 30, 180),
    }


def _reversion_factory(p: Params) -> Strategy:
    return CostAwareReversion(
        dip_window_days=int(p["dip_window_days"]),
        dip_threshold=float(p["dip_threshold"]),
        min_edge=float(p["min_edge"]),
        take_profit=float(p["take_profit"]),
        max_hold_days=int(p["max_hold_days"]),
    )
```

and add `"cost-aware-reversion": RegistryEntry(factory=_reversion_factory, space=_reversion_space)` to `REGISTRY`.

- [ ] **Step 4: Run tests, all four gates.** `uv run pkmn walkforward --strategy cost-aware-reversion --help`-level smoke is covered by registry tests; the real run is Task 8.

- [ ] **Step 5: Commit**

```bash
git add src/pkmn_quant/research/registry.py tests/research/test_registry.py
git commit -m "feat: register cost-aware-reversion (optuna space + factory)"
```

---

### Task 7: Live wiring — grow `PORTFOLIO_SAFE_STRATEGIES`

**Files:**
- Modify: `src/pkmn_quant/live/signals.py` (allowlist + comment)
- Test: modify `tests/live/test_signals.py` (one Plan-5 test flips — deliberate)

- [ ] **Step 1: Write the failing tests.** In `tests/live/test_signals.py`:

**DELETE** `test_portfolio_mode_rejects_entry_state_strategies` (Plan 5 asserted dip-buyer is rejected; after the retrofit that is wrong). Replace with these two:

```python
def test_portfolio_mode_guard_still_rejects_unlisted_strategy(
    warehouse: Warehouse, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The allowlist mechanism survives even though every current strategy
    is now a member: shrink it and confirm the clean rejection."""
    import pkmn_quant.live.signals as signals_mod
    from pkmn_quant.engine.portfolio import Portfolio

    monkeypatch.setattr(
        signals_mod, "PORTFOLIO_SAFE_STRATEGIES", frozenset({"sealed-accumulation"})
    )
    with pytest.raises(SignalsError, match="dip-buyer"):
        generate_signals(
            warehouse=warehouse,
            strategy_name="dip-buyer",
            results_dir=tmp_path / "data" / "results",
            portfolio=Portfolio(cash=100.0),
        )


def test_dip_buyer_portfolio_mode_time_exit_end_to_end(
    warehouse: Warehouse, tmp_path: Path
) -> None:
    """A ledger position held past hold_days produces a SELL through the real
    generate_signals path — the whole point of Plan 6."""
    from pkmn_quant.engine.portfolio import Portfolio, Position
    from pkmn_quant.engine.portfolio import Asset as EAsset

    results_dir = tmp_path / "data" / "results"
    seed_wf_artifact(results_dir, strategy="dip-buyer",
                     params={"dip_threshold": 0.3, "hold_days": 30, "take_profit": 5.0})
    pf = Portfolio(cash=100.0)
    # opened_on far in the past relative to the warehouse's latest day:
    # any hold_days in the search space has elapsed; take_profit 5.0 can't
    # fire (mark 100 < 60*5), so the SELL is unambiguously the time exit.
    pf.positions[EAsset(1, "Normal")] = Position(
        quantity=2, avg_cost=60.0, opened_on=date(2020, 1, 1)
    )
    report = generate_signals(
        warehouse=warehouse,
        strategy_name="dip-buyer",
        results_dir=results_dir,
        portfolio=pf,
    )
    sells = [r for r in report.recommendations if r.action == "SELL"]
    [sell] = sells
    assert sell.quantity == 2 and sell.avg_cost == 60.0
```

`seed_wf_artifact` note: read its current definition in this file. If it hardcodes sealed-accumulation, extend it with keyword args `strategy="sealed-accumulation"` and `params=None` (defaulting to its current params) so both tests share it — keep existing call sites working unchanged. The artifact's params dict must be exactly what `generate_signals` feeds the registry factory, so the dip-buyer params above must match `_dip_factory`'s expected keys (`dip_threshold`, `hold_days`, `take_profit`).

- [ ] **Step 2: verify failure** (dip-buyer is still blocked by the Plan-5 allowlist).

- [ ] **Step 3: Implement.** In `src/pkmn_quant/live/signals.py`:

```python
# Strategies whose exit rules read only Context. Since Plan 6, positions
# carry opened_on (engine fills and ledger replay both set it), so hold-day
# and rebalance clocks are reconstructible from a single live bar.
PORTFOLIO_SAFE_STRATEGIES = frozenset(
    {"sealed-accumulation", "dip-buyer", "xs-momentum", "cost-aware-reversion"}
)
```

- [ ] **Step 4: Run tests, all four gates:**

```bash
uv run pytest tests/live/ -q
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

- [ ] **Step 5: Commit**

```bash
git add src/pkmn_quant/live/signals.py tests/live/test_signals.py
git commit -m "feat: all strategies portfolio-safe — allowlist grows, guard test kept"
```

---

### Task 8: Research runs + honest reporting (manual, real data)

No new code. Real-data walk-forwards for the three changed/new strategies, then documentation. Budget ~30–60 min of compute.

- [ ] **Step 1: Run the walk-forwards** (repo root, real warehouse):

```bash
uv run pkmn walkforward --strategy dip-buyer            --start 2024-03-01 --end 2026-06-30 --trials 15
uv run pkmn walkforward --strategy xs-momentum          --start 2024-03-01 --end 2026-06-30 --trials 15
uv run pkmn walkforward --strategy cost-aware-reversion --start 2024-03-01 --end 2026-06-30 --trials 15
```

Each writes a run dir + `walkforward.json` under `data/results/`. STOP and report if any run errors.

- [ ] **Step 2: Update `docs/research-findings-2026-07.md`.** Add a dated section: (a) why dip-buyer/xs-momentum numbers changed (opened_on retrofit — fill-date hold clocks, no more overdue-dumping of partial fills; cite the old docstring bugs); (b) cost-aware-reversion's stitched OOS result vs buy-and-hold sealed and sealed-accumulation; (c) the standard caveat that Sharpe/Sortino are inflated by mark smoothing. Report negative results as negative — the success criterion is a usable short-horizon tool plus an honest record, not beating buy-and-hold.

- [ ] **Step 3: Update `README.md`** (strategy list + one line on the cost hurdle idea and the 1–6 month exit window) and `CLAUDE.md` (status: Plan 6 merged, new test count, `cost_aware_reversion.py` in Layout, PORTFOLIO_SAFE note now says all registry strategies).

- [ ] **Step 4: Live smoke against the real ledger** (whatever it holds by then):

```bash
uv run pkmn signals --strategy cost-aware-reversion --portfolio
uv run pkmn signals --strategy dip-buyer --portfolio
```

Expect clean reports (BUYs gated by the hurdle; SELLs only per the exit rules). STOP and report anything surprising before committing docs.

- [ ] **Step 5: Commit**

```bash
git add docs/research-findings-2026-07.md README.md CLAUDE.md
git commit -m "docs: Plan 6 findings — opened_on retrofits + cost-aware-reversion results"
```

---

**Done criteria (Plan 6):** all gates green; goldens byte-identical; `opened_on` set by engine fills and ledger replay; dip-buyer/xs-momentum stateless and portfolio-safe with re-run walk-forwards; cost-aware-reversion registered, walk-forwarded, and runnable via `pkmn signals --portfolio`; a ledger position aged past its hold window produces a time-exit SELL end-to-end; findings/README/CLAUDE.md current, negative results reported honestly.
