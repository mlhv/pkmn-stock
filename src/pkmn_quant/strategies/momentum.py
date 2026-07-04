"""Cross-sectional momentum: hold the top-N trailing performers among singles."""

from __future__ import annotations

import math
from datetime import date, timedelta

import polars as pl

from pkmn_quant.engine.execution import Order
from pkmn_quant.engine.portfolio import Asset
from pkmn_quant.engine.strategy import Context, Strategy


class CrossSectionalMomentum(Strategy):
    """Every rebalance_days: rank singles by trailing lookback return, target
    the top_n equally weighted, sell everything that dropped out (sells
    emitted first). Stateful (_last_rebalance); reset() clears it.
    """

    def __init__(
        self,
        lookback_days: int = 60,
        top_n: int = 10,
        rebalance_days: int = 30,
        min_price: float = 3.0,
    ) -> None:
        self.lookback_days = lookback_days
        self.top_n = top_n
        self.rebalance_days = rebalance_days
        self.min_price = min_price
        self.name = "xs-momentum"
        self._last_rebalance: date | None = None

    def reset(self) -> None:
        self._last_rebalance = None

    def on_bar(self, ctx: Context) -> list[Order]:
        if (
            self._last_rebalance is not None
            and (ctx.today - self._last_rebalance).days < self.rebalance_days
        ):
            return []
        self._last_rebalance = ctx.today

        single_ids = set(ctx.products.filter(pl.col("kind") == "single")["product_id"].to_list())
        window_start = ctx.today - timedelta(days=self.lookback_days)
        past = (
            ctx.history.filter(
                (pl.col("date") <= window_start) & pl.col("product_id").is_in(single_ids)
            )
            .group_by(["product_id", "sub_type"])
            .agg(pl.col("market").sort_by(pl.col("date")).last().alias("past"))
        )
        momentum: list[tuple[float, Asset, float]] = []
        for r in past.iter_rows(named=True):
            asset = Asset(product_id=int(r["product_id"]), sub_type=str(r["sub_type"]))
            past_price = float(r["past"])
            mark = ctx.marks.get(asset)
            if mark is None or mark < self.min_price or past_price <= 0:
                continue
            momentum.append((mark / past_price - 1.0, asset, mark))

        momentum.sort(key=lambda m: (-m[0], m[1].product_id))
        target = {asset: mark for _, asset, mark in momentum[: self.top_n]}

        orders: list[Order] = []
        # Sells first: the executor fills this list sequentially on T+1, so
        # sell proceeds are in portfolio.cash before any buy fill is attempted.
        for asset, pos in sorted(ctx.positions.items(), key=lambda kv: kv[0].product_id):
            if asset not in target:
                orders.append(Order(asset=asset, quantity=-pos.quantity))

        if not target:
            return orders

        equity = ctx.cash + sum(
            pos.quantity * ctx.marks.get(a, pos.avg_cost) for a, pos in ctx.positions.items()
        )
        per_name = equity / len(target)
        for asset, mark in sorted(target.items(), key=lambda kv: kv[0].product_id):
            held = ctx.positions.get(asset)
            held_value = held.quantity * mark if held else 0.0
            qty = math.floor((per_name - held_value) / mark)
            if qty > 0:
                orders.append(Order(asset=asset, quantity=qty))
        return orders
