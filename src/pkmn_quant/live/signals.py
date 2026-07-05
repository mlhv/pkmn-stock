"""Live mode: one on_bar at the latest warehouse date -> recommendations.

The strategy cannot tell it is live (same Context as backtests) — the
project's central design invariant. Params come from the LAST fold of the
latest walk-forward run (the most recently optimized regime); every report
carries that run's OOS summary so a recommendation is never separated from
its honest track record. Positions are empty and cash is hypothetical:
recommendations answer "what would this strategy enter today".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.data import MarketData
from pkmn_quant.engine.strategy import Context
from pkmn_quant.research.artifacts import find_latest_wf_run, load_walkforward_json
from pkmn_quant.research.registry import REGISTRY

Params = dict[str, float | int]

DEFAULT_WARMUP_DAYS = 365


class SignalsError(Exception):
    """User-facing signal-generation failure (clean CLI message)."""


@dataclass(frozen=True)
class Recommendation:
    action: str  # "BUY" | "SELL"
    product_id: int
    sub_type: str
    name: str
    quantity: int
    market_price: float
    notional: float


@dataclass(frozen=True)
class SignalReport:
    as_of: date
    strategy: str
    params: Params
    wf_summary: dict[str, float]
    wf_run_dir: str
    recommendations: list[Recommendation]


def generate_signals(
    warehouse: Warehouse,
    strategy_name: str,
    cash: float,
    results_dir: Path,
    warmup_days: int = DEFAULT_WARMUP_DAYS,
) -> SignalReport:
    entry = REGISTRY.get(strategy_name)
    if entry is None:
        raise SignalsError(f"unknown strategy {strategy_name!r}; known: {sorted(REGISTRY)}")

    run_dir = find_latest_wf_run(results_dir, strategy_name)
    if run_dir is None:
        raise SignalsError(
            f"no walk-forward run found for {strategy_name!r} in {results_dir};"
            f" run `pkmn walkforward --strategy {strategy_name} ...` first"
        )
    try:
        run = load_walkforward_json(run_dir)
    except (ValueError, KeyError, TypeError) as exc:
        raise SignalsError(
            f"corrupt walkforward.json in {run_dir} ({exc});"
            f" re-run `pkmn walkforward --strategy {strategy_name} ...`"
        ) from exc
    if not run.folds:
        raise SignalsError(f"walk-forward run {run_dir} has no folds")
    params = run.folds[-1].params

    prices = warehouse.load_prices()
    if prices.height == 0:
        raise SignalsError("warehouse has no price data; run `pkmn ingest` first")
    latest = prices["date"].max()
    assert isinstance(latest, date)

    market = MarketData.from_warehouse(warehouse, latest, latest, warmup_days=warmup_days)
    try:
        strategy = entry.factory(params)
    except (KeyError, ValueError, TypeError) as exc:
        raise SignalsError(
            f"artifact params in {run_dir} incompatible with {strategy_name!r} ({exc!r});"
            f" re-run `pkmn walkforward --strategy {strategy_name} ...`"
        ) from exc
    strategy.reset()
    ctx = Context(
        today=latest,
        history=market.history_until(latest),
        products=warehouse.load_products(),
        positions={},
        cash=cash,
        marks=market.marks_on(latest),
    )
    orders = strategy.on_bar(ctx)

    names = {
        int(r["product_id"]): str(r["name"])
        for r in ctx.products.select("product_id", "name").iter_rows(named=True)
    }
    marks = ctx.marks
    recommendations: list[Recommendation] = []
    for order in orders:
        mark = marks.get(order.asset)
        if mark is None:  # unreachable: strategies only order marked assets
            continue
        qty = abs(order.quantity)
        recommendations.append(
            Recommendation(
                action="BUY" if order.quantity > 0 else "SELL",
                product_id=order.asset.product_id,
                sub_type=order.asset.sub_type,
                name=names.get(order.asset.product_id, f"product {order.asset.product_id}"),
                quantity=qty,
                market_price=mark,
                notional=round(qty * mark, 2),
            )
        )

    return SignalReport(
        as_of=latest,
        strategy=strategy_name,
        params=params,
        wf_summary=run.summary,
        wf_run_dir=str(run_dir),
        recommendations=recommendations,
    )
