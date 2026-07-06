# pkmn_quant Plan 5: Reinvest Loop (Portfolio + Daily Signals)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A manual-entry position ledger that feeds real holdings into `pkmn signals` (so the strategy's own exit rules produce SELL recommendations), a scheduled `pkmn daily` command with macOS notifications, a dashboard Portfolio tab with an alerts strip, and a paper-trading mode — the buy → hold → take-profit → reinvest loop from the spec (`docs/superpowers/specs/2026-07-06-reinvest-loop-design.md`).

**Architecture:** `src/pkmn_quant/live/ledger.py` is new: an append-only JSONL ledger replayed through the EXISTING engine `Portfolio` class (avg-cost accounting is reused, never reimplemented). `generate_signals` gains an optional `portfolio` argument — positions/cash materialize into the same `Context`, so the backtested exit rule (`mark >= avg_cost * take_profit`) emits SELLs; strategies whose exits need entry dates (dip-buyer, xs-momentum) are rejected with a clean error until the research plan adds `Position.opened_on`. `pkmn daily` chains ingest → signals → artifacts → `osascript` notification. The engine is untouched; goldens stay byte-identical.

**Tech Stack:** existing stack only. No new dependencies (notifications via `osascript` subprocess; scheduling via a launchd plist template).

**Key design decisions (from the spec):**
- Ledger is the single source of truth; marks are always computed from the warehouse at read time, never stored.
- Ledger `price` is per-unit, `fees` is the total non-price cost of the event — identical semantics to the engine's `Fill`, so replay is a direct translation.
- Portfolio mode is opt-in (`--portfolio`); without it, `pkmn signals` is byte-identical to v1.
- `PORTFOLIO_SAFE_STRATEGIES = {"sealed-accumulation"}` — dip-buyer treats unknown entries as overdue and would dump every holding; fail loudly instead.
- Paper mode (Task 8) is the stretch goal: same code path, second ledger file, fills auto-recorded with the engine `CostModel`.

---

### Task 1: The ledger — parse, validate, replay

**Files:**
- Create: `src/pkmn_quant/live/ledger.py`
- Test: `tests/live/test_ledger.py`

The ledger is JSONL at `data/portfolio/ledger.jsonl`. Four kinds:

```jsonl
{"date": "2026-07-01", "kind": "deposit", "amount": 2000.0}
{"date": "2026-07-03", "kind": "buy", "product_id": 1, "sub_type": "Normal", "qty": 2, "price": 18.94, "fees": 5.20}
{"date": "2026-09-15", "kind": "sell", "product_id": 1, "sub_type": "Normal", "qty": 2, "price": 31.00, "fees": 9.05}
{"date": "2026-09-20", "kind": "withdraw", "amount": 500.0}
```

- [ ] **Step 1: Write the failing tests** — `tests/live/test_ledger.py`:

```python
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from pkmn_quant.live.ledger import (
    LedgerError,
    append_event,
    ledger_path,
    load_portfolio,
    make_snapshot,
)

PRODUCTS = pl.DataFrame(
    {
        "product_id": [1, 2],
        "group_id": [1, 1],
        "name": ["Crashed Box", "Other Box"],
        "rarity": [None, None],
        "kind": ["sealed", "sealed"],
        "released_on": [date(2025, 1, 1), date(2025, 1, 1)],
    }
)


def write_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(line + "\n" for line in lines))


def test_replay_hand_derived_accounting(tmp_path: Path) -> None:
    """deposit 1000; buy 2@100 fees 5; sell 1@150 fees 20.
    cash = 1000 - 205 + 130 = 925; position 1@100; realized = -5 + (150-100) - 20 = 25."""
    path = tmp_path / "ledger.jsonl"
    write_lines(
        path,
        [
            '{"date": "2026-07-01", "kind": "deposit", "amount": 1000.0}',
            '{"date": "2026-07-02", "kind": "buy", "product_id": 1, "sub_type": "Normal",'
            ' "qty": 2, "price": 100.0, "fees": 5.0}',
            '{"date": "2026-07-10", "kind": "sell", "product_id": 1, "sub_type": "Normal",'
            ' "qty": 1, "price": 150.0, "fees": 20.0}',
        ],
    )
    pf = load_portfolio(path, PRODUCTS)
    assert pf.cash == pytest.approx(925.0)
    [(asset, pos)] = list(pf.positions.items())
    assert (asset.product_id, asset.sub_type, pos.quantity) == (1, "Normal", 1)
    assert pos.avg_cost == pytest.approx(100.0)
    assert pf.realized_pnl == pytest.approx(25.0)


def test_events_sorted_by_date_then_file_order(tmp_path: Path) -> None:
    """A deposit dated before a buy funds it even if the lines are reversed."""
    path = tmp_path / "ledger.jsonl"
    write_lines(
        path,
        [
            '{"date": "2026-07-02", "kind": "buy", "product_id": 1, "sub_type": "Normal",'
            ' "qty": 1, "price": 100.0, "fees": 0.0}',
            '{"date": "2026-07-01", "kind": "deposit", "amount": 1000.0}',
        ],
    )
    assert load_portfolio(path, PRODUCTS).cash == pytest.approx(900.0)


def test_missing_ledger_is_empty_portfolio(tmp_path: Path) -> None:
    pf = load_portfolio(tmp_path / "nope.jsonl", PRODUCTS)
    assert pf.cash == 0.0 and pf.positions == {}


@pytest.mark.parametrize(
    ("line", "match"),
    [
        ("{not json", "line 1"),
        ('{"date": "2026-07-01", "kind": "teleport", "amount": 1.0}', "unknown kind"),
        ('{"date": "2026-07-01", "kind": "deposit", "amount": -5.0}', "amount"),
        (
            '{"date": "2026-07-01", "kind": "buy", "product_id": 999, "sub_type": "Normal",'
            ' "qty": 1, "price": 1.0, "fees": 0.0}',
            "unknown product",
        ),
        (
            '{"date": "2026-07-01", "kind": "buy", "product_id": 1, "sub_type": "Normal",'
            ' "qty": 1, "price": 1.0, "fees": 0.0}',
            "negative",  # buy with no prior deposit -> negative cash
        ),
    ],
)
def test_validation_errors_name_the_line(tmp_path: Path, line: str, match: str) -> None:
    path = tmp_path / "ledger.jsonl"
    write_lines(path, [line])
    with pytest.raises(LedgerError, match=match):
        load_portfolio(path, PRODUCTS)


def test_oversell_raises_ledger_error(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    write_lines(
        path,
        [
            '{"date": "2026-07-01", "kind": "deposit", "amount": 1000.0}',
            '{"date": "2026-07-02", "kind": "sell", "product_id": 1, "sub_type": "Normal",'
            ' "qty": 1, "price": 100.0, "fees": 0.0}',
        ],
    )
    with pytest.raises(LedgerError, match="line 2"):
        load_portfolio(path, PRODUCTS)


def test_append_event_validates_and_rolls_back(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    append_event(path, {"date": "2026-07-01", "kind": "deposit", "amount": 500.0}, PRODUCTS)
    with pytest.raises(LedgerError):
        append_event(
            path,
            {"date": "2026-07-02", "kind": "buy", "product_id": 1, "sub_type": "Normal",
             "qty": 100, "price": 100.0, "fees": 0.0},  # can't afford
            PRODUCTS,
        )
    # File unchanged by the failed append; the valid deposit still loads.
    assert load_portfolio(path, PRODUCTS).cash == pytest.approx(500.0)
    assert len(path.read_text().strip().splitlines()) == 1


def test_snapshot_values_positions_at_marks(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    write_lines(
        path,
        [
            '{"date": "2026-07-01", "kind": "deposit", "amount": 1000.0}',
            '{"date": "2026-07-02", "kind": "buy", "product_id": 1, "sub_type": "Normal",'
            ' "qty": 2, "price": 100.0, "fees": 0.0}',
        ],
    )
    pf = load_portfolio(path, PRODUCTS)
    from pkmn_quant.engine.portfolio import Asset

    snap = make_snapshot(pf, {Asset(1, "Normal"): 130.0}, {1: "Crashed Box"})
    assert snap.cash == pytest.approx(800.0)
    [row] = snap.positions
    assert row.name == "Crashed Box"
    assert row.unrealized_pnl == pytest.approx(60.0)  # (130-100)*2
    assert snap.equity == pytest.approx(800.0 + 260.0)


def test_snapshot_missing_mark_raises(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    write_lines(
        path,
        [
            '{"date": "2026-07-01", "kind": "deposit", "amount": 1000.0}',
            '{"date": "2026-07-02", "kind": "buy", "product_id": 1, "sub_type": "Weird",'
            ' "qty": 1, "price": 100.0, "fees": 0.0}',
        ],
    )
    pf = load_portfolio(path, PRODUCTS)
    with pytest.raises(LedgerError, match="Weird"):
        make_snapshot(pf, {}, {1: "Crashed Box"})


def test_ledger_path_helper(tmp_path: Path) -> None:
    assert ledger_path(tmp_path) == tmp_path / "data" / "portfolio" / "ledger.jsonl"
    assert ledger_path(tmp_path, paper=True) == tmp_path / "data" / "portfolio" / "paper.jsonl"
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/live/test_ledger.py -q` → ModuleNotFoundError.

