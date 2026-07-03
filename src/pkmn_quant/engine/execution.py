"""Order execution with card-market realism.

Fills happen at the day's actually-printed prices (no carry-forward), with
spread/fees from the CostModel, clipped by liquidity, cash, and held quantity.
Long-only: a sell can never exceed the position; shorts cannot exist.

Design note: Fill.price is always the observable market print; ALL costs are
explicit in fees (buy side: shipping; sell side: marketplace fee + shipping).
This keeps the ledger auditable.
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
        """Fill orders against the day's prices, applying them to the portfolio.

        The liquidity cap is per asset per DAY, shared across all orders and
        both sides: splitting one big order into several cannot buy more depth.
        """
        fills: list[Fill] = []
        filled_today: dict[Asset, int] = {}
        for order in orders:
            market = prices.get(order.asset)
            # market <= 0 should be impossible (quality gates quarantine it),
            # but a zero price here would divide by zero — skip defensively.
            if market is None or market <= 0 or order.quantity == 0:
                continue  # asset didn't trade today; order expires unfilled
            used = filled_today.get(order.asset, 0)
            cap_left = self.cost_model.max_daily_qty(market) - used
            if cap_left <= 0:
                continue
            fill = (
                self._fill_buy(order, market, portfolio, day, cap_left)
                if order.quantity > 0
                else self._fill_sell(order, market, portfolio, day, cap_left)
            )
            if fill is not None:
                portfolio.apply(fill)
                fills.append(fill)
                filled_today[order.asset] = used + abs(fill.quantity)
        return fills

    def _fill_buy(
        self, order: Order, market: float, portfolio: Portfolio, day: date, cap_left: int
    ) -> Fill | None:
        qty = min(order.quantity, cap_left)
        # afford: qty * market + shipping_per_line <= cash
        affordable = math.floor((portfolio.cash - self.cost_model.shipping_per_line) / market)
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
        self, order: Order, market: float, portfolio: Portfolio, day: date, cap_left: int
    ) -> Fill | None:
        pos = portfolio.positions.get(order.asset)
        if pos is None:
            return None
        qty = min(-order.quantity, pos.quantity, cap_left)
        if qty <= 0:
            return None
        fees = qty * market * self.cost_model.fee_rate + self.cost_model.shipping_per_line
        return Fill(day=day, asset=order.asset, quantity=-qty, price=market, fees=fees)
