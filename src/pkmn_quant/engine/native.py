"""NativeBacktest: the C++ engine behind the same Result type.

Crosses the Python/C++ boundary once per run: MarketData loads and shapes
the data exactly as the Python engine sees it (same frame, same mark
change-point order), flattened to numpy arrays. Fills and equity come back
and are repackaged into engine.backtest.Result, so downstream consumers
(runs registry, reports, walk-forward stitching) cannot tell engines apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import polars as pl

from pkmn_quant import _engine
from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.backtest import Result
from pkmn_quant.engine.costs import CostModel
from pkmn_quant.engine.data import MarketData
from pkmn_quant.engine.metrics import summarize
from pkmn_quant.engine.portfolio import Asset, Fill, Position
from pkmn_quant.engine.strategy import Context, Strategy

_EPOCH = date(1970, 1, 1)
_NULL_DAY = -(2**31)
_KIND_CODES = {"sealed": 0, "single": 1}

# Rule strategies with a native C++ port (factory.cpp). Anything else runs
# on the C++ engine via the callback bridge.
NATIVE_STRATEGY_NAMES: frozenset[str] = frozenset(
    {"buy-and-hold", "sealed-accumulation", "dip-buyer", "xs-momentum", "cost-aware-reversion"}
)


def _to_day(d: date) -> int:
    return (d - _EPOCH).days


def _from_day(i: int) -> date:
    return _EPOCH + timedelta(days=int(i))


def _kind_code(kind: str | None) -> int:
    """-1 ("other") for both an unrecognized kind string and None.

    None means a priced asset has no row at all in products.parquet -- a
    real condition in the warehouse (upstream tcgcsv catalog drift, not
    stale local data; see docs/research-findings-2026-07.md Plan 10). The
    Python engine never requires a catalog row: strategies build their
    universe by filtering/joining against ctx.products, so an uncataloged
    asset is simply absent from that join. Kind "other" reproduces that
    exactly here: it fails the `kind == 0`/`kind == 1` checks in the four
    kind-filtered strategies (buy-and-hold, sealed-accumulation, dip-buyer,
    xs-momentum) but cost-aware-reversion has no kind filter at all
    (cost_aware_reversion.py:76-97 scans every asset), so it stays a
    tradeable candidate there -- dropping the asset instead of tagging it
    "other" would silently break that parity.
    """
    if kind is None:
        return -1
    return _KIND_CODES.get(kind, -1)


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
    _asset_list: list[Asset] = field(default_factory=list, init=False, repr=False)

    def run(self) -> Result:
        market = MarketData.from_warehouse(
            self.warehouse, self.start, self.end, warmup_days=self.warmup_days
        )
        products = self.warehouse.load_products()

        frame = market.frame.sort("date")
        assets_df = (
            frame.select("product_id", "sub_type")
            .unique()
            .sort(["product_id", "sub_type"])
            .with_row_index("asset_id")
        )
        asset_list = [
            Asset(product_id=int(pid), sub_type=str(st))
            for pid, st in assets_df.select("product_id", "sub_type").iter_rows()
        ]
        self._asset_list = asset_list
        asset_index = {a: i for i, a in enumerate(asset_list)}

        joined = frame.join(assets_df, on=["product_id", "sub_type"], how="left").sort("date")
        row_day = joined["date"].cast(pl.Int32).to_numpy().astype(np.int32)
        row_asset = joined["asset_id"].cast(pl.Int32).to_numpy().astype(np.int32)
        row_market = joined["market"].cast(pl.Float64).to_numpy().astype(np.float64)
        nan = float("nan")
        row_mid = joined["mid"].cast(pl.Float64).fill_null(nan).to_numpy().astype(np.float64)
        row_low = joined["low"].cast(pl.Float64).fill_null(nan).to_numpy().astype(np.float64)

        events = market.mark_events()
        ev_day = np.array([_to_day(d) for d, _, _ in events], dtype=np.int32)
        ev_asset = np.array([asset_index[a] for _, a, _ in events], dtype=np.int32)
        ev_price = np.array([p for _, _, p in events], dtype=np.float64)

        prod_info: dict[int, tuple[str, date | None]] = {
            int(r["product_id"]): (str(r["kind"]), r["released_on"])
            for r in products.iter_rows(named=True)
        }
        # .get(..., (None, None)): a priced asset absent from products.parquet
        # is not an error (see _kind_code docstring) -- it gets kind "other"
        # and no release date, same as the Python engine's implicit handling.
        prod_id = np.array([a.product_id for a in asset_list], dtype=np.int64)
        prod_kind = np.array(
            [_kind_code(prod_info.get(a.product_id, (None, None))[0]) for a in asset_list],
            dtype=np.int8,
        )
        prod_released = np.array(
            [
                _to_day(rel)
                if (rel := prod_info.get(a.product_id, (None, None))[1]) is not None
                else _NULL_DAY
                for a in asset_list
            ],
            dtype=np.int32,
        )

        trading_days = np.array([_to_day(d) for d in market.days], dtype=np.int32)
        tiers = self.cost_model.liquidity_tiers
        tier_thresholds = np.array([t for t, _ in tiers], dtype=np.float64)
        tier_qtys = np.array([q for _, q in tiers], dtype=np.int64)

        if isinstance(self.strategy, NativeStrategySpec):
            name = self.strategy.name
            params = {k: float(v) for k, v in self.strategy.params.items()}
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
                    asset_list[aid]: Position(quantity=qty, avg_cost=avg, opened_on=_from_day(op))
                    for aid, qty, avg, op in raw
                }
                ctx = Context(
                    today=today,
                    history=market.history_until(today),
                    products=products,
                    positions=positions,
                    cash=cash,
                    marks=market.marks_on(today),
                )
                return [(asset_index[o.asset], o.quantity) for o in strategy.on_bar(ctx)]

        days_out, equity_out, fills_out = _engine.run_backtest(
            trading_days=trading_days,
            row_day=row_day,
            row_asset=row_asset,
            row_market=row_market,
            row_mid=row_mid,
            row_low=row_low,
            ev_day=ev_day,
            ev_asset=ev_asset,
            ev_price=ev_price,
            prod_id=prod_id,
            prod_kind=prod_kind,
            prod_released=prod_released,
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
                asset=asset_list[aid],
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