- [ ] **Step 3: Implement** — `src/pkmn_quant/live/ledger.py`:

```python
"""Append-only JSONL trade ledger replayed through the engine's Portfolio.

The ledger is the single source of truth for what the user actually did;
marks/valuations are never stored, always computed from the warehouse at
read time. `price` is per-unit and `fees` is the total non-price cost of
the event (shipping on buys; marketplace cut + shipping on sells) —
identical semantics to engine Fill, so replay is a direct translation and
avg-cost/realized-P&L math is the backtester's math by construction.

Validation is strict and names the offending line: a ledger that sells
more than it holds or spends cash it does not have is mis-entered.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import polars as pl

from pkmn_quant.engine.portfolio import Asset, Fill, Portfolio

KINDS = frozenset({"deposit", "withdraw", "buy", "sell"})


class LedgerError(Exception):
    """User-facing ledger failure (clean CLI message)."""


@dataclass(frozen=True)
class LedgerEvent:
    line_no: int
    day: date
    kind: str
    amount: float | None = None  # deposit/withdraw
    asset: Asset | None = None  # buy/sell
    qty: int | None = None
    price: float | None = None
    fees: float | None = None


@dataclass(frozen=True)
class PositionView:
    product_id: int
    sub_type: str
    name: str
    quantity: int
    avg_cost: float
    mark: float
    unrealized_pnl: float


@dataclass(frozen=True)
class Snapshot:
    cash: float
    realized_pnl: float
    equity: float
    positions: list[PositionView]


def ledger_path(root: Path, paper: bool = False) -> Path:
    name = "paper.jsonl" if paper else "ledger.jsonl"
    return root / "data" / "portfolio" / name


def _parse_line(line_no: int, raw: str) -> LedgerEvent:
    def fail(msg: str) -> LedgerError:
        return LedgerError(f"ledger line {line_no}: {msg}")

    try:
        obj = json.loads(raw)
    except ValueError as exc:
        raise fail(f"invalid JSON ({exc})") from exc
    if not isinstance(obj, dict):
        raise fail("event must be a JSON object")
    try:
        day = date.fromisoformat(str(obj["date"]))
        kind = str(obj["kind"])
    except (KeyError, ValueError) as exc:
        raise fail(f"missing/invalid date or kind ({exc!r})") from exc
    if kind not in KINDS:
        raise fail(f"unknown kind {kind!r}; choose from {sorted(KINDS)}")

    if kind in ("deposit", "withdraw"):
        try:
            amount = float(obj["amount"])
        except (KeyError, TypeError, ValueError) as exc:
            raise fail(f"missing/invalid amount ({exc!r})") from exc
        if amount <= 0:
            raise fail(f"amount must be positive, got {amount}")
        return LedgerEvent(line_no=line_no, day=day, kind=kind, amount=amount)

    try:
        asset = Asset(product_id=int(obj["product_id"]), sub_type=str(obj["sub_type"]))
        qty = int(obj["qty"])
        price = float(obj["price"])
        fees = float(obj.get("fees", 0.0))
    except (KeyError, TypeError, ValueError) as exc:
        raise fail(f"missing/invalid trade field ({exc!r})") from exc
    if qty <= 0:
        raise fail(f"qty must be positive, got {qty}")
    if price <= 0:
        raise fail(f"price must be positive, got {price}")
    if fees < 0:
        raise fail(f"fees must be non-negative, got {fees}")
    return LedgerEvent(
        line_no=line_no, day=day, kind=kind, asset=asset, qty=qty, price=price, fees=fees
    )


def _parse_lines(lines: list[str]) -> list[LedgerEvent]:
    events = [
        _parse_line(i, raw) for i, raw in enumerate(lines, start=1) if raw.strip()
    ]
    # Stable sort: date order, file order within a date.
    return sorted(events, key=lambda e: e.day)


def _replay(events: list[LedgerEvent], products: pl.DataFrame) -> Portfolio:
    known_ids = set(products["product_id"].to_list())
    pf = Portfolio(cash=0.0)
    for e in events:
        def fail(msg: str, _e: LedgerEvent = e) -> LedgerError:
            return LedgerError(f"ledger line {_e.line_no}: {msg}")

        if e.kind == "deposit":
            assert e.amount is not None
            pf.cash += e.amount
            continue
        if e.kind == "withdraw":
            assert e.amount is not None
            pf.cash -= e.amount
        else:
            assert e.asset is not None and e.qty and e.price is not None
            if e.asset.product_id not in known_ids:
                raise fail(f"unknown product_id {e.asset.product_id}")
            signed = e.qty if e.kind == "buy" else -e.qty
            fill = Fill(
                day=e.day, asset=e.asset, quantity=signed, price=e.price, fees=e.fees or 0.0
            )
            try:
                pf.apply(fill)
            except ValueError as exc:  # oversell from Portfolio._sell
                raise fail(str(exc)) from exc
        if pf.cash < 0:
            raise fail(f"cash goes negative ({pf.cash:.2f}) — mis-entered ledger?")
    return pf


def load_portfolio(path: Path, products: pl.DataFrame) -> Portfolio:
    """Replay the ledger into a Portfolio. Missing file = empty portfolio."""
    if not path.exists():
        return Portfolio(cash=0.0)
    lines = path.read_text().splitlines()
    return _replay(_parse_lines(lines), products)


def append_event(path: Path, event: dict[str, object], products: pl.DataFrame) -> None:
    """Validate existing + new event together; only then append to the file."""
    existing = path.read_text().splitlines() if path.exists() else []
    candidate = existing + [json.dumps(event)]
    _replay(_parse_lines(candidate), products)  # raises LedgerError if invalid
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(line + "\n" for line in candidate))


def make_snapshot(
    pf: Portfolio, marks: dict[Asset, float], names: dict[int, str]
) -> Snapshot:
    rows: list[PositionView] = []
    missing: list[Asset] = []
    for asset, pos in sorted(pf.positions.items(), key=lambda kv: kv[0].product_id):
        mark = marks.get(asset)
        if mark is None:
            missing.append(asset)
            continue
        rows.append(
            PositionView(
                product_id=asset.product_id,
                sub_type=asset.sub_type,
                name=names.get(asset.product_id, f"product {asset.product_id}"),
                quantity=pos.quantity,
                avg_cost=pos.avg_cost,
                mark=mark,
                unrealized_pnl=(mark - pos.avg_cost) * pos.quantity,
            )
        )
    if missing:
        raise LedgerError(
            f"no warehouse mark for held asset(s): "
            f"{[f'{a.product_id}/{a.sub_type}' for a in missing]}"
        )
    value = sum(r.mark * r.quantity for r in rows)
    return Snapshot(
        cash=pf.cash, realized_pnl=pf.realized_pnl, equity=pf.cash + value, positions=rows
    )
```

