"""NativeBacktest: the C++ engine behind the same Result type.

Crosses the Python/C++ boundary once per run: MarketData loads and shapes
the data exactly as the Python engine sees it (same frame, same mark
change-point order), flattened to numpy arrays. Fills and equity come back
and are repackaged into engine.backtest.Result, so downstream consumers
(runs registry, reports, walk-forward stitching) cannot tell engines apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import polars as pl

from pkmn_quant import _engine
from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.backtest import Result
from pkmn_quant.engine.costs import CostModel
from pkmn_quant.engine.metrics import summarize
from pkmn_quant.engine.portfolio import Fill, Position
from pkmn_quant.engine.prepared import _KIND_CODES, PreparedMarket
from pkmn_quant.engine.strategy import Context, Strategy

_EPOCH = date(1970, 1, 1)

# Rule strategies with a native C++ port (factory.cpp). Anything else runs
# on the C++ engine via the callback bridge.
NATIVE_STRATEGY_NAMES: frozenset[str] = frozenset(
    {"buy-and-hold", "sealed-accumulation", "dip-buyer", "xs-momentum", "cost-aware-reversion"}
)


def _from_day(i: int) -> date:
    return _EPOCH + timedelta(days=int(i))


@dataclass(frozen=True)
class NativeStrategySpec:
    """A strategy the C++ factory can build: registry name + params."""

    name: str
    params: dict[str, float]
    kind: str = "sealed"  # only read by buy-and-hold


@dataclass
class NativeBacktest:
    """Drop-in for engine.backtest.Backtest, running the C++ engine.

    strategy: a NativeStrategySpec (native C++ strategy) or a Python
    Strategy instance (runs unmodified via the per-bar callback bridge —
    correct but without the native speedup).
    """

    warehouse: Warehouse
    strategy: NativeStrategySpec | Strategy
    cost_model: CostModel
    start: date
    end: date
    initial_cash: float
    warmup_days: int = 0
    prepared: PreparedMarket | None = None

    def run(self) -> Result:
        p = self.prepared
        if p is None:
            p = PreparedMarket.prepare(
                self.warehouse, self.start, self.end, warmup_days=self.warmup_days
            )
        elif (p.start, p.end, p.warmup_days) != (self.start, self.end, self.warmup_days):
            raise ValueError(
                "PreparedMarket window mismatch: prepared for "
                f"({p.start}, {p.end}, warmup={p.warmup_days}), run wants "
                f"({self.start}, {self.end}, warmup={self.warmup_days})"
            )

        tiers = self.cost_model.liquidity_tiers
        tier_thresholds = np.array([t for t, _ in tiers], dtype=np.float64)
        tier_qtys = np.array([q for _, q in tiers], dtype=np.int64)

        if isinstance(self.strategy, NativeStrategySpec):
            name = self.strategy.name
            params = {k: float(v) for k, v in self.strategy.params.items()}
            if name == "buy-and-hold" and self.strategy.kind not in _KIND_CODES:
                raise ValueError(f"unknown kind {self.strategy.kind!r}; choose sealed or single")
            universe_kind = _KIND_CODES.get(self.strategy.kind, -1)
            callback = None
            strategy_name = f"buy-and-hold-{self.strategy.kind}" if name == "buy-and-hold" else name
        else:
            strategy = self.strategy
            strategy.reset()  # Backtest.run() parity: fresh per-run state
            name, params, universe_kind = "", {}, -1
            strategy_name = strategy.name

            def callback(
                day_i: int, raw: list[tuple[int, int, float, int]], cash: float
            ) -> list[tuple[int, int]]:
                today = _from_day(day_i)
                positions = {
                    p.asset_list[aid]: Position(quantity=qty, avg_cost=avg, opened_on=_from_day(op))
                    for aid, qty, avg, op in raw
                }
                ctx = Context(
                    today=today,
                    history=p.market.history_until(today),
                    products=p.products,
                    positions=positions,
                    cash=cash,
                    marks=p.market.marks_on(today),
                )
                return [(p.asset_index[o.asset], o.quantity) for o in strategy.on_bar(ctx)]

        days_out, equity_out, fills_out = _engine.run_backtest(
            trading_days=p.trading_days,
            row_day=p.row_day,
            row_asset=p.row_asset,
            row_market=p.row_market,
            row_mid=p.row_mid,
            row_low=p.row_low,
            ev_day=p.ev_day,
            ev_asset=p.ev_asset,
            ev_price=p.ev_price,
            prod_id=p.prod_id,
            prod_kind=p.prod_kind,
            prod_released=p.prod_released,
            strategy_name=name,
            params=params,
            universe_kind=universe_kind,
            fee_rate=self.cost_model.fee_rate,
            shipping_per_line=self.cost_model.shipping_per_line,
            tier_thresholds=tier_thresholds,
            tier_qtys=tier_qtys,
            fallback_max_qty=self.cost_model.fallback_max_qty,
            impact_enabled=self.cost_model.impact_enabled,
            initial_cash=self.initial_cash,
            callback=callback,
        )

        equity_curve = pl.DataFrame(
            {"date": [_from_day(i) for i in days_out], "equity": equity_out},
            schema={"date": pl.Date, "equity": pl.Float64},
        )
        fills = [
            Fill(
                day=_from_day(d),
                asset=p.asset_list[aid],
                quantity=qty,
                price=price,
                fees=fees,
                impact=impact,
            )
            for d, aid, qty, price, fees, impact in fills_out
        ]
        return Result(
            strategy_name=strategy_name,
            equity_curve=equity_curve,
            fills=fills,
            summary=summarize(equity_curve),
            cost_model=self.cost_model.as_dict(),
        )
