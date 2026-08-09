"""Order execution with card-market realism.

Fills happen at the day's actually-printed prices (no carry-forward), with
spread/fees from the CostModel, clipped by liquidity, cash, and held quantity.
Long-only: a sell can never exceed the position; shorts cannot exist.

Design note: Fill.price is always the observable market print; ALL costs are
explicit in fees (buy side: shipping; sell side: marketplace fee + shipping)
and impact (walk-the-spread cost of demanding size). This keeps the ledger
auditable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from pkmn_quant.engine.costs import CostModel
from pkmn_quant.engine.portfolio import Asset, Fill, Portfolio
from pkmn_quant.engine.quotes import Quote


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
        quotes: dict[Asset, Quote] | None = None,
    ) -> list[Fill]:
        """Fill orders against the day's prices, applying them to the portfolio.

        The liquidity cap is per asset per DAY, shared across all orders and
        both sides: splitting one big order into several cannot buy more depth.
        """
        fills: list[Fill] = []
        filled_today: dict[Asset, int] = {}
        quotes_map = quotes or {}
        for order in orders:
            market = prices.get(order.asset)
            # market <= 0 should be impossible (quality gates quarantine it),
            # but a zero price here would divide by zero — skip defensively.
            if market is None or market <= 0 or order.quantity == 0:
                continue  # asset didn't trade today; order expires unfilled
            used = filled_today.get(order.asset, 0)
            quote = quotes_map.get(order.asset)
            # The depth cap is a claim about the product being transacted, so
            # it is tiered on the price actually paid: the floored base on a
            # buy (CostModel.buy_base), the print on a sell, which has no
            # floor. Off `market`, an inverted row buys at $60 with the depth
            # of the $4 product the print names.
            tier_price = (
                self.cost_model.buy_base(market, quote.low if quote is not None else None)
                if order.quantity > 0
                else market
            )
            cap_left = self.cost_model.max_daily_qty(tier_price) - used
            if cap_left <= 0:
                continue
            fill = (
                self._fill_buy(order, market, portfolio, day, cap_left, used, quote)
                if order.quantity > 0
                else self._fill_sell(order, market, portfolio, day, cap_left, used, quote)
            )
            if fill is not None:
                portfolio.apply(fill)
                fills.append(fill)
                filled_today[order.asset] = used + abs(fill.quantity)
        return fills

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
        mid = quote.mid if quote is not None else None
        low = quote.low if quote is not None else None
        # A buy cannot execute below the cheapest current listing; `market` is
        # sales-derived and can sit under it (see CostModel.buy_base, which
        # also explains the impact_enabled gate and is the single definition
        # of this price — `cap_left` above was tiered on the same number).
        base = self.cost_model.buy_base(market, low)
        qty = min(order.quantity, cap_left)
        # afford: qty * base + shipping_per_line + walk-impact(qty) <= cash.
        # With impact ON, qty*base <= qty*market + impact(qty), so `base` is a
        # valid upper bound: it only changes how many times the decrement loop
        # below runs, never where it stops — and off `market` that loop can run
        # hundreds of iterations when low >> market. With impact OFF the true
        # constraint is qty*market + shipping <= cash and a `base` clip would
        # bind BELOW it, cutting real quantities (10x on a 10x floor); hence
        # the gate above.
        affordable = math.floor((portfolio.cash - self.cost_model.shipping_per_line) / base)
        qty = min(qty, max(affordable, 0))
        impact = self.cost_model.buy_impact(market, mid, low, qty, used)
        cost = qty * market + self.cost_model.shipping_per_line + impact
        while qty > 0 and cost > portfolio.cash:
            qty -= 1
            impact = self.cost_model.buy_impact(market, mid, low, qty, used)
            cost = qty * market + self.cost_model.shipping_per_line + impact
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
        return Fill(
            day=day,
            asset=order.asset,
            quantity=-qty,
            price=market,
            fees=fees,
            impact=impact,
        )