Implementation notes: mypy strict will flag the closure-over-loop-variable `fail` helpers if written differently — the `_e: LedgerEvent = e` default-arg binding is deliberate. If ruff B023 still complains, inline the f-strings instead of the helper.

- [ ] **Step 4: Run tests, then all four gates:**

```bash
uv run pytest tests/live/test_ledger.py -q
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

- [ ] **Step 5: Commit**

```bash
git add src/pkmn_quant/live/ledger.py tests/live/test_ledger.py
git commit -m "feat: JSONL trade ledger replayed through engine Portfolio"
```

---

### Task 2: `pkmn portfolio` CLI

**Files:**
- Modify: `src/pkmn_quant/cli.py`
- Test: `tests/test_cli_portfolio.py`

- [ ] **Step 1: Write the failing tests** — `tests/test_cli_portfolio.py`:

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


def seed(root: Path) -> None:
    w = Warehouse(Paths(root=root))
    start = date(2025, 1, 1)
    for i in range(10):
        d = start + timedelta(days=i)
        w.write_prices(d, pl.DataFrame([price_row(d, 1, 100.0)], schema=PRICE_SCHEMA))
    w.write_products(
        pl.DataFrame(
            {
                "product_id": [1],
                "group_id": [1],
                "name": ["Crashed Box"],
                "rarity": [None],
                "kind": ["sealed"],
                "released_on": [start],
            }
        )
    )


def test_deposit_buy_show_roundtrip(tmp_path: Path) -> None:
    seed(tmp_path)
    runner = CliRunner()
    r1 = runner.invoke(
        app, ["portfolio", "deposit", "--amount", "1000", "--date", "2025-01-02",
              "--root", str(tmp_path)]
    )
    assert r1.exit_code == 0, r1.output
    r2 = runner.invoke(
        app, ["portfolio", "buy", "--product-id", "1", "--sub-type", "Normal",
              "--qty", "2", "--price", "90", "--fees", "2", "--date", "2025-01-03",
              "--root", str(tmp_path)]
    )
    assert r2.exit_code == 0, r2.output
    r3 = runner.invoke(app, ["portfolio", "show", "--root", str(tmp_path)])
    assert r3.exit_code == 0, r3.output
    assert "Crashed Box" in r3.output
    assert "818.00" in r3.output  # cash 1000 - 180 - 2
    assert "20.00" in r3.output  # unrealized (100-90)*2
    assert (tmp_path / "data" / "portfolio" / "ledger.jsonl").exists()


def test_invalid_entry_rejected_cleanly(tmp_path: Path) -> None:
    seed(tmp_path)
    result = CliRunner().invoke(
        app, ["portfolio", "buy", "--product-id", "1", "--sub-type", "Normal",
              "--qty", "1", "--price", "90", "--root", str(tmp_path)]  # no deposit
    )
    assert result.exit_code != 0
    assert "negative" in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert not (tmp_path / "data" / "portfolio" / "ledger.jsonl").exists()


def test_show_empty_portfolio(tmp_path: Path) -> None:
    seed(tmp_path)
    result = CliRunner().invoke(app, ["portfolio", "show", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "empty" in result.output.lower()
```

- [ ] **Step 2: verify failure** (`portfolio` is not a command), then **Step 3: Implement.** In `src/pkmn_quant/cli.py`, add below `app = typer.Typer(...)`:

```python
portfolio_app = typer.Typer(no_args_is_help=True, help="Record and inspect real positions.")
app.add_typer(portfolio_app, name="portfolio")
```

The `tuple["Warehouse", ...]` forward reference needs the name importable for mypy without paying the import at runtime — add to cli.py's top imports:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pkmn_quant.data.warehouse import Warehouse
```

Shared helpers + subcommands (place above the `version` command; house style: deferred imports):

```python
def _portfolio_deps(root: Path) -> tuple["Warehouse", pl.DataFrame, Path]:
    """(warehouse, products, ledger file) — shared by the portfolio subcommands."""
    from pkmn_quant.data.warehouse import Warehouse
    from pkmn_quant.live.ledger import ledger_path

    warehouse = Warehouse(Paths(root=root))
    return warehouse, warehouse.load_products(), ledger_path(root)


def _append_or_die(path: Path, event: dict[str, object], products: pl.DataFrame) -> None:
    from pkmn_quant.live.ledger import LedgerError, append_event

    try:
        append_event(path, event, products)
    except LedgerError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"recorded: {event}")


@portfolio_app.command()
def deposit(
    amount: float = typer.Option(..., help="Cash added to the portfolio."),
    date: str | None = typer.Option(None, help="Event date (YYYY-MM-DD); default today."),
    root: Path = typer.Option(Path("."), help="Project root holding the data/ directory."),
) -> None:
    """Record a cash deposit."""
    _, products, path = _portfolio_deps(root)
    day = date or dt.date.today().isoformat()
    _append_or_die(path, {"date": day, "kind": "deposit", "amount": amount}, products)


@portfolio_app.command()
def withdraw(
    amount: float = typer.Option(..., help="Cash removed from the portfolio."),
    date: str | None = typer.Option(None, help="Event date (YYYY-MM-DD); default today."),
    root: Path = typer.Option(Path("."), help="Project root holding the data/ directory."),
) -> None:
    """Record a cash withdrawal."""
    _, products, path = _portfolio_deps(root)
    day = date or dt.date.today().isoformat()
    _append_or_die(path, {"date": day, "kind": "withdraw", "amount": amount}, products)


def _trade(kind: str, product_id: int, sub_type: str, qty: int, price: float,
           fees: float, date: str | None, root: Path) -> None:
    _, products, path = _portfolio_deps(root)
    day = date or dt.date.today().isoformat()
    _append_or_die(
        path,
        {"date": day, "kind": kind, "product_id": product_id, "sub_type": sub_type,
         "qty": qty, "price": price, "fees": fees},
        products,
    )


@portfolio_app.command()
def buy(
    product_id: int = typer.Option(..., help="TCGplayer product id (see signals output)."),
    sub_type: str = typer.Option("Normal", help="Printing sub-type, e.g. Normal/Holofoil."),
    qty: int = typer.Option(..., help="Units bought."),
    price: float = typer.Option(..., help="Per-unit price actually paid."),
    fees: float = typer.Option(0.0, help="Total non-price cost (shipping etc.)."),
    date: str | None = typer.Option(None, help="Trade date (YYYY-MM-DD); default today."),
    root: Path = typer.Option(Path("."), help="Project root holding the data/ directory."),
) -> None:
    """Record a real purchase."""
    _trade("buy", product_id, sub_type, qty, price, fees, date, root)


@portfolio_app.command()
def sell(
    product_id: int = typer.Option(..., help="TCGplayer product id."),
    sub_type: str = typer.Option("Normal", help="Printing sub-type."),
    qty: int = typer.Option(..., help="Units sold."),
    price: float = typer.Option(..., help="Per-unit sale price."),
    fees: float = typer.Option(0.0, help="Total fees + shipping kept by the marketplace."),
    date: str | None = typer.Option(None, help="Trade date (YYYY-MM-DD); default today."),
    root: Path = typer.Option(Path("."), help="Project root holding the data/ directory."),
) -> None:
    """Record a real sale."""
    _trade("sell", product_id, sub_type, qty, price, fees, date, root)


