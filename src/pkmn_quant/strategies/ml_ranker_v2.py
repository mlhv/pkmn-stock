"""Cross-sectional ML ranker v2: friction features, net-of-cost labels,
in-loop purged validation. ml-ranker (v1) is the frozen ablation baseline.

Differences from v1, each with its own honest-evaluation rationale:
- Features: FEATURE_COLS_V2 adds spread/friction, momentum-shape, and
  cross-sectional-rank features (research/features.py).
- Labels: horizon forward return NET of the per-row round-trip cost from
  ``label_cost`` (default: the real CostModel with impact on) — the model
  learns what clears the toll, not what merely rises.
- Model selection: research/purged.py picks (max_iter, learning_rate) from
  a fixed grid on an embargoed, most-recent-dates validation split, then
  refits on the full frame; early_stopping is always False (the sklearn
  auto early-stopping split is random and leaks under correlated labels).

Trading skeleton (rebalance clock, sells-first, equity/len(target) sizing,
min_price trade filter, deterministic tie-breaks) is v1 verbatim.
Stateless and live-safe for the same reasons as v1."""

from __future__ import annotations

import math
from datetime import date

import numpy as np

from pkmn_quant.engine.costs import CostModel
from pkmn_quant.engine.execution import Order
from pkmn_quant.engine.portfolio import Asset
from pkmn_quant.engine.strategy import Context, Strategy
from pkmn_quant.research.features import (
    FEATURE_COLS_V2,
    build_features_v2,
    build_training_frame_v2,
)
from pkmn_quant.research.purged import DEFAULT_GRID, _make_model, select_config


class MLRankerV2(Strategy):
    def __init__(
        self,
        horizon_days: int = 30,
        rebalance_days: int = 30,
        top_n: int = 8,
        train_days: int = 365,
        stride_days: int | None = None,
        min_price: float = 3.0,
        min_train_rows: int = 200,
        min_samples_leaf: int = 20,
        label_cost: CostModel = CostModel(impact_enabled=True),  # noqa: B008 (frozen, immutable)
    ) -> None:
        self.horizon_days = horizon_days
        self.rebalance_days = rebalance_days
        self.top_n = top_n
        self.train_days = train_days
        self.stride_days = stride_days if stride_days is not None else horizon_days
        self.min_price = min_price
        self.min_train_rows = min_train_rows
        self.min_samples_leaf = min_samples_leaf
        self.label_cost = label_cost
        self.name = "ml-ranker-v2"

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
        training = build_training_frame_v2(
            ctx.history,
            ctx.products,
            ctx.today,
            horizon_days=self.horizon_days,
            train_days=self.train_days,
            stride_days=self.stride_days,
            cost_model=self.label_cost,
        )
        if training.height < self.min_train_rows:
            return []
        usable_cols = [c for c in FEATURE_COLS_V2 if training[c].null_count() < training.height]
        if not usable_cols:
            return []
        config = select_config(
            training,
            usable_cols,
            self.horizon_days,
            grid=DEFAULT_GRID,
            min_samples_leaf=self.min_samples_leaf,
        )
        model = _make_model(config, self.min_samples_leaf)
        model.fit(
            training.select(usable_cols).to_numpy(),
            training["label"].to_numpy(),
        )
        today_feats = build_features_v2(ctx.history, ctx.products, ctx.today)
        if today_feats.height == 0:
            return []
        preds = model.predict(today_feats.select(usable_cols).to_numpy())
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
