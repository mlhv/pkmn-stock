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

    def __post_init__(self) -> None:
        if self.price <= 0:
            raise ValueError(f"Fill.price must be positive, got {self.price}")
        if self.fees < 0:
            raise ValueError(f"Fill.fees must be non-negative, got {self.fees}")


@dataclass
class Position:
    quantity: int
    avg_cost: float


@dataclass
class Portfolio:
    cash: float
    # NOTE: mutable and aliased — callers exposing this to external code
    # (e.g. the strategy Context) must deep-copy it first; mutations here
    # propagate straight into the accounting.
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
        """Cash plus positions valued at the given per-asset market prices.

        Raises KeyError if a held asset has no mark - deliberate: the engine
        must always supply carry-forward marks; silence would corrupt every
        downstream number.
        """
        value = sum(pos.quantity * marks[asset] for asset, pos in self.positions.items())
        return self.cash + value
