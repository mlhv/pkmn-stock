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
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import polars as pl

from pkmn_quant.engine.portfolio import Asset, Fill, Portfolio

KINDS = frozenset({"deposit", "withdraw", "buy", "sell"})
_DEPOSIT_WITHDRAW_KEYS = frozenset({"date", "kind", "amount"})
_TRADE_KEYS = frozenset({"date", "kind", "product_id", "sub_type", "qty", "price", "fees"})


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
        extra = set(obj.keys()) - _DEPOSIT_WITHDRAW_KEYS
        if extra:
            raise fail(f"unexpected key(s) for {kind!r}: {', '.join(sorted(extra))}")
        try:
            amount = float(obj["amount"])
        except (KeyError, TypeError, ValueError) as exc:
            raise fail(f"missing/invalid amount ({exc!r})") from exc
        if not math.isfinite(amount):
            raise fail(f"amount must be finite, got {amount}")
        if amount <= 0:
            raise fail(f"amount must be positive, got {amount}")
        return LedgerEvent(line_no=line_no, day=day, kind=kind, amount=amount)

    extra = set(obj.keys()) - _TRADE_KEYS
    if extra:
        raise fail(f"unexpected key(s) for {kind!r}: {', '.join(sorted(extra))}")
    try:
        asset = Asset(product_id=int(obj["product_id"]), sub_type=str(obj["sub_type"]))
        raw_qty = obj["qty"]
        if isinstance(raw_qty, float) and not raw_qty.is_integer():
            raise fail(f"qty must be a whole number, got {raw_qty}")
        qty = int(raw_qty)
        price = float(obj["price"])
        fees = float(obj.get("fees", 0.0))
    except (KeyError, TypeError, ValueError) as exc:
        raise fail(f"missing/invalid trade field ({exc!r})") from exc
    if qty <= 0:
        raise fail(f"qty must be positive, got {qty}")
    if not math.isfinite(price):
        raise fail(f"price must be finite, got {price}")
    if price <= 0:
        raise fail(f"price must be positive, got {price}")
    if not math.isfinite(fees):
        raise fail(f"fees must be finite, got {fees}")
    if fees < 0:
        raise fail(f"fees must be non-negative, got {fees}")
    return LedgerEvent(
        line_no=line_no, day=day, kind=kind, asset=asset, qty=qty, price=price, fees=fees
    )


def _parse_lines(lines: list[str]) -> list[LedgerEvent]:
    events = [_parse_line(i, raw) for i, raw in enumerate(lines, start=1) if raw.strip()]
    # Stable sort: date order, file order within a date.
    return sorted(events, key=lambda e: e.day)


def replay(events: list[LedgerEvent], products: pl.DataFrame) -> Portfolio:
    """Replay parsed events into a Portfolio.

    Raises LedgerError, naming the offending line, on invalid sequences.
    """
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
        # Clamp float dust (strictly within half-cent) to zero for clean round-trips.
        if -0.005 < pf.cash < 0.0:
            pf.cash = 0.0
        if pf.cash <= -0.005:
            raise fail(f"cash goes negative ({pf.cash:.2f}) — mis-entered ledger?")
    return pf


def load_events(path: Path) -> list[LedgerEvent]:
    """Parse the ledger file into date-sorted events. Missing file = []."""
    if not path.exists():
        return []
    return _parse_lines(path.read_text().splitlines())


def load_portfolio(path: Path, products: pl.DataFrame) -> Portfolio:
    """Replay the ledger into a Portfolio. Missing file = empty portfolio."""
    return replay(load_events(path), products)


def append_event(path: Path, event: dict[str, object], products: pl.DataFrame) -> None:
    """Validate existing + new event together; only then append to the file."""
    existing = path.read_text().splitlines() if path.exists() else []
    candidate = [*existing, json.dumps(event)]
    replay(_parse_lines(candidate), products)  # raises LedgerError if invalid
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(line + "\n" for line in candidate))


def append_events(path: Path, events: list[dict[str, object]], products: pl.DataFrame) -> None:
    """Validate existing + ALL new events in one replay; write once (atomic).

    If any event in *events* is invalid (or the combined sequence violates
    portfolio invariants), raises LedgerError and the file is left completely
    unchanged.  Mirror of append_event but for a batch of events.
    """
    existing = path.read_text().splitlines() if path.exists() else []
    candidate = [*existing, *[json.dumps(e) for e in events]]
    replay(_parse_lines(candidate), products)  # raises LedgerError if invalid
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(line + "\n" for line in candidate))


def make_snapshot(pf: Portfolio, marks: dict[Asset, float], names: dict[int, str]) -> Snapshot:
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
            f"{', '.join(f'{a.product_id}/{a.sub_type}' for a in missing)}"
        )
    value = sum(r.mark * r.quantity for r in rows)
    return Snapshot(
        cash=pf.cash, realized_pnl=pf.realized_pnl, equity=pf.cash + value, positions=rows
    )
