"""Long-only mean reversion: buy sharp dips, exit on time or profit target."""

from __future__ import annotations

import math
from datetime import timedelta

import polars as pl

from pkmn_quant.engine.execution import Order
from pkmn_quant.engine.portfolio import Asset
from pkmn_quant.engine.strategy import Context, Strategy


class DipBuyer(Strategy):
    """Entry: singles down >= dip_threshold over dip_window_days.
    Exit: held >= hold_days (measured from Position.opened_on), or
    mark >= avg_cost * take_profit.

    Stateless: all exit timing comes from pos.opened_on, which the engine sets
    at the T+1 fill date (not at order-emission time). This means:
    - The hold clock starts at the actual fill, so a position held for exactly
      hold_days days exits on that bar (not one day early as in the old
      emission-based clock).
    - Partial fills keep their original opened_on; there is no stale-entry
      problem, and the remaining quantity is not re-sold every bar.
    - An emitted-but-unfilled buy does NOT block re-entry: the next bar may
      re-emit a buy order while the dip persists and no position yet exists.

    Positions constructed outside the engine (e.g. hand-built test portfolios)
    may have opened_on=None; on_bar raises ValueError loudly in that case.
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

    def on_bar(self, ctx: Context) -> list[Order]:
        orders: list[Order] = []

        # Sells first: the executor fills this list sequentially on T+1, so
        # sell proceeds are in portfolio.cash before any buy fill is attempted.
        for asset, pos in sorted(ctx.positions.items(), key=lambda kv: kv[0].product_id):
            if pos.opened_on is None:
                raise ValueError(
                    f"{self.name}: position {asset} has no opened_on; "
                    "engine fills and ledger replay always set it"
                )
            mark = ctx.marks.get(asset)
            too_old = (ctx.today - pos.opened_on).days >= self.hold_days
            hit_target = mark is not None and mark >= pos.avg_cost * self.take_profit
            if too_old or hit_target:
                orders.append(Order(asset=asset, quantity=-pos.quantity))

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
            if asset in ctx.positions or past_price <= 0:
                continue
            mark = ctx.marks.get(asset)
            if mark is None or mark < self.min_price:
                continue
            ret = mark / past_price - 1.0
            if ret <= -self.dip_threshold:
                candidates.append((ret, asset, mark))

        # Deepest dip first; tie-break by (product_id, sub_type) so the order
        # is fully determined by the key (not by Python's stable-sort
        # fallback to insertion order, which for a product with two
        # sub_types depends on polars group_by's non-deterministic,
        # PYTHONHASHSEED-dependent iteration order — see the native.py port).
        candidates.sort(key=lambda c: (c[0], c[1].product_id, c[1].sub_type))
        budget = ctx.cash * self.budget_frac
        affordable = [
            (asset, mark, qty)
            for _, asset, mark in candidates
            if (qty := math.floor(budget / mark)) > 0
        ]
        for asset, _, qty in affordable[:open_slots]:
            orders.append(Order(asset=asset, quantity=qty))
        return orders