@portfolio_app.command()
def show(
    root: Path = typer.Option(Path("."), help="Project root holding the data/ directory."),
) -> None:
    """Positions, cash, and P&L valued at the latest warehouse marks."""
    from pkmn_quant.engine.data import MarketData
    from pkmn_quant.live.ledger import LedgerError, load_portfolio, make_snapshot

    warehouse, products, path = _portfolio_deps(root)
    try:
        pf = load_portfolio(path, products)
        if not pf.positions and pf.cash == 0.0:
            typer.echo("portfolio is empty — record a deposit first")
            return
        days = warehouse.stored_days()
        if not days:
            raise LedgerError("warehouse has no price data; run `pkmn ingest` first")
        latest = days[-1]
        market = MarketData.from_warehouse(warehouse, latest, latest, warmup_days=365)
        names = {
            int(r["product_id"]): str(r["name"])
            for r in products.select("product_id", "name").iter_rows(named=True)
        }
        snap = make_snapshot(pf, market.marks_on(latest), names)
    except LedgerError as exc:
        raise typer.BadParameter(str(exc)) from exc

    typer.echo(f"as of {latest}")
    for r in snap.positions:
        typer.echo(
            f"{r.product_id:>8}  {r.name} ({r.sub_type})  x{r.quantity}"
            f"  avg ${r.avg_cost:.2f}  mark ${r.mark:.2f}"
            f"  unrealized ${r.unrealized_pnl:+.2f}"
        )
    typer.echo(f"cash: ${snap.cash:.2f}")
    typer.echo(f"realized P&L: ${snap.realized_pnl:+.2f}")
    typer.echo(f"equity: ${snap.equity:.2f}")
```

Note: the `date: str = typer.Option(None, ...)` parameter shadows the `dt.date` type only if you import `date` from datetime in cli.py — cli.py uses `import datetime as dt`, so `dt.date.today()` is safe. mypy: `date` params are `str | None` at runtime with `None` default; declare them as `date: str = typer.Option(None, ...)` and mypy will complain — use `date: str | None = typer.Option(None, ...)`.

- [ ] **Step 4: Run tests, all four gates:**

```bash
uv run pytest tests/test_cli_portfolio.py -q
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

- [ ] **Step 5: Commit**

```bash
git add src/pkmn_quant/cli.py tests/test_cli_portfolio.py
git commit -m "feat: pkmn portfolio CLI (deposit/withdraw/buy/sell/show)"
```

---

### Task 3: Signals against real positions

**Files:**
- Modify: `src/pkmn_quant/live/signals.py`
- Modify: `src/pkmn_quant/live/report.py`
- Test: extend `tests/live/test_signals.py`, `tests/live/test_report.py`

- [ ] **Step 1: Write the failing tests.** Append to `tests/live/test_signals.py` (reuses that file's `warehouse` fixture and `seed_wf_artifact` — the fixture's product peaked at 200 then fell to 100):

```python
def test_portfolio_mode_emits_sell_at_take_profit(
    warehouse: Warehouse, tmp_path: Path
) -> None:
    """Bought at 60, mark is 100, take_profit 1.5 -> 100 >= 90 fires the exit."""
    from pkmn_quant.engine.portfolio import Portfolio, Position
    from pkmn_quant.engine.portfolio import Asset as EAsset

    results_dir = tmp_path / "data" / "results"
    seed_wf_artifact(results_dir)
    pf = Portfolio(cash=500.0)
    pf.positions[EAsset(1, "Normal")] = Position(quantity=2, avg_cost=60.0)
    report = generate_signals(
        warehouse=warehouse,
        strategy_name="sealed-accumulation",
        results_dir=results_dir,
        portfolio=pf,
    )
    sells = [r for r in report.recommendations if r.action == "SELL"]
    [sell] = sells
    assert sell.product_id == 1 and sell.quantity == 2
    assert sell.avg_cost == 60.0
    assert sell.gain_pct == pytest.approx(100.0 / 60.0 - 1.0)
    assert report.portfolio_snapshot is not None
    assert report.portfolio_snapshot.cash == 500.0
    assert report.portfolio_snapshot.equity == pytest.approx(500.0 + 200.0)


def test_portfolio_mode_rejects_entry_state_strategies(
    warehouse: Warehouse, tmp_path: Path
) -> None:
    from pkmn_quant.engine.portfolio import Portfolio

    with pytest.raises(SignalsError, match="dip-buyer"):
        generate_signals(
            warehouse=warehouse,
            strategy_name="dip-buyer",
            results_dir=tmp_path / "data" / "results",
            portfolio=Portfolio(cash=100.0),
        )


def test_cash_and_portfolio_are_mutually_exclusive(
    warehouse: Warehouse, tmp_path: Path
) -> None:
    from pkmn_quant.engine.portfolio import Portfolio

    with pytest.raises(SignalsError, match="either"):
        generate_signals(
            warehouse=warehouse,
            strategy_name="sealed-accumulation",
            results_dir=tmp_path / "data" / "results",
            cash=1000.0,
            portfolio=Portfolio(cash=100.0),
        )
```

Existing tests in the file call `generate_signals(..., cash=1000.0, ...)` by keyword — they keep working unchanged.

Append to `tests/live/test_report.py`:

```python
def test_markdown_renders_portfolio_section_and_exits() -> None:
    from pkmn_quant.live.ledger import PositionView, Snapshot

    sell = Recommendation(
        action="SELL", product_id=1, sub_type="Normal", name="Crashed Box",
        quantity=2, market_price=100.0, notional=200.0, avg_cost=60.0,
        gain_pct=100.0 / 60.0 - 1.0,
    )
    snap = Snapshot(
        cash=500.0, realized_pnl=25.0, equity=700.0,
        positions=[PositionView(1, "Normal", "Crashed Box", 2, 60.0, 100.0, 80.0)],
    )
    report = _report([sell])
    report = SignalReport(
        as_of=report.as_of, strategy=report.strategy, params=report.params,
        wf_summary=report.wf_summary, wf_run_dir=report.wf_run_dir,
        recommendations=[sell], portfolio_snapshot=snap,
    )
    md = render_signals_markdown(report)
    assert "## Portfolio" in md
    assert "$500.00" in md  # cash
    assert "+66.7%" in md  # exit gain line
```

- [ ] **Step 2: verify failure**, then **Step 3: Implement.**

In `src/pkmn_quant/live/signals.py`:

1. Add imports: `from pkmn_quant.engine.portfolio import Portfolio, Position` and `from pkmn_quant.live.ledger import Snapshot, make_snapshot`.
2. Add the allowlist constant below `DEFAULT_WARMUP_DAYS`:

```python
# Strategies whose exit rules read only Context (positions.avg_cost + marks).
# dip-buyer / xs-momentum keep hold-day clocks in strategy-internal state a
# single live bar cannot reconstruct (dip-buyer treats unknown entries as
# overdue and would dump every holding). Research plan adds Position.opened_on.
PORTFOLIO_SAFE_STRATEGIES = frozenset({"sealed-accumulation"})
```

3. Extend `Recommendation` (new optional fields at the end):

```python
    avg_cost: float | None = None  # SELLs in portfolio mode
    gain_pct: float | None = None
```

4. Extend `SignalReport` (new optional field at the end):

```python
    portfolio_snapshot: Snapshot | None = None
```

5. Change `generate_signals` signature and body:

