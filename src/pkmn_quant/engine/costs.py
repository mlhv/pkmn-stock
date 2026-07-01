"""Execution-cost assumptions for the TCG market.

Every backtest Result serializes its CostModel so reports state their own
assumptions. Defaults model TCGplayer: ~12.75% seller fees and flat shipping.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# (price threshold, units tradeable per asset per day); above the last
# threshold, DEFAULT_MAX_QTY applies. Cheap cards are liquid; a $300 alt-art
# might see one sale a day. Tuple-of-tuples keeps the frozen dataclass truly
# immutable and hashable.
DEFAULT_LIQUIDITY_TIERS: tuple[tuple[float, int], ...] = ((5.0, 20), (50.0, 8), (200.0, 3))
DEFAULT_MAX_QTY = 1


@dataclass(frozen=True)
class CostModel:
    fee_rate: float = 0.1275
    shipping_per_line: float = 1.0
    liquidity_tiers: tuple[tuple[float, int], ...] = DEFAULT_LIQUIDITY_TIERS
    fallback_max_qty: int = DEFAULT_MAX_QTY

    def buy_price(self, market: float) -> float:
        """Per-unit cash outlay when buying at the market price."""
        return market + self.shipping_per_line

    def sell_proceeds(self, market: float) -> float:
        """Per-unit cash received when selling at the market price."""
        return market * (1 - self.fee_rate) - self.shipping_per_line

    def max_daily_qty(self, market: float) -> int:
        # Strict <: a price exactly at a threshold falls to the NEXT tier
        # ($5.00 is mid-tier, not cheap).
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
