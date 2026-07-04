"""Buy sealed product after the post-release crash; sell at a target multiple."""

from __future__ import annotations

import math
from datetime import timedelta

import polars as pl

from pkmn_quant.engine.execution import Order
from pkmn_quant.engine.portfolio import Asset
from pkmn_quant.engine.strategy import Context, Strategy


class SealedAccumulation(Strategy):
    """Entry: sealed, aged [min_age_days, max_age_days], down >= min_drawdown
    from its peak-to-date. Exit: mark >= avg_cost * take_profit. Long-only,
    sells emitted before buys.
    """

    def __init__(
        self,
        min_age_days: int = 60,
        max_age_days: int = 365,
        min_drawdown: float = 0.25,
        take_profit: float = 1.5,
        max_positions: int = 10,
        budget_frac: float = 0.10,
    ) -> None:
        self.min_age_days = min_age_days
        self.max_age_days = max_age_days
        self.min_drawdown = min_drawdown
        self.take_profit = take_profit
        self.max_positions = max_positions
        self.budget_frac = budget_frac
        self.name = "sealed-accumulation"

    def on_bar(self, ctx: Context) -> list[Order]:
        orders: list[Order] = []

        # Exits first: proceeds are available to later buys in the same batch.
        for asset, pos in sorted(ctx.positions.items(), key=lambda kv: kv[0].product_id):
            mark = ctx.marks.get(asset)
            if mark is not None and mark >= pos.avg_cost * self.take_profit:
                orders.append(Order(asset=asset, quantity=-pos.quantity))

        open_slots = self.max_positions - (len(ctx.positions) - len(orders))
        if open_slots <= 0:
            return orders

        aged = ctx.products.filter(
            (pl.col("kind") == "sealed")
            & (pl.col("released_on") <= ctx.today - timedelta(days=self.min_age_days))
            & (pl.col("released_on") >= ctx.today - timedelta(days=self.max_age_days))
        )
        aged_ids = set(aged["product_id"].to_list())
        if not aged_ids:
            return orders

        peaks = (
            ctx.history.filter(pl.col("product_id").is_in(sorted(aged_ids)))
            .group_by(["product_id", "sub_type"])
            .agg(pl.col("market").max().alias("peak"))
        )
        peak_by_asset = {
            Asset(product_id=int(r["product_id"]), sub_type=str(r["sub_type"])): float(r["peak"])
            for r in peaks.iter_rows(named=True)
        }

        candidates: list[tuple[float, Asset, float]] = []  # (drawdown, asset, mark)
        for asset, peak in peak_by_asset.items():
            if asset in ctx.positions or peak <= 0:
                continue
            mark = ctx.marks.get(asset)
            if mark is None:
                continue
            drawdown = 1.0 - mark / peak
            if drawdown >= self.min_drawdown:
                candidates.append((drawdown, asset, mark))

        # Deepest discounts first; deterministic tie-break by product_id.
        candidates.sort(key=lambda c: (-c[0], c[1].product_id))
        budget = ctx.cash * self.budget_frac
        for _, asset, mark in candidates[:open_slots]:
            qty = math.floor(budget / mark)
            if qty > 0:
                orders.append(Order(asset=asset, quantity=qty))
        return orders