```python
def generate_signals(
    warehouse: Warehouse,
    strategy_name: str,
    results_dir: Path,
    cash: float | None = None,
    portfolio: Portfolio | None = None,
    warmup_days: int = DEFAULT_WARMUP_DAYS,
) -> SignalReport:
```

Right after the registry lookup, add:

```python
    if (cash is None) == (portfolio is None):
        raise SignalsError("provide either cash (hypothetical) or portfolio (ledger), not both")
    if portfolio is not None and strategy_name not in PORTFOLIO_SAFE_STRATEGIES:
        raise SignalsError(
            f"{strategy_name!r} cannot run against real positions: its exit rule"
            f" needs entry dates the live Context does not carry yet"
            f" (supported: {sorted(PORTFOLIO_SAFE_STRATEGIES)})"
        )
```

Replace the Context construction block so positions/cash come from the portfolio when given (positions deep-copied at the trust boundary, matching the backtest loop):

```python
    if portfolio is not None:
        ctx_cash = portfolio.cash
        ctx_positions = {
            a: Position(quantity=p.quantity, avg_cost=p.avg_cost)
            for a, p in portfolio.positions.items()
        }
    else:
        assert cash is not None
        ctx_cash = cash
        ctx_positions = {}
    ctx = Context(
        today=latest,
        history=market.history_until(latest),
        products=warehouse.load_products(),
        positions=ctx_positions,
        cash=ctx_cash,
        marks=market.marks_on(latest),
    )
```

In the recommendation loop, populate the SELL extras (portfolio positions are authoritative for basis):

```python
        held = portfolio.positions.get(order.asset) if portfolio is not None else None
        avg_cost = held.avg_cost if held is not None and order.quantity < 0 else None
        recommendations.append(
            Recommendation(
                action="BUY" if order.quantity > 0 else "SELL",
                product_id=order.asset.product_id,
                sub_type=order.asset.sub_type,
                name=names.get(order.asset.product_id, f"product {order.asset.product_id}"),
                quantity=qty,
                market_price=mark,
                notional=round(qty * mark, 2),
                avg_cost=avg_cost,
                gain_pct=(mark / avg_cost - 1.0) if avg_cost else None,
            )
        )
```

Before the return, build the snapshot in portfolio mode:

```python
    snapshot = (
        make_snapshot(portfolio, marks, names) if portfolio is not None else None
    )
```

and pass `portfolio_snapshot=snapshot` to `SignalReport`. (`make_snapshot` raising `LedgerError` for a mark-less held asset is fine — the CLI catches both error types.)

In `src/pkmn_quant/live/report.py`, inside `render_signals_markdown`, after the recommendations table block and before the footer, add:

```python
    if report.portfolio_snapshot is not None:
        snap = report.portfolio_snapshot
        lines += ["", "## Portfolio", ""]
        exits = [r for r in report.recommendations if r.action == "SELL"]
        for r in exits:
            assert r.avg_cost is not None and r.gain_pct is not None
            lines.append(
                f"- EXIT {r.name}: {r.quantity} @ mark ${r.market_price:.2f},"
                f" basis ${r.avg_cost:.2f}, gain {r.gain_pct:+.1%}"
            )
        for p in snap.positions:
            lines.append(
                f"- HOLD {p.name} ({p.sub_type}) x{p.quantity}: avg ${p.avg_cost:.2f},"
                f" mark ${p.mark:.2f}, unrealized ${p.unrealized_pnl:+.2f}"
            )
        lines += [
            f"- cash: ${snap.cash:.2f}",
            f"- realized P&L: ${snap.realized_pnl:+.2f}",
            f"- equity: ${snap.equity:.2f}",
        ]
```

`signals_to_json` needs no change — `asdict` recurses into `Snapshot`/`PositionView`.

- [ ] **Step 4: Run tests, all four gates.** All existing signals/report/CLI tests must pass unchanged (they use `cash=` keyword).

- [ ] **Step 5: Commit**

```bash
git add src/pkmn_quant/live/signals.py src/pkmn_quant/live/report.py tests/live/test_signals.py tests/live/test_report.py
git commit -m "feat: portfolio mode for signal generation (real positions -> exits)"
```

---

### Task 4: `--portfolio` on the signals CLI

**Files:**
- Modify: `src/pkmn_quant/cli.py` (signals command)
- Test: extend `tests/test_cli_signals.py`

- [ ] **Step 1: Write the failing test.** Append to `tests/test_cli_signals.py`:

```python
def test_signals_portfolio_flag_end_to_end(tmp_path: Path) -> None:
    """Ledger holds 2 units bought at 35; latest mark 100 >= 35*take_profit for
    every take_profit in the search space (max 2.5 -> threshold 87.5), so the
    SELL fires regardless of what optuna picked."""
    seed(tmp_path)
    runner = CliRunner()
    wf = runner.invoke(app, [
        "walkforward", "--strategy", "sealed-accumulation",
        "--start", "2025-01-01", "--end", "2025-04-11",
        "--is-days", "30", "--oos-days", "30", "--trials", "2",
        "--cash", "1000", "--root", str(tmp_path),
    ])
    assert wf.exit_code == 0, wf.output
    for args in (
        ["portfolio", "deposit", "--amount", "1000", "--date", "2025-01-02"],
        ["portfolio", "buy", "--product-id", "1", "--sub-type", "Normal",
         "--qty", "2", "--price", "35", "--date", "2025-01-03"],
    ):
        r = runner.invoke(app, [*args, "--root", str(tmp_path)])
        assert r.exit_code == 0, r.output

    result = runner.invoke(
        app, ["signals", "--strategy", "sealed-accumulation", "--portfolio",
              "--root", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert "SELL" in result.output
    assert "## Portfolio" in result.output


def test_signals_portfolio_and_cash_conflict(tmp_path: Path) -> None:
    seed(tmp_path)
    result = CliRunner().invoke(
        app, ["signals", "--strategy", "sealed-accumulation", "--portfolio",
              "--cash", "5000", "--root", str(tmp_path)]
    )
    assert result.exit_code != 0
    assert result.exception is None or isinstance(result.exception, SystemExit)
```

Note on the take-profit trigger: the latest mark is 100 and the ledger basis is 35, so `100 >= 35 * take_profit` holds for the entire search space (`take_profit` ≤ 2.5 → threshold ≤ 87.5) — the SELL is deterministic no matter which params optuna picked.

- [ ] **Step 2: verify failure**, then **Step 3: Implement.** In the `signals` command in `cli.py`:

Change the `cash` option and add `portfolio_flag`:

```python
    cash: float | None = typer.Option(
        None, "--cash", help="Hypothetical cash for position sizing (default 10000)."
    ),
    portfolio_flag: bool = typer.Option(
        False, "--portfolio", help="Run against the real ledger (positions + cash)."
    ),
```

Body changes (deferred imports gain `ledger_path`, `load_portfolio`, `LedgerError`):

```python
    from pkmn_quant.live.ledger import LedgerError, ledger_path, load_portfolio

    if portfolio_flag and cash is not None:
        raise typer.BadParameter("--cash and --portfolio are mutually exclusive")
    warehouse = Warehouse(Paths(root=root))
    try:
        pf = (
            load_portfolio(ledger_path(root), warehouse.load_products())
            if portfolio_flag
            else None
        )
        report = generate_signals(
            warehouse=warehouse,
            strategy_name=strategy,
            results_dir=results_dir,
            cash=None if portfolio_flag else (cash if cash is not None else 10_000.0),
            portfolio=pf,
            warmup_days=warmup_days,
        )
    except (SignalsError, LedgerError) as exc:
        raise typer.BadParameter(str(exc)) from exc
```

- [ ] **Step 4: Run tests, all four gates.**

