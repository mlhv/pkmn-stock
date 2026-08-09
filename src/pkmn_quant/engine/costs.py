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
    # Walk-the-spread market impact (spec 2026-07-13). OFF at the engine
    # level so existing goldens/backtests are bit-identical; the CLI turns
    # it on by default.
    impact_enabled: bool = False

    def buy_price(self, market: float) -> float:
        """Cash outlay for a SINGLE unit (market + one shipping charge).

        For multi-unit totals use total_buy_cost - shipping is charged once
        per order line, not per unit.
        """
        return market + self.shipping_per_line

    def sell_proceeds(self, market: float) -> float:
        """Cash received for a SINGLE unit (net of fees and one shipping charge).

        For multi-unit totals use total_sell_proceeds - shipping is charged
        once per order line, not per unit. Can be negative for penny cards.
        """
        return market * (1 - self.fee_rate) - self.shipping_per_line

    def total_buy_cost(self, market: float, qty: int) -> float:
        """Total cash outlay for one buy order line of qty units."""
        return qty * market + self.shipping_per_line

    def total_sell_proceeds(self, market: float, qty: int) -> float:
        """Total cash received for one sell order line of qty units."""
        return qty * market * (1 - self.fee_rate) - self.shipping_per_line

    def max_daily_qty(self, price: float) -> int:
        """Units of one asset tradeable per day at `price`.

        Pass the price actually transacted at, not the print: on the buy side
        that is `buy_base`, since the tiers are a claim about how much depth a
        product of that price has. See buy_base.
        """
        # Strict <: a price exactly at a threshold falls to the NEXT tier
        # ($5.00 is mid-tier, not cheap).
        for threshold, qty in self.liquidity_tiers:
            if price < threshold:
                return qty
        return self.fallback_max_qty

    def buy_base(self, market: float, low: float | None) -> float:
        """The price a buy actually executes at.

        `market` is sales-derived and can sit below `low`, the cheapest
        current listing; a buy cannot lift an offer that does not exist, so
        the fill starts here and the shortfall is charged as impact (see
        buy_impact). Gated on `impact_enabled` because that is where the
        premium is booked: with the model off the floor is not a cost and
        must not size, cap, or constrain anything, keeping flat-cost runs
        bit-identical to pre-floor.

        This is the single definition of the executable buy price. The
        executor sizes off it, `buy_impact` walks from it, it selects the
        liquidity tier for the buy side, and `live/paper.py` mirrors it —
        four call sites that must not drift apart.
        """
        if not self.impact_enabled or low is None:
            return market
        return max(market, low)

    def buy_impact(
        self, market: float, mid: float | None, low: float | None, qty: int, used: int = 0
    ) -> float:
        """Total $ impact for buying qty units after `used` already filled today.

        tcgcsv's `market` is derived from recent completed SALES while `low`
        is the cheapest current LISTING, so `market < low` is ordinary rather
        than corrupt — it means recent sales cleared below today's cheapest
        offer. A buy cannot execute below that offer, so the fill starts at
        max(market, low) and the shortfall is charged as impact; the walk to
        `mid` then starts from there.

        Marginal price then ramps linearly from that base to mid (median
        listing) at the daily cap Q; units used+1..used+qty cost
        spread * qty * (2*used + qty) / (2Q) extra in total. Q is the tier of
        the price actually paid — the floored base, not the print. The tiers
        are a claim about how much depth a product of a given price has, and
        on an inverted row the print names a cheaper product than the one
        being bought: on the research window 346,378 rows (15.8% of the 2.19M
        inverted ones) sit in a different tier under `low` than under
        `market`, and every one of them was being granted a cheaper product's
        depth. That walk term
        alone is zero when mid is missing or the quote is crossed — but the
        floor premium is still charged in those cases, because a missing mid
        says nothing about whether the cheapest listing was reachable. The
        whole result is zero when impact is disabled. Never negative, never
        invented from missing data: a missing `low` means no floor, not a
        guessed one.

        Sells are deliberately NOT symmetric: tcgcsv publishes no bid series
        (low/mid/high are all ask-side), so there is nothing to floor a sell
        against without inventing a price. See sell_impact.
        """
        if not self.impact_enabled or qty <= 0:
            return 0.0
        base = self.buy_base(market, low)
        premium = (base - market) * qty
        return premium + self._impact(base, mid, base, qty, used)

    def sell_impact(self, market: float, low: float | None, qty: int, used: int = 0) -> float:
        """Total $ impact for selling: undercut from market toward low.

        The tier comes off `market` here and that is deliberate: a sell has no
        floor (tcgcsv publishes no bid series), so the price transacted at IS
        the print.
        """
        return self._impact(market, market, low, qty, used)

    def _impact(
        self, tier_price: float, upper: float | None, lower: float | None, qty: int, used: int
    ) -> float:
        """Walk cost from `upper` to `lower`, at the daily cap for `tier_price`.

        `tier_price` is the price transacted at — the floored base on a buy,
        the print on a sell — never automatically the print.
        """
        if not self.impact_enabled or qty <= 0 or upper is None or lower is None:
            return 0.0
        spread = upper - lower
        if spread <= 0:
            return 0.0
        q_cap = self.max_daily_qty(tier_price)
        return spread * qty * (2 * used + qty) / (2 * q_cap)

    def as_dict(self) -> dict[str, Any]:
        return {
            "fee_rate": self.fee_rate,
            "shipping_per_line": self.shipping_per_line,
            "liquidity_tiers": list(self.liquidity_tiers),
            "fallback_max_qty": self.fallback_max_qty,
            "impact_enabled": self.impact_enabled,
        }
