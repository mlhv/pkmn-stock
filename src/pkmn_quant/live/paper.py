"""Paper-trading fill planner: pure arithmetic, no I/O.

Turns a signal report's recommendations into the batch of ledger event
dicts that `append_events` can write. Mirrors the backtest executor's
clipping: sells capped by the liquidity tier; buys capped by liquidity
AND by what running cash affords after shipping AND walk-the-spread
impact are reserved.

Recommendations are walked in order (strategies emit sells before buys),
so sell proceeds top up cash before any buy is sized. Recommendations
that clip to zero quantity produce no event — counting the returned
batch is therefore the honest "what was actually recorded" number.

Fills are dated *day* (the run date), not the report's as_of: the ledger
is chronological in event time, and as_of marks can predate deposits,
which would sort fills before the deposit and fail replay.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date

from pkmn_quant.engine.costs import CostModel
from pkmn_quant.live.signals import Recommendation


def plan_paper_fills(
    recommendations: Sequence[Recommendation],
    cash: float,
    day: date,
    costs: CostModel,
) -> list[dict[str, object]]:
    """Pure planner: turn recommendations into ledger event dicts.

    Walks recommendations in order (sells before buys), sizing each fill
    subject to liquidity caps and cash constraints. Drops any fill that
    clips to zero quantity. Returns the batch of event dicts ready for
    append_events.

    Args:
        recommendations: Ordered list of BUY/SELL signals from strategy.
        cash: Starting cash balance for this planner run.
        day: Run date; all fill events will be dated this day.
        costs: CostModel with fee_rate, shipping_per_line, liquidity tiers.

    Returns:
        List of ledger event dicts (one per non-zero fill), in order.
    """
    cash_remaining = cash
    batch: list[dict[str, object]] = []
    for rec in recommendations:
        mark = rec.market_price
        cap = costs.max_daily_qty(mark)
        if rec.action == "SELL":
            # Clip to liquidity cap; rec.quantity already equals held qty.
            qty = min(rec.quantity, cap)
            if qty <= 0:
                continue
            impact = costs.sell_impact(mark, rec.low, qty)
            fees = round(qty * mark * costs.fee_rate + costs.shipping_per_line, 2)
            cash_remaining += qty * mark * (1 - costs.fee_rate) - costs.shipping_per_line - impact
        else:  # BUY
            # Mirror executor _fill_buy: clip to liquidity cap, then to
            # what cash_remaining can afford after shipping is reserved.
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
        batch.append(
            {
                "date": day.isoformat(),
                "kind": rec.action.lower(),
                "product_id": rec.product_id,
                "sub_type": rec.sub_type,
                "qty": qty,
                "price": mark,
                "fees": fees,
                "impact": round(impact, 2),
            }
        )
    return batch