- [ ] **Step 5: Commit**

```bash
git add src/pkmn_quant/cli.py tests/test_cli_signals.py
git commit -m "feat: --portfolio flag on pkmn signals"
```

---

### Task 5: Notifications module + `pkmn daily`

**Files:**
- Create: `src/pkmn_quant/live/notify.py`
- Modify: `src/pkmn_quant/cli.py`
- Test: `tests/test_cli_daily.py`

- [ ] **Step 1: Write the failing tests** — `tests/test_cli_daily.py`:

```python
import json
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest
from typer.testing import CliRunner

import pkmn_quant.live.notify as notify
from pkmn_quant.cli import app
from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA
from pkmn_quant.data.warehouse import Warehouse
from tests.helpers import price_row


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(notify, "send_notification", lambda t, b: calls.append((t, b)))
    return calls


def seed(root: Path) -> None:
    """Product crashes 200 -> 100 and is 121 days old: sealed-accumulation buys."""
    w = Warehouse(Paths(root=root))
    start = date(2025, 1, 1)
    for i in range(121):
        d = start + timedelta(days=i)
        price = 200.0 if i < 30 else 100.0
        w.write_prices(d, pl.DataFrame([price_row(d, 1, price)], schema=PRICE_SCHEMA))
    w.write_products(
        pl.DataFrame(
            {
                "product_id": [1],
                "group_id": [1],
                "name": ["Crashed Box"],
                "rarity": [None],
                "kind": ["sealed"],
                "released_on": [start],
            }
        )
    )


def run_walkforward(runner: CliRunner, root: Path) -> None:
    r = runner.invoke(app, [
        "walkforward", "--strategy", "sealed-accumulation",
        "--start", "2025-01-01", "--end", "2025-04-11",
        "--is-days", "30", "--oos-days", "30", "--trials", "2",
        "--cash", "1000", "--root", str(root),
    ])
    assert r.exit_code == 0, r.output


def test_daily_writes_artifacts_and_notifies_when_actionable(
    tmp_path: Path, sent: list[tuple[str, str]]
) -> None:
    seed(tmp_path)
    runner = CliRunner()
    run_walkforward(runner, tmp_path)
    for args in (
        ["portfolio", "deposit", "--amount", "1000", "--date", "2025-01-02"],
    ):
        r = runner.invoke(app, [*args, "--root", str(tmp_path)])
        assert r.exit_code == 0, r.output

    result = runner.invoke(app, ["daily", "--skip-ingest", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output

    daily_dirs = sorted((tmp_path / "data" / "results").glob("daily-*"))
    assert len(daily_dirs) == 1
    meta = json.loads((daily_dirs[0] / "daily.json").read_text())
    assert meta["status"] == "ok"
    assert meta["strategy"] == "sealed-accumulation"
    assert meta["as_of"] == "2025-05-01"
    assert meta["n_buys"] >= 1  # the crashed box qualifies for entry
    assert (daily_dirs[0] / "signals.md").exists()
    assert (daily_dirs[0] / "signals.json").exists()
    assert len(sent) == 1  # actionable -> exactly one notification


def test_daily_silent_when_nothing_actionable(
    tmp_path: Path, sent: list[tuple[str, str]]
) -> None:
    """No cash in the ledger -> no affordable entries -> no notification."""
    seed(tmp_path)
    runner = CliRunner()
    run_walkforward(runner, tmp_path)
    result = runner.invoke(app, ["daily", "--skip-ingest", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    meta = json.loads(
        next((tmp_path / "data" / "results").glob("daily-*/daily.json")).read_text()
    )
    assert meta["status"] == "ok" and meta["n_buys"] == 0 and meta["n_sells"] == 0
    assert sent == []


def test_daily_failure_writes_error_status_and_notifies(
    tmp_path: Path, sent: list[tuple[str, str]]
) -> None:
    """No walk-forward artifact -> SignalsError -> status error, nonzero exit."""
    seed(tmp_path)
    result = CliRunner().invoke(app, ["daily", "--skip-ingest", "--root", str(tmp_path)])
    assert result.exit_code != 0
    assert result.exception is None or isinstance(result.exception, SystemExit)
    meta = json.loads(
        next((tmp_path / "data" / "results").glob("daily-*/daily.json")).read_text()
    )
    assert meta["status"] == "error"
    assert "walkforward" in meta["error"] or "walk-forward" in meta["error"]
    assert len(sent) == 1
```

- [ ] **Step 2: verify failure**, then **Step 3: Implement.**

`src/pkmn_quant/live/notify.py`:

```python
"""macOS banner notifications via osascript. No-op on other platforms.

Kept in its own module so tests (and the daily CLI) can monkeypatch
`send_notification` without touching subprocess.
"""

from __future__ import annotations

import json
import subprocess
import sys


def send_notification(title: str, body: str) -> None:
    if sys.platform != "darwin":  # pragma: no cover
        return
    script = f"display notification {json.dumps(body)} with title {json.dumps(title)}"
    subprocess.run(["osascript", "-e", script], check=False, capture_output=True)
```

`daily` command in `cli.py` (import `pkmn_quant.live.notify` as a module so monkeypatching works):

```python
@app.command()
def daily(
    strategy: str = typer.Option(
        "sealed-accumulation", help="Strategy to run against the ledger."
    ),
    skip_ingest: bool = typer.Option(
        False, "--skip-ingest", help="Skip fetching new price days (tests/offline)."
    ),
    root: Path = typer.Option(Path("."), help="Project root holding the data/ directory."),
) -> None:
    """The morning loop: ingest missing days, run signals against the ledger,
    write artifacts, notify when actionable. Designed for launchd/cron."""
    import json as _json

    from pkmn_quant.data.warehouse import Warehouse
    from pkmn_quant.live import notify
    from pkmn_quant.live.ledger import LedgerError, ledger_path, load_portfolio
    from pkmn_quant.live.report import render_signals_markdown, signals_to_json
    from pkmn_quant.live.signals import SignalsError, generate_signals

    today = dt.date.today()
    out_dir = root / "data" / "results" / f"daily-{today.isoformat()}"
    out_dir.mkdir(parents=True, exist_ok=True)
    warehouse = Warehouse(Paths(root=root))

    ingest_error: str | None = None
    if not skip_ingest:
        try:
            days = warehouse.stored_days()
            yesterday = today - dt.timedelta(days=1)
            if days and days[-1] < yesterday:
                ingest_range(Paths(root=root), days[-1] + dt.timedelta(days=1), yesterday)
        except Exception as exc:  # noqa: BLE001 — scheduled run must never die silently
            ingest_error = f"ingest failed: {exc}"

    def finish(status: str, error: str | None, n_buys: int, n_sells: int,
               as_of: str | None) -> None:
        (out_dir / "daily.json").write_text(
            _json.dumps(
                {"date": today.isoformat(), "strategy": strategy, "status": status,
                 "error": error, "n_buys": n_buys, "n_sells": n_sells, "as_of": as_of},
                indent=2,
            )
            + "\n"
        )

    try:
        pf = load_portfolio(ledger_path(root), warehouse.load_products())
        report = generate_signals(
            warehouse=warehouse, strategy_name=strategy,
            results_dir=root / "data" / "results", portfolio=pf,
        )
    except (SignalsError, LedgerError) as exc:
        finish("error", str(exc), 0, 0, None)
        notify.send_notification("pkmn daily FAILED", str(exc))
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc

    (out_dir / "signals.md").write_text(render_signals_markdown(report))
    (out_dir / "signals.json").write_text(signals_to_json(report))
    n_buys = sum(1 for r in report.recommendations if r.action == "BUY")
    n_sells = sum(1 for r in report.recommendations if r.action == "SELL")
    finish("error" if ingest_error else "ok", ingest_error, n_buys, n_sells,
           report.as_of.isoformat())

    if n_buys + n_sells > 0:
        notify.send_notification(
            "pkmn daily", f"{strategy}: {n_buys} buys, {n_sells} sells — see dashboard"
        )
    if ingest_error:
        notify.send_notification("pkmn daily: ingest problem", ingest_error)
        typer.echo(ingest_error, err=True)
        raise typer.Exit(1)
    typer.echo(f"daily run written to {out_dir}")
```

