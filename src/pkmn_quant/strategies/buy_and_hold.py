"""The benchmark every strategy must beat: buy the universe, hold it.

Equal-dollar alone stopped being a benchmark once the price universe widened
from 23 tcgcsv groups to 217: `cash / len(universe)` fell below the price of
most sealed products, so the "fully invested" bar sat mostly in cash. Two
rules fix that without changing the thesis. A liquidity screen
(`research/screen.py`) picks the n_assets most tradeable products instead of
spreading across all of them, and a deficit sweep spends the remainder that
integer sizing leaves behind, one unit at a time.

Ported to C++ in cpp/src/pkmn_engine/strategies/buy_and_hold.cpp, and pinned
bit-for-bit by tests/test_native_parity.py — a change here is a change there,
in the same commit.
"""

from __future__ import annotations

import math

import polars as pl

from pkmn_quant.engine.execution import Order
from pkmn_quant.engine.portfolio import Asset
from pkmn_quant.engine.strategy import Context, Strategy
from pkmn_quant.research.screen import rank_assets

_UNRANKED: tuple[int, float] = (0, float("inf"))


class BuyAndHold(Strategy):
    """On the first bar, spend cash across the screened `kind` universe. Hold."""

    def __init__(self, kind: str = "sealed", n_assets: int = 100) -> None:
        self.kind = kind
        self.n_assets = n_assets
        self.name = f"buy-and-hold-{kind}"
        self._entered = False

    def reset(self) -> None:
        self._entered = False

    def _universe(self, ctx: Context) -> list[tuple[Asset, float]]:
        """The screened, best-first slice of the `kind` universe."""
        wanted_ids = set(ctx.products.filter(pl.col("kind") == self.kind)["product_id"].to_list())
        # Rank every eligible mark. An asset with no history is not dropped —
        # it ranks last (0 prints, infinite spread) and stays buyable.
        rank: dict[tuple[int, str], tuple[int, float]] = {}
        if ctx.history.height:
            for pid, sub_type, n_prints, spread in rank_assets(ctx.history, ctx.today).iter_rows():
                rank[(pid, sub_type)] = (n_prints, spread)
        candidates = [
            (asset, price)
            for asset, price in ctx.marks.items()
            if asset.product_id in wanted_ids and price > 0
        ]
        candidates.sort(
            key=lambda ap: (
                -rank.get((ap[0].product_id, ap[0].sub_type), _UNRANKED)[0],
                rank.get((ap[0].product_id, ap[0].sub_type), _UNRANKED)[1],
                ap[0].product_id,
                ap[0].sub_type,
            )
        )
        return candidates[: self.n_assets]

    def on_bar(self, ctx: Context) -> list[Order]:
        if self._entered:
            return []
        self._entered = True

        universe = self._universe(ctx)
        if not universe:
            return []

        target = ctx.cash / len(universe)
        qty = [math.floor(target / price) for _, price in universe]
        cash = ctx.cash - sum(q * price for q, (_, price) in zip(qty, universe, strict=True))

        # Deficit sweep: buy one unit at a time of whichever screened asset is
        # furthest below its target dollar weight. Strict > means ties go to
        # the earliest in screen order, which is itself product_id-tie-broken,
        # so the result is total and reproducible in both engines.
        while True:
            best = -1
            best_deficit = 0.0
            for i, (_, price) in enumerate(universe):
                if price > cash:
                    continue
                deficit = target - qty[i] * price
                if deficit > best_deficit:
                    best_deficit = deficit
                    best = i
            if best < 0:
                break
            qty[best] += 1
            cash -= universe[best][1]

        return [
            Order(asset=asset, quantity=q)
            for q, (asset, _) in zip(qty, universe, strict=True)
            if q > 0
        ]
