"""The benchmark every strategy must beat: buy the universe, hold it.

Equal-dollar alone stopped being a benchmark once the price universe widened
from 23 tcgcsv groups to 217: `cash / len(universe)` fell below the price of
most sealed products, so the "fully invested" bar sat mostly in cash. Two
rules fix that without changing the thesis. A liquidity screen
(`research/screen.py`) picks the n_assets most tradeable products instead of
spreading across all of them, and a deficit sweep spends the remainder that
integer sizing leaves behind, one unit at a time. Between the two sits an
affordability filter (`_affordable`) that re-targets over the assets the
account can actually hold a unit of, without which the sweep concentrates
the account into whatever it cannot afford.

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


def _affordable(candidates: list[tuple[Asset, float]], cash: float) -> list[tuple[Asset, float]]:
    """Drop the screened assets the account cannot equal-weight into.

    Keeps the largest k such that the k CHEAPEST candidates each cost at most
    ``cash / k``, in screen order. The predicate is monotone — the k-th
    cheapest price only rises with k while ``cash / k`` only falls — so
    scanning ascending prices and stopping at the first failure finds the
    maximum k exactly, and the result is total (ties in price break on screen
    position, which is itself product_id-tie-broken).

    Why this exists. `on_bar`'s deficit sweep measures ``target - qty*price``,
    which SATURATES at ``target`` for any asset too expensive to hold one unit
    of, so the single most expensive product in the universe always carried the
    largest possible deficit and the sweep funded it out of every other asset's
    share. Measured on the published benchmark run: one $3,500 box took 50.05%
    of a $10,000 account (1 unit, $3,500 print + $1,500 impact) while the other
    37 filled assets held ~1.4% each — a bar documented as "screened top-100,
    equal-dollar" that was really one asset at half the book, marking on a
    price that had not printed for 19 months.

    Filtering rather than guarding is the load-bearing choice. The narrow fix
    (skip a candidate when ``price > target``) removes the concentration but
    strands every dropped asset's share in cash: measured on the same first
    bar, 35 of the 100 screened sealed products price above the $100 target, and
    the narrow guard lands at 84.2% of the account deployed with $1,579 idle,
    against 99.9% before. Re-targeting over the assets that survive spends the
    remainder AND bounds the weight — 99.9% deployed across 74 assets, largest
    weight 1.95% — because the freed shares raise ``target`` (here $100 →
    $135.14) for the assets that can actually absorb them. Equal-dollar over
    what the account can hold is a benchmark; equal-dollar over a wish list is
    a cash sleeve with a concentrated tail.

    Two consequences worth stating rather than discovering:

    * After this filter every kept asset holds at least one unit from the
      initial floor sizing (``price <= target``), so no deficit can saturate
      any more and the sweep needs no per-asset guard of its own — the
      pathology is gone by construction, not suppressed by a test. Note the
      review's suggested ``qty[i] == 0 and price > target`` is exactly
      ``price > target``: qty starts at ``floor(target / price)``, so an asset
      with any units already has ``price <= target``.
    * A kept asset can still overshoot its target by ONE unit (a positive
      deficit may be smaller than the price that closes it), i.e. it ends
      under 2x its share — 1.95% of the account above, not 50%. Forbidding
      that would stop the sweep from spending the remainder at all, which is
      the entire reason the sweep exists, so it is allowed and bounded rather
      than banned.

    ``k == 0`` (nothing priced at or below the whole balance) returns nothing:
    the account cannot buy a single unit of anything screened.
    """
    if not candidates:
        return []
    by_price = sorted(range(len(candidates)), key=lambda i: (candidates[i][1], i))
    k = 0
    for rank, i in enumerate(by_price, start=1):
        if candidates[i][1] > cash / rank:
            break
        k = rank
    return [candidates[i] for i in sorted(by_price[:k])]


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
        if self.n_assets <= 0:
            # "Hold nothing", matching the C++ guard in buy_and_hold.cpp.
            # Without this, `candidates[: self.n_assets]` reads a negative
            # n_assets as Python's "all but the last N" and silently trades
            # the rest, while C++ returns no orders — a divergence no
            # production path reaches (resolve_params pins [1, 10000]) but
            # both public constructors do.
            return []
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
        return _affordable(candidates[: self.n_assets], ctx.cash)

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
        #
        # `deficit` can no longer saturate at `target`: _affordable() has
        # already dropped every candidate priced above it, so each one holds
        # >= 1 unit here and its deficit is strictly under one unit's price.
        # Do not reintroduce unaffordable candidates and expect this loop to
        # behave — see _affordable's docstring for what happens when it can.
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
