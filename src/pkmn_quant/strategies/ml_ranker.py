"""Cross-sectional ML ranker: hold the top-N by predicted forward return.

The model is trained INSIDE on_bar from ctx.history — the engine's
anti-look-ahead wall (history_until) makes leakage structurally impossible,
and the label builder additionally bounds training dates to
as_of - horizon_days (see research/features.py). Universe: every product
printing today (kind is a feature; the model may learn "sealed trends").

Rebalance clock, sells-first ordering, and equity/len(target) sizing are
xs-momentum's, verbatim: due when flat or when the newest opened_on is
rebalance_days old; drop-outs from the target are sold in full; buys top
held names up to the per-name allocation. Not-due bars return [].

Degenerate data: fewer than min_train_rows training rows -> no model, no
target, no orders that bar (hold). Reachable in early-history folds and
tiny test contexts; not an error.

Stateless: the fitted model is a local of the due-bar path; nothing
survives between invocations that cannot be rebuilt from Context, so a
single live bar (365d warm-up history) behaves like a backtest bar.
Determinism is pinned by tests (random_state=0)."""

from __future__ import annotations

import math
from datetime import date

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor

from pkmn_quant.engine.execution import Order
from pkmn_quant.engine.portfolio import Asset
from pkmn_quant.engine.strategy import Context, Strategy
from pkmn_quant.research.features import FEATURE_COLS, build_features, build_training_frame


class MLRanker(Strategy):
    def __init__(
        self,
        horizon_days: int = 30,
        rebalance_days: int = 30,
        top_n: int = 8,
        train_days: int = 365,
        stride_days: int | None = None,
        min_price: float = 3.0,
        min_train_rows: int = 200,
        max_iter: int = 100,
        learning_rate: float = 0.1,
        min_samples_leaf: int = 20,
    ) -> None:
        self.horizon_days = horizon_days
        self.rebalance_days = rebalance_days
        self.top_n = top_n
        self.train_days = train_days
        self.stride_days = stride_days if stride_days is not None else horizon_days
        self.min_price = min_price
        self.min_train_rows = min_train_rows
        self.max_iter = max_iter
        self.learning_rate = learning_rate
        self.min_samples_leaf = min_samples_leaf
        self.name = "ml-ranker"

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

        training = build_training_frame(
            ctx.history,
            ctx.products,
            ctx.today,
            horizon_days=self.horizon_days,
            train_days=self.train_days,
            stride_days=self.stride_days,
        )
        if training.height < self.min_train_rows:
            return []  # no model -> no target -> hold (documented above)

        model = HistGradientBoostingRegressor(
            max_iter=self.max_iter,
            learning_rate=self.learning_rate,
            min_samples_leaf=self.min_samples_leaf,
            random_state=0,
        )
        model.fit(
            training.select(FEATURE_COLS).to_numpy(),
            training["label"].to_numpy(),
        )

        today_feats = build_features(ctx.history, ctx.products, ctx.today)
        if today_feats.height == 0:
            return []
        preds = model.predict(today_feats.select(FEATURE_COLS).to_numpy())
        ranked: list[tuple[float, Asset, float]] = []
        for score, r in zip(np.asarray(preds), today_feats.iter_rows(named=True), strict=True):
            asset = Asset(product_id=int(r["product_id"]), sub_type=str(r["sub_type"]))
            mark = ctx.marks.get(asset)
            if mark is None or mark < self.min_price:  # trade-only; training sees full universe
                continue
            ranked.append((-float(score), asset, mark))
        ranked.sort(key=lambda t: (t[0], t[1].product_id))
        target = {asset: mark for _, asset, mark in ranked[: self.top_n]}

        orders: list[Order] = []
        # Sells first: the executor fills sequentially on T+1, so sell
        # proceeds are in cash before any buy fill is attempted.
        for asset, pos in sorted(ctx.positions.items(), key=lambda kv: kv[0].product_id):
            if asset not in target:
                orders.append(Order(asset=asset, quantity=-pos.quantity))
        if not target:
            return orders

        equity = ctx.cash + sum(pos.quantity * ctx.marks[a] for a, pos in ctx.positions.items())
        per_name = equity / len(target)
        for asset, mark in sorted(target.items(), key=lambda kv: kv[0].product_id):
            held = ctx.positions.get(asset)
            held_value = held.quantity * mark if held else 0.0
            qty = math.floor((per_name - held_value) / mark)
            if qty > 0:
                orders.append(Order(asset=asset, quantity=qty))
        return orders
