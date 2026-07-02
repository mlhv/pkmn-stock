"""The benchmark every strategy must beat: buy the universe, hold it."""

from __future__ import annotations

import math

import polars as pl

from pkmn_quant.engine.execution import Order
from pkmn_quant.engine.strategy import Context, Strategy


class BuyAndHold(Strategy):
    """On the first bar, split cash equally across the `kind` universe. Hold."""

    def __init__(self, kind: str = "sealed") -> None:
        self.kind = kind
        self.name = f"buy-and-hold-{kind}"
        self._entered = False

    def on_bar(self, ctx: Context) -> list[Order]:
        if self._entered:
            return []
        self._entered = True

        wanted_ids = set(ctx.products.filter(pl.col("kind") == self.kind)["product_id"].to_list())
        universe = [
            (asset, price)
            for asset, price in sorted(ctx.marks.items(), key=lambda kv: kv[0].product_id)
            if asset.product_id in wanted_ids
        ]
        if not universe:
            return []

        budget_per_asset = ctx.cash / len(universe)
        orders: list[Order] = []
        for asset, price in universe:
            qty = math.floor(budget_per_asset / price)
            if qty > 0:
                orders.append(Order(asset=asset, quantity=qty))
        return orders
