"""Live mode: one on_bar at the latest warehouse date -> recommendations.

The strategy cannot tell it is live (same Context as backtests) — the
project's central design invariant. Params come from the LAST fold of the
latest walk-forward run (the most recently optimized regime); every report
carries that run's OOS summary so a recommendation is never separated from
its honest track record. Positions are empty and cash is hypothetical:
recommendations answer "what would this strategy enter today".

Portfolio mode: pass a real Portfolio (from the ledger) so the strategy's
own exit rule emits SELL recommendations for real holdings. Cash mode (the
default, pass cash=) and portfolio mode are mutually exclusive.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.data import MarketData
from pkmn_quant.engine.portfolio import Portfolio
from pkmn_quant.engine.strategy import Context
from pkmn_quant.live.ledger import LedgerError, Snapshot, make_snapshot
from pkmn_quant.research.artifacts import find_latest_wf_run, load_walkforward_json
from pkmn_quant.research.registry import REGISTRY

Params = dict[str, float | int]

DEFAULT_WARMUP_DAYS = 365

# Strategies whose exit rules read only Context. Since Plan 6, positions
# carry opened_on (engine fills and ledger replay both set it), so hold-day
# and rebalance clocks are reconstructible from a single live bar.
# ml-ranker satisfies this contract: its rebalance clock uses opened_on from
# Context and the model is retrained from ctx.history on each due bar.
PORTFOLIO_SAFE_STRATEGIES = frozenset(
    {
        "sealed-accumulation",
        "dip-buyer",
        "xs-momentum",
        "cost-aware-reversion",
        "ml-ranker",
        "ml-ranker-v2",
    }
)


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
    avg_cost: float | None = None  # SELLs in portfolio mode
    gain_pct: float | None = None
    # As-printed quotes on the as-of day (None when the asset did not print
    # that day) — the paper planner's impact inputs.
    mid: float | None = None
    low: float | None = None


@dataclass(frozen=True)
class SignalReport:
    as_of: date
    strategy: str
    params: Params
    wf_summary: dict[str, float]
    wf_run_dir: str
    recommendations: list[Recommendation]
    portfolio_snapshot: Snapshot | None = None
    paper: bool = False


def generate_signals(
    warehouse: Warehouse,
    strategy_name: str,
    results_dir: Path,
    cash: float | None = None,
    portfolio: Portfolio | None = None,
    warmup_days: int = DEFAULT_WARMUP_DAYS,
    paper: bool = False,
) -> SignalReport:
    entry = REGISTRY.get(strategy_name)
    if entry is None:
        raise SignalsError(f"unknown strategy {strategy_name!r}; known: {sorted(REGISTRY)}")

    if (cash is None) == (portfolio is None):
        raise SignalsError("provide either cash (hypothetical) or portfolio (ledger) — exactly one")
    if portfolio is not None and strategy_name not in PORTFOLIO_SAFE_STRATEGIES:
        raise SignalsError(
            f"{strategy_name!r} cannot run against real positions: its exit rule"
            f" needs entry dates the live Context does not carry yet"
            f" (supported: {sorted(PORTFOLIO_SAFE_STRATEGIES)})"
        )

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

    # Partition dir names give the latest date without scanning price data
    # (MarketData.from_warehouse below does the one full load).
    days = warehouse.stored_days()
    if not days:
        raise SignalsError("warehouse has no price data; run `pkmn ingest` first")
    latest = days[-1]

    market = MarketData.from_warehouse(warehouse, latest, latest, warmup_days=warmup_days)
    try:
        strategy = entry.factory(params)
    except (KeyError, ValueError, TypeError) as exc:
        raise SignalsError(
            f"artifact params in {run_dir} incompatible with {strategy_name!r} ({exc!r});"
            f" re-run `pkmn walkforward --strategy {strategy_name} ...`"
        ) from exc
    strategy.reset()

    if portfolio is not None:
        ctx_cash = portfolio.cash
        # Same trust-boundary idiom as the backtest loop (backtest.py):
        # replace() copies every field, including opened_on.
        ctx_positions = {a: replace(p) for a, p in portfolio.positions.items()}
    else:
        assert cash is not None
        ctx_cash = cash
        ctx_positions = {}

    ctx = Context(
        today=latest,
        history=market.history_until(latest),
        products=warehouse.load_products(),
        positions=ctx_positions,
        cash=ctx_cash,
        marks=market.marks_on(latest),
    )
    orders = strategy.on_bar(ctx)
    quotes = market.quotes_on(latest, [o.asset for o in orders]) if orders else {}

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
        held = portfolio.positions.get(order.asset) if portfolio is not None else None
        avg_cost = held.avg_cost if held is not None and order.quantity < 0 else None
        quote = quotes.get(order.asset)
        recommendations.append(
            Recommendation(
                action="BUY" if order.quantity > 0 else "SELL",
                product_id=order.asset.product_id,
                sub_type=order.asset.sub_type,
                name=names.get(order.asset.product_id, f"product {order.asset.product_id}"),
                quantity=qty,
                market_price=mark,
                notional=round(qty * mark, 2),
                avg_cost=avg_cost,
                # avg_cost==0.0 is falsy so the guard also blocks division-by-zero;
                # the ledger validates price > 0, so 0.0 is unreachable via real data.
                gain_pct=(mark / avg_cost - 1.0) if avg_cost else None,
                mid=quote.mid if quote is not None else None,
                low=quote.low if quote is not None else None,
            )
        )

    if portfolio is not None:
        try:
            snapshot = make_snapshot(portfolio, marks, names)
        except LedgerError as exc:
            raise SignalsError(
                f"cannot value portfolio — {exc};"
                f" try a larger --warmup-days so all held assets have a warehouse mark"
            ) from exc
    else:
        snapshot = None

    return SignalReport(
        as_of=latest,
        strategy=strategy_name,
        params=params,
        wf_summary=run.summary,
        wf_run_dir=str(run_dir),
        recommendations=recommendations,
        portfolio_snapshot=snapshot,
        paper=paper,
    )
