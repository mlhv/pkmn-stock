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
            (asset, qty) for _, asset, mark in candidates if (qty := math.floor(budget / mark)) > 0
        ]
        for asset, qty in affordable[:open_slots]:
            orders.append(Order(asset=asset, quantity=qty))
        return orders