Note `finish("error", ..., 0, 0, None)` runs before notify in the failure path so the dashboard always sees the run. `ingest_range` is already imported at cli.py module top.

- [ ] **Step 4: Run tests, all four gates.**

- [ ] **Step 5: Commit**

```bash
git add src/pkmn_quant/live/notify.py src/pkmn_quant/cli.py tests/test_cli_daily.py
git commit -m "feat: pkmn daily — scheduled loop with notifications"
```

---

### Task 6: launchd template + docs

**Files:**
- Create: `scripts/com.pkmn-quant.daily.plist`
- Modify: `README.md` (one short subsection), `CLAUDE.md` (commands block)

No unit tests (a plist is config); verification is the manual smoke step.

- [ ] **Step 1: Create** `scripts/com.pkmn-quant.daily.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.pkmn-quant.daily</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/zsh</string>
        <string>-lc</string>
        <string>cd REPO_PATH &amp;&amp; uv run pkmn daily >> data/results/daily.log 2>&amp;1</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>9</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
</dict>
</plist>
```

- [ ] **Step 2: Document.** In `README.md`, add under Quickstart:

```markdown
### Scheduling the daily loop (macOS)

    sed "s|REPO_PATH|$(pwd)|" scripts/com.pkmn-quant.daily.plist \
        > ~/Library/LaunchAgents/com.pkmn-quant.daily.plist
    launchctl load ~/Library/LaunchAgents/com.pkmn-quant.daily.plist

Runs `pkmn daily` at 09:00: ingests new prices, runs signals against your
ledger (`pkmn portfolio ...`), and sends a macOS notification only when
there is something to act on.
```

In `CLAUDE.md`, add to the Commands block:

```bash
uv run pkmn portfolio show                               # real positions + P&L
uv run pkmn daily --skip-ingest                          # the loop, offline
```

- [ ] **Step 3: Manual smoke test** — from the repo root:

```bash
sed "s|REPO_PATH|$(pwd)|" scripts/com.pkmn-quant.daily.plist > /tmp/pkmn-daily-test.plist
plutil -lint /tmp/pkmn-daily-test.plist   # must print OK
uv run pkmn daily --skip-ingest           # real data; expect artifacts + exit 0
```

(The second command needs a real ledger; if `data/portfolio/ledger.jsonl` does not exist yet, `pkmn daily` runs with an empty portfolio — cash 0, no entries affordable, still a valid "ok" run.) STOP and report if `plutil` complains or `daily` errors.

- [ ] **Step 4: Gates, commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
git add scripts/com.pkmn-quant.daily.plist README.md CLAUDE.md
git commit -m "feat: launchd template + scheduling docs"
```

---

### Task 7: Dashboard — Portfolio tab + alerts strip

**Files:**
- Modify: `app/dashboard.py`

No unit tests (demo tool, same as Plan 4 Task 6); gate is ruff + the AppTest smoke below.

- [ ] **Step 1: Implement.** In `app/dashboard.py`:

Add imports at the top (the installed package is importable from the dashboard):

```python
import pkmn_quant.live.ledger as ledger_mod
from pkmn_quant.config import Paths
from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.data import MarketData
from pkmn_quant.live.ledger import LedgerError, ledger_path, load_portfolio, make_snapshot
```

Add a fifth tab — change the tabs line to:

```python
tab_wf, tab_signals, tab_prices, tab_trades, tab_portfolio = st.tabs(
    ["Walk-forward", "Signals", "Prices", "Trades", "Portfolio"]
)
```

Append the tab body at the end of the file:

```python
with tab_portfolio:
    # Alerts strip: recent daily runs, newest first.
    daily_dirs = sorted(RESULTS.glob("daily-*/daily.json"), reverse=True)
    if daily_dirs:
        st.subheader("Daily runs")
        for meta_path in daily_dirs[:14]:
            meta = json.loads(meta_path.read_text())
            actionable = (meta.get("n_buys", 0) + meta.get("n_sells", 0)) > 0
            if meta.get("status") != "ok":
                label = f"🔴 {meta['date']} — FAILED: {meta.get('error')}"
            elif actionable:
                label = (
                    f"🟡 {meta['date']} — {meta['n_buys']} buys,"
                    f" {meta['n_sells']} sells ({meta['strategy']})"
                )
            else:
                label = f"⚪ {meta['date']} — nothing to do"
            with st.expander(label, expanded=False):
                md = meta_path.parent / "signals.md"
                if md.exists():
                    st.markdown(md.read_text())
                else:
                    st.write(meta)

    lp = ledger_path(ROOT)
    if not lp.exists():
        st.info("No ledger yet. Record trades with `uv run pkmn portfolio buy ...`.")
    else:
        warehouse = Warehouse(Paths(root=ROOT))
        products = load_products()
        try:
            pf = load_portfolio(lp, products)
            days = warehouse.stored_days()
            latest = days[-1]
            market = MarketData.from_warehouse(warehouse, latest, latest, warmup_days=365)
            names = {
                int(r["product_id"]): str(r["name"])
                for r in products.select("product_id", "name").iter_rows(named=True)
            }
            snap = make_snapshot(pf, market.marks_on(latest), names)
        except (LedgerError, IndexError) as exc:
            st.error(f"cannot value portfolio: {exc}")
        else:
            c1, c2, c3 = st.columns(3)
            c1.metric("Equity", f"${snap.equity:,.2f}")
            c2.metric("Cash", f"${snap.cash:,.2f}")
            c3.metric("Realized P&L", f"${snap.realized_pnl:+,.2f}")
            if snap.positions:
                st.dataframe(
                    pl.DataFrame(
                        [
                            {
                                "product": p.name, "sub_type": p.sub_type,
                                "qty": p.quantity, "avg cost": p.avg_cost,
                                "mark": p.mark, "unrealized": p.unrealized_pnl,
                            }
                            for p in snap.positions
                        ]
                    ).to_pandas(),
                    hide_index=True,
                )

            # Equity over time: replay the ledger day by day against
            # forward-filled marks for the assets ever held. Demo-grade.
            lines = lp.read_text().splitlines()
            events = ledger_mod._parse_lines(lines)
            if events:
                held_ids = {e.asset.product_id for e in events if e.asset is not None}
                prices = load_prices()
                hist = (
                    prices.filter(pl.col("product_id").is_in(sorted(held_ids)))
                    .sort("date")
                    if held_ids
                    else prices.head(0)
                )
                first = min(e.day for e in events)
                all_days = sorted(d for d in prices["date"].unique().to_list() if d >= first)
                series = []
                for d in all_days:
                    pf_d = ledger_mod._replay(
                        [e for e in events if e.day <= d], products
                    )
                    value = 0.0
                    ok = True
                    for asset, pos in pf_d.positions.items():
                        m = (
                            hist.filter(
                                (pl.col("product_id") == asset.product_id)
                                & (pl.col("sub_type") == asset.sub_type)
                                & (pl.col("date") <= d)
                            )["market"]
                        )
                        if m.len() == 0:
                            ok = False
                            break
                        value += pos.quantity * float(m[-1])
                    if ok:
                        series.append({"date": d, "equity": pf_d.cash + value})
                if series:
                    st.line_chart(
                        pl.DataFrame(series).to_pandas().set_index("date")
                    )
