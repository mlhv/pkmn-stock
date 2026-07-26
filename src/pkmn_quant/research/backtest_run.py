"""Parameterized single-strategy backtest capability (Plan 2b).

One entry point the CLI and (later) the web trigger both call: build any
registered strategy from name + params, run it on the native engine with cash
+ initial holdings over a window, write artifacts + a registry record. Native
engine only — never the Python Backtest.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import polars as pl

from pkmn_quant.config import Paths
from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.backtest import Result
from pkmn_quant.engine.costs import CostModel
from pkmn_quant.engine.native import (
    NATIVE_STRATEGY_NAMES,
    NativeBacktest,
    NativeStrategySpec,
    SeededHolding,
)
from pkmn_quant.engine.prepared import PreparedMarket
from pkmn_quant.engine.strategy import Strategy
from pkmn_quant.research.registry import REGISTRY, Params
from pkmn_quant.research.runs import new_run_id, record_run

# A param may arrive as a string (CLI --param k=v) or a number (web JSON).
ParamValue = str | float | int


@dataclass(frozen=True)
class BacktestRunResult:
    run_id: str | None
    result: Result
    artifact_dir: Path


def resolve_params(strategy_name: str, params: dict[str, ParamValue]) -> Params:
    """Validate + coerce + default-fill user params against the strategy's
    ParamSpecs. Accepts str or numeric values. buy-and-hold takes none.
    Raises ValueError on any problem."""
    if strategy_name == "buy-and-hold":
        if params:
            raise ValueError("buy-and-hold has no tunable parameters")
        return {}
    entry = REGISTRY.get(strategy_name)
    if entry is None:
        raise ValueError(f"unknown strategy {strategy_name!r}; known: {sorted(REGISTRY)}")
    by_name = {p.name: p for p in entry.params}
    unknown = set(params) - set(by_name)
    if unknown:
        raise ValueError(f"unknown param(s) {sorted(unknown)} for {strategy_name}")
    out: Params = {}
    for spec in entry.params:
        raw = params.get(spec.name, spec.default)
        try:
            # int(float(raw)) handles str/float/int uniformly ("2.0" -> 2).
            val: float | int = int(float(raw)) if spec.kind == "int" else float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{spec.name}={raw!r} is not a valid {spec.kind} value for {strategy_name}: {exc}"
            ) from exc
        if not (spec.low <= val <= spec.high):
            raise ValueError(
                f"{spec.name}={val} out of range [{spec.low}, {spec.high}] for {strategy_name}"
            )
        out[spec.name] = val
    return out


def _build_strategy(strategy_name: str, params: Params, kind: str) -> NativeStrategySpec | Strategy:
    if strategy_name == "buy-and-hold":
        return NativeStrategySpec("buy-and-hold", {}, kind=kind)
    if strategy_name in NATIVE_STRATEGY_NAMES:
        return NativeStrategySpec(strategy_name, {k: float(v) for k, v in params.items()})
    # ml-ranker / ml-ranker-v2: Python Strategy via the callback bridge.
    return REGISTRY[strategy_name].factory(params)


def _validate_holdings(prepared: PreparedMarket, holdings: list[SeededHolding]) -> None:
    """Reject a starting holding whose asset can't legally seed this window.

    Two independent checks, both required:
      1. universe membership — the asset must appear somewhere in [start, end]
         (``prepared.asset_index``); NativeBacktest itself also enforces this
         (mapping to a dense C++ asset id), but checking here gives an error
         before any C++ boundary crossing.
      2. a mark on the window's first trading day — marks_on carries forward
         from a PRIOR print, never backward from a later one, so an asset
         that first prints after day 0 has no legitimate day-0 value to seed
         with, even though it's a valid in-universe tradeable asset overall.
    """
    if not holdings:
        return
    day0 = prepared.market.days[0]
    marks = prepared.market.marks_on(day0)
    for h in holdings:
        if h.asset not in prepared.asset_index:
            raise ValueError(f"holding asset {h.asset} not in backtest universe")
        if h.asset not in marks:
            raise ValueError(f"holding asset {h.asset} has no price on/before window start {day0}")


def _holdings_config(holdings: list[SeededHolding]) -> list[dict[str, object]]:
    rows = [
        {
            "product_id": h.asset.product_id,
            "sub_type": h.asset.sub_type,
            "quantity": h.quantity,
            "avg_cost": h.avg_cost,
            "opened_on": h.opened_on.isoformat(),
        }
        for h in holdings
    ]
    return sorted(rows, key=lambda r: (r["product_id"], r["sub_type"]))


def _write_artifacts(result: Result, run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    result.equity_curve.write_parquet(run_dir / "equity.parquet")
    pl.DataFrame(
        [
            {
                "day": f.day,
                "product_id": f.asset.product_id,
                "sub_type": f.asset.sub_type,
                "quantity": f.quantity,
                "price": f.price,
                "fees": f.fees,
                "impact": f.impact,
            }
            for f in result.fills
        ],
        schema={
            "day": pl.Date,
            "product_id": pl.Int64,
            "sub_type": pl.Utf8,
            "quantity": pl.Int64,
            "price": pl.Float64,
            "fees": pl.Float64,
            "impact": pl.Float64,
        },
    ).write_parquet(run_dir / "fills.parquet")


def run_single_backtest(
    root: Path,
    strategy_name: str,
    params: dict[str, ParamValue],
    cash: float,
    holdings: list[SeededHolding],
    start: date,
    end: date,
    *,
    impact: bool = True,
    warmup_days: int = 0,
    kind: str = "sealed",
) -> BacktestRunResult:
    resolved = resolve_params(strategy_name, params)
    wh = Warehouse(Paths(root=root))
    cm = CostModel(impact_enabled=impact)
    prepared = PreparedMarket.prepare(wh, start, end, warmup_days=warmup_days)
    _validate_holdings(prepared, holdings)
    strategy = _build_strategy(strategy_name, resolved, kind)

    result = NativeBacktest(
        warehouse=wh,
        strategy=strategy,
        cost_model=cm,
        start=start,
        end=end,
        initial_cash=cash,
        warmup_days=warmup_days,
        initial_holdings=holdings,
        prepared=prepared,
    ).run()

    run_id = new_run_id()
    run_dir = root / "data" / "results" / run_id
    _write_artifacts(result, run_dir)

    recorded = record_run(
        root=root,
        command="backtest",
        strategy=result.strategy_name,
        config={
            "command": "backtest",
            "strategy": strategy_name,
            "params": resolved,
            "holdings": _holdings_config(holdings),
            "cash": cash,
            "kind": kind if strategy_name == "buy-and-hold" else None,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "impact": impact,
            "warmup_days": warmup_days,
            "cost_model": cm.as_dict(),
        },
        results=result.summary,
        artifact_path=run_dir,
        warehouse=wh,
        run_id=run_id,
    )
    return BacktestRunResult(run_id=recorded, result=result, artifact_dir=run_dir)
