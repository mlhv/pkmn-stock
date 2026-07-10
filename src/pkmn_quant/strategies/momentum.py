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
    emitted first). Stateless: rebalance timing is derived from the ledger
    via Position.opened_on rather than a mutable clock.

    Rebalance clock semantics:
    - When the portfolio is flat (no positions), the strategy evaluates every
      bar until a buy fills — it no longer waits out rebalance_days with no
      holdings.
    - When positions are held, a rebalance is due when
      (today - newest opened_on).days >= rebalance_days, where the newest
      opened_on approximates the date of the last rebalance buy.
    - A rebalance whose buys don't fill (e.g. price gaps above budget) retries
      the following bar, because the portfolio remains flat or the newest
      opened_on is unchanged.
    - Corollary: a due rebalance that needs no new buys (every held name is
      still in the target) emits nothing and stays due, so the ranking is
      recomputed every bar until a buy fills. Idempotent, just extra compute.

    Names that stay in the target are never trimmed (long-only, entry-only
    weighting): winners drift above equal weight over time.

    Positions constructed outside the engine (e.g. hand-built test portfolios)
    may have opened_on=None; on_bar raises ValueError loudly in that case.
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

    def _rebalance_due(self, ctx: Context) -> bool:
        if not ctx.positions:
            return True
        newest: date | None = None
        for asset, pos in ctx.positions.items():
            if pos.opened_on is None:
                raise ValueError(
                    f"{self.name}: position {asset} has no opened_on; "
                    "engine fills and ledger replay always set it"
                )
            newest = pos.opened_on if newest is None else max(newest, pos.opened_on)
        assert newest is not None
        return (ctx.today - newest).days >= self.rebalance_days

    def on_bar(self, ctx: Context) -> list[Order]:
        if not self._rebalance_due(ctx):
            return []

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

        # Held assets always have a carry-forward mark (a fill requires a
        # print); fail loudly rather than mis-valuing equity at avg_cost.
        equity = ctx.cash + sum(pos.quantity * ctx.marks[a] for a, pos in ctx.positions.items())
        per_name = equity / len(target)
        # Buys sorted by product_id for determinism; if sells under-fill
        # (liquidity cap), lower product_ids get first claim on cash.
        for asset, mark in sorted(target.items(), key=lambda kv: kv[0].product_id):
            held = ctx.positions.get(asset)
            held_value = held.quantity * mark if held else 0.0
            qty = math.floor((per_name - held_value) / mark)
            if qty > 0:
                orders.append(Order(asset=asset, quantity=qty))
        return orders