```

(Using the module's `_parse_lines`/`_replay` privates from the demo dashboard is acceptable — it is not part of the package's public surface and is explicitly not a product. If the per-day replay is slow for a big ledger, wrap the series computation in a small `@st.cache_data` function keyed on the ledger text.)

- [ ] **Step 2: Smoke test** with AppTest against a seeded tmp portfolio — run from the repo root:

```bash
uv run --group dashboard python - <<'EOF'
from streamlit.testing.v1 import AppTest

at = AppTest.from_file("app/dashboard.py", default_timeout=240)
at.run()
print("exception:", at.exception)
assert not len(at.exception), "dashboard raised"
print("tabs:", [t.label for t in at.tabs])
EOF
```

Expected: no exception; five tabs. If a real ledger exists in `data/portfolio/`, also eyeball the Portfolio tab in a browser (`uv run --group dashboard streamlit run app/dashboard.py`). STOP and report if the tab errors.

- [ ] **Step 3: Gates, commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
git add app/dashboard.py
git commit -m "feat: dashboard portfolio tab + daily alerts strip"
```

---

### Task 8: Paper-trading mode (stretch — skip if Tasks 1-7 ran over)

**Files:**
- Modify: `src/pkmn_quant/cli.py` (`--paper` on portfolio subcommands, signals, daily)
- Modify: `src/pkmn_quant/live/signals.py` (`paper` label on `SignalReport`)
- Modify: `src/pkmn_quant/live/report.py` (PAPER in the title)
- Test: `tests/test_cli_paper.py`

- [ ] **Step 1: Write the failing tests** — `tests/test_cli_paper.py`:

```python
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import pkmn_quant.live.notify as notify
from pkmn_quant.cli import app
from tests.test_cli_daily import run_walkforward, seed


def test_paper_daily_auto_records_fills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(notify, "send_notification", lambda t, b: None)
    seed(tmp_path)
    runner = CliRunner()
    run_walkforward(runner, tmp_path)
    r = runner.invoke(
        app, ["portfolio", "deposit", "--amount", "1000", "--date", "2025-01-02",
              "--paper", "--root", str(tmp_path)]
    )
    assert r.exit_code == 0, r.output

    result = runner.invoke(app, ["daily", "--skip-ingest", "--paper", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output

    paper = tmp_path / "data" / "portfolio" / "paper.jsonl"
    lines = paper.read_text().strip().splitlines()
    assert len(lines) >= 2  # deposit + at least one auto-recorded buy
    assert json.loads(lines[1])["kind"] == "buy"
    assert not (tmp_path / "data" / "portfolio" / "ledger.jsonl").exists()  # real untouched

    # Paper label on every surface
    daily_dir = next((tmp_path / "data" / "results").glob("daily-*"))
    assert "PAPER" in (daily_dir / "signals.md").read_text()
    assert json.loads((daily_dir / "daily.json").read_text())["paper"] is True


def test_paper_show_reads_paper_ledger(tmp_path: Path) -> None:
    seed(tmp_path)
    runner = CliRunner()
    r = runner.invoke(
        app, ["portfolio", "deposit", "--amount", "777", "--date", "2025-01-02",
              "--paper", "--root", str(tmp_path)]
    )
    assert r.exit_code == 0, r.output
    show = runner.invoke(app, ["portfolio", "show", "--paper", "--root", str(tmp_path)])
    assert show.exit_code == 0, show.output
    assert "777.00" in show.output
```

- [ ] **Step 2: verify failure**, then **Step 3: Implement.**

1. `SignalReport` gains `paper: bool = False`; `render_signals_markdown` title becomes:

```python
        f"# Signals{' (PAPER)' if report.paper else ''}: {report.strategy} — {report.as_of}",
```

2. Every `portfolio` subcommand and `signals`/`daily` gain
   `paper: bool = typer.Option(False, "--paper", help="Use the paper ledger.")`;
   thread it into `ledger_path(root, paper=paper)`. In `_portfolio_deps`, add the
   `paper: bool` parameter. In `signals`, `--paper` implies portfolio mode
   (`portfolio_flag = portfolio_flag or paper`). Pass `paper=paper` when building
   the `SignalReport` — simplest: `generate_signals` gains `paper: bool = False`
   passed through to the report.

3. In `daily`, after writing artifacts in paper mode, auto-record fills with the
   engine cost model (mirrors `Fill` semantics: `price` per unit, `fees` total):

```python
    if paper and report.recommendations:
        from pkmn_quant.engine.costs import CostModel
        from pkmn_quant.live.ledger import append_event

        cost = CostModel()
        for r in report.recommendations:
            fees = (
                cost.shipping_per_line
                if r.action == "BUY"
                else r.quantity * r.market_price * cost.fee_rate + cost.shipping_per_line
            )
            append_event(
                ledger_path(root, paper=True),
                {"date": report.as_of.isoformat(), "kind": r.action.lower(),
                 "product_id": r.product_id, "sub_type": r.sub_type, "qty": r.quantity,
                 "price": r.market_price, "fees": round(fees, 2)},
                warehouse.load_products(),
            )
```

   and add `"paper": paper` to the `daily.json` payload (update `finish`).
   Known, documented optimism vs the backtester: paper fills are same-day at
   mark, not T+1 — this is in the spec and must stay in the module docstring.

- [ ] **Step 4: Run tests, all four gates.**

- [ ] **Step 5: Commit**

```bash
git add src/pkmn_quant/cli.py src/pkmn_quant/live/signals.py src/pkmn_quant/live/report.py tests/test_cli_paper.py
git commit -m "feat: paper-trading mode (--paper)"
```

---

### Task 9: Real-data verification + status updates (manual)

- [ ] **Step 1:** Real-data smoke from the repo root (walk-forward artifacts exist from Plan 4 Task 8):

```bash
uv run pkmn portfolio deposit --amount 1000 --paper
uv run pkmn daily --skip-ingest --paper       # expect PAPER buys auto-recorded
uv run pkmn portfolio show --paper            # positions match the daily report
uv run pkmn daily --skip-ingest               # real ledger (likely empty): "ok", silent
```

Sanity-check: paper fills' prices equal the signals marks; `show --paper` equity ≈ deposit minus fees (paper fills at mark mean unrealized ≈ -fees on day one). Delete the demo paper ledger afterwards if you don't want it: `rm data/portfolio/paper.jsonl` (it is gitignored either way).

- [ ] **Step 2:** Install the launchd job for real (optional, user's call — needs their approval):

```bash
sed "s|REPO_PATH|$(pwd)|" scripts/com.pkmn-quant.daily.plist \
    > ~/Library/LaunchAgents/com.pkmn-quant.daily.plist
launchctl load ~/Library/LaunchAgents/com.pkmn-quant.daily.plist
```

- [ ] **Step 3:** Update `CLAUDE.md` status (Plan 5 merged, new test count, `src/pkmn_quant/live/ledger.py` + `notify.py` in Layout) and `README.md` (add `pkmn portfolio`/`pkmn daily` to Quickstart; move "scheduled ingestion + signals" and "position tracking" out of Future work). Commit:

```bash
git add CLAUDE.md README.md
git commit -m "docs: update status for Plan 5 completion"
```

**Done criteria (Plan 5):** all gates green; a hand-entered ledger produces SELL recommendations when marks cross the strategy's take-profit; `pkmn daily --skip-ingest` writes artifacts and notifies only when actionable; dashboard Portfolio tab shows positions/P&L and the alerts strip; paper mode auto-records cost-modeled fills into a separate ledger labeled PAPER; goldens byte-identical; docs current.
