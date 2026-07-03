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
    """A strategy's read-only window onto the world.

    The containers are the strategy's own copies (the backtest loop copies
    positions/marks before construction) - mutating them affects nothing.
    """

    today: date
    history: pl.DataFrame  # price rows, date <= today only
    products: pl.DataFrame  # product catalog (id, kind, rarity, ...)
    positions: dict[Asset, Position]
    cash: float
    marks: dict[Asset, float]  # today's mark prices (carry-forward)


class Strategy(ABC):
    """Implement on_bar; emit orders. Orders fill at the NEXT day's prices."""

    name: str = "strategy"

    @abstractmethod
    def on_bar(self, ctx: Context) -> list[Order]: ...

    def reset(self) -> None:  # noqa: B027
        """Clear any per-run state. Called by Backtest.run() before the loop.

        Stateful strategies (entry flags, rolling caches) must override this
        so one instance can be reused across runs (e.g. walk-forward windows).
        """
