"""The daily event loop: history -> strategy -> orders -> T+1 fills -> equity."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import Any

import polars as pl

from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.costs import CostModel
from pkmn_quant.engine.data import MarketData
from pkmn_quant.engine.execution import ExecutionSimulator, Order
from pkmn_quant.engine.metrics import summarize
from pkmn_quant.engine.portfolio import Fill, Portfolio
from pkmn_quant.engine.strategy import Context, Strategy


@dataclass(frozen=True)
class Result:
    """A completed backtest. Treat all fields as read-only: frozen only
    protects rebinding, not the list/frame/dict contents."""

    strategy_name: str
    equity_curve: pl.DataFrame  # date, equity
    fills: list[Fill]
    summary: dict[str, float]
    cost_model: dict[str, Any]


@dataclass
class Backtest:
    warehouse: Warehouse
    strategy: Strategy
    cost_model: CostModel
    start: date
    end: date
    initial_cash: float
    warmup_days: int = 0
    """Observe-only history days loaded before ``start``.

    When > 0, MarketData loads prices from ``start - warmup_days`` through
    ``end`` so that strategy look-backs (momentum, dip windows, peak-to-date)
    see history on the first trading day.  The event loop still iterates only
    days in [start, end]; no trades are generated during the warm-up period.
    Default 0 preserves the original behaviour bit-for-bit.
    """

    def run(self) -> Result:
        self.strategy.reset()
        market = MarketData.from_warehouse(
            self.warehouse, self.start, self.end, warmup_days=self.warmup_days
        )
        products = self.warehouse.load_products()
        simulator = ExecutionSimulator(self.cost_model)
        portfolio = Portfolio(cash=self.initial_cash)
        fills: list[Fill] = []
        curve_days: list[date] = []
        curve_equity: list[float] = []
        # Orders awaiting T+1 fill. Local so repeated run() calls are
        # independent (a field would leak last-day orders across runs).
        pending: list[Order] = []

        for day in market.days:
            # 1. Yesterday's orders fill at today's actually-printed prices.
            #    Quotes (mid/low for impact) are resolved lazily for the
            #    ordered assets only — the hot per-day dict paths stay as
            #    Plan 8 tuned them.
            quotes = market.quotes_on(day, [o.asset for o in pending]) if pending else {}
            fills.extend(
                simulator.execute(pending, market.prices_on(day), portfolio, day, quotes=quotes)
            )
            pending = []

            # 2. Strategy sees history <= today and emits orders for tomorrow.
            # positions is copied per-Position via replace() (sufficient: all
            # Position fields are immutable); a buggy strategy must not be
            # able to edit real holdings.
            marks = market.marks_on(day)  # computed once: used by Context AND equity
            ctx = Context(
                today=day,
                history=market.history_until(day),
                products=products,
                positions={a: replace(p) for a, p in portfolio.positions.items()},
                cash=portfolio.cash,
                marks=marks,
            )
            pending = self.strategy.on_bar(ctx)

            # 3. Record today's mark-to-market equity.
            curve_days.append(day)
            curve_equity.append(portfolio.equity(marks))

        equity_curve = pl.DataFrame({"date": curve_days, "equity": curve_equity})
        return Result(
            strategy_name=self.strategy.name,
            equity_curve=equity_curve,
            fills=list(fills),  # Result owns its copy
            summary=summarize(equity_curve),
            cost_model=self.cost_model.as_dict(),
        )
