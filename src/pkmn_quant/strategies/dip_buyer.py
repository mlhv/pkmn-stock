"""Long-only mean reversion: buy sharp dips, exit on time or profit target."""

from __future__ import annotations

import math
from datetime import date, timedelta

import polars as pl

from pkmn_quant.engine.execution import Order
from pkmn_quant.engine.portfolio import Asset
from pkmn_quant.engine.strategy import Context, Strategy


class DipBuyer(Strategy):
    """Entry: singles down >= dip_threshold over dip_window_days.
    Exit: held >= hold_days, or mark >= avg_cost * take_profit.
    Stateful (_entries: asset -> entry-intent date); reset() clears it.

    Known imprecision (acceptable for research): _entries records order-EMISSION
    date, not fill date. An emitted buy that never fills leaves a stale entry,
    blocking re-entry for that asset until reset(). The hold_days clock
    therefore starts at order emission, making actual holding one day shorter
    than hold_days (T+1 fill).

    Partial-fill behaviour: when a sell order is emitted the entry record is
    removed immediately. If the executor clips the fill (liquidity cap), the
    remaining position has no entry record and is treated as overdue — it will
    be re-sold every bar until fully closed.
    """

    def __init__(
        self,
        dip_window_days: int = 7,
        dip_threshold: float = 0.30,
        hold_days: int = 30,
        take_profit: float = 1.25,
        max_positions: int = 10,
        budget_frac: float = 0.10,
        min_price: float = 3.0,
    ) -> None:
        self.dip_window_days = dip_window_days
        self.dip_threshold = dip_threshold
        self.hold_days = hold_days
        self.take_profit = take_profit
        self.max_positions = max_positions
        self.budget_frac = budget_frac
        self.min_price = min_price
        self.name = "dip-buyer"
        self._entries: dict[Asset, date] = {}

    def reset(self) -> None:
        self._entries = {}

    def on_bar(self, ctx: Context) -> list[Order]:
        orders: list[Order] = []

        # Sells first: the executor fills this list sequentially on T+1, so
        # sell proceeds are in portfolio.cash before any buy fill is attempted.
        for asset, pos in sorted(ctx.positions.items(), key=lambda kv: kv[0].product_id):
            mark = ctx.marks.get(asset)
            entered = self._entries.get(asset)
            too_old = entered is None or (ctx.today - entered).days >= self.hold_days
            hit_target = mark is not None and mark >= pos.avg_cost * self.take_profit
            if too_old or hit_target:
                orders.append(Order(asset=asset, quantity=-pos.quantity))
                self._entries.pop(asset, None)

        open_slots = self.max_positions - (len(ctx.positions) - len(orders))
        if open_slots <= 0:
            return orders

        single_ids = set(ctx.products.filter(pl.col("kind") == "single")["product_id"].to_list())
        window_start = ctx.today - timedelta(days=self.dip_window_days)
        past = (
            ctx.history.filter(
                (pl.col("date") <= window_start) & pl.col("product_id").is_in(single_ids)
            )
            .group_by(["product_id", "sub_type"])
            .agg(pl.col("market").sort_by(pl.col("date")).last().alias("past"))
        )
        past_by_asset = {
            Asset(product_id=int(r["product_id"]), sub_type=str(r["sub_type"])): float(r["past"])
            for r in past.iter_rows(named=True)
        }

        candidates: list[tuple[float, Asset, float]] = []
        for asset, past_price in past_by_asset.items():
            if asset in ctx.positions or asset in self._entries or past_price <= 0:
                continue
            mark = ctx.marks.get(asset)
            if mark is None or mark < self.min_price:
                continue
            ret = mark / past_price - 1.0
            if ret <= -self.dip_threshold:
                candidates.append((ret, asset, mark))

        candidates.sort(key=lambda c: (c[0], c[1].product_id))  # deepest dip first
        budget = ctx.cash * self.budget_frac
        affordable = [
            (asset, mark, qty)
            for _, asset, mark in candidates
            if (qty := math.floor(budget / mark)) > 0
        ]
        for asset, _, qty in affordable[:open_slots]:
            orders.append(Order(asset=asset, quantity=qty))
            self._entries[asset] = ctx.today
        return orders
