"""Parameterized single-strategy backtest capability (Plan 2b).

One entry point the CLI and the web trigger both call: build any
registered strategy from name + params, run it on the native engine with cash
+ initial holdings over a window, write artifacts + a registry record. Native
engine only — never the Python Backtest.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

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
from pkmn_quant.research.registry import REGISTRY, Params, ParamSpec
from pkmn_quant.research.runs import new_run_id, record_run

# A param may arrive as a string (CLI --param k=v) or a number (web JSON).
ParamValue = str | float | int

# buy-and-hold stays out of REGISTRY on purpose: it is the benchmark, not a
# tunable research strategy, so it has no optuna search space and nothing
# walk-forward should be tuning. It does take one knob — how many screened
# assets to spread across — so it carries its own ParamSpec here.
BUY_AND_HOLD_PARAMS: tuple[ParamSpec, ...] = (
    ParamSpec(name="n_assets", kind="int", default=100, low=1, high=10_000),
)


@dataclass(frozen=True)
class BacktestRunResult:
    run_id: str | None
    result: Result
    artifact_dir: Path


def resolve_params(strategy_name: str, params: dict[str, ParamValue]) -> Params:
    """Validate + coerce + default-fill user params against the strategy's
    ParamSpecs. Accepts str or numeric values. buy-and-hold takes exactly one
    (`n_assets`); every other strategy takes its RegistryEntry's specs.
    Raises ValueError on any problem.

    Note on int coercion: an "int"-kind param is coerced via
    ``int(float(raw))``, which TRUNCATES rather than rejects a fractional
    value ("90.9" -> 90, 90.9 -> 90). A typo like ``--param
    min_age_days=90.9`` silently runs with 90, not an error. This is
    pinned by tests and by the CLI's string-param path (every ``--param
    k=v`` value arrives as a string) — do not change the behavior; if you
    need stricter validation, add it as a new, separate check.
    """
    if strategy_name == "buy-and-hold":
        specs = BUY_AND_HOLD_PARAMS
    else:
        entry = REGISTRY.get(strategy_name)
        if entry is None:
            raise ValueError(f"unknown strategy {strategy_name!r}; known: {sorted(REGISTRY)}")
        specs = entry.params
    unknown = set(params) - {p.name for p in specs}
    if unknown:
        raise ValueError(f"unknown param(s) {sorted(unknown)} for {strategy_name}")
    out: Params = {}
    for spec in specs:
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
        return NativeStrategySpec(
            "buy-and-hold", {k: float(v) for k, v in params.items()}, kind=kind
        )
    if strategy_name in NATIVE_STRATEGY_NAMES:
        return NativeStrategySpec(strategy_name, {k: float(v) for k, v in params.items()})
    # ml-ranker / ml-ranker-v2: Python Strategy via the callback bridge.
    return REGISTRY[strategy_name].factory(params)


def validate_opened_on(index: int, opened_on: date, start: date) -> None:
    """Reject a STARTING holding opened after the window starts.

    Incoherent by definition — the position is held on day 0, so it cannot
    have been opened later — and silently corrosive if allowed through: five
    strategies derive their exit/rebalance timing from
    ``(ctx.today - pos.opened_on).days``, which goes NEGATIVE for an
    opened_on past the window, so the age gate never fires. For xs-momentum
    and both ml-rankers the rebalance gate is "flat OR newest opened_on old
    enough", so one such seeded holding keeps the portfolio non-flat forever
    and the strategy makes zero trades for the entire run — while the run
    still succeeds and reports a clean equity curve.

    Needs no market data, so callers that can check early (the API's
    ``_to_seeded``) do it synchronously; ``_validate_holdings`` re-checks so
    the invariant holds for every caller of ``run_single_backtest``,
    including the CLI's ``--holdings`` CSV path.
    """
    if opened_on > start:
        raise ValueError(
            f"holding {index}: opened_on {opened_on.isoformat()} is after the backtest "
            f"window start {start.isoformat()}; a starting holding must already be held"
        )


def _validate_holdings(prepared: PreparedMarket, holdings: list[SeededHolding]) -> None:
    """Reject a starting holding that can't legally seed this window.

    Three independent checks, all required:
      1. opened_on no later than the window start (``validate_opened_on``);
         needs no market data, so it runs first.
      2. universe membership — the asset must appear somewhere in [start, end]
         (``prepared.asset_index``); NativeBacktest itself also enforces this
         (mapping to a dense C++ asset id), but checking here gives an error
         before any C++ boundary crossing.
      3. a mark on the window's first trading day — marks_on carries forward
         from a PRIOR print, never backward from a later one, so an asset
         that first prints after day 0 has no legitimate day-0 value to seed
         with, even though it's a valid in-universe tradeable asset overall.
    """
    if not holdings:
        return
    for i, h in enumerate(holdings, start=1):
        validate_opened_on(i, h.opened_on, prepared.start)
    if not prepared.market.days:
        raise ValueError(
            "cannot seed initial holdings: the backtest window contains no trading days"
        )
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
    config_extra: dict[str, Any] | None = None,
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

    config: dict[str, Any] = {
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
    }
    if config_extra:
        config.update(config_extra)

    recorded = record_run(
        root=root,
        command="backtest",
        strategy=result.strategy_name,
        config=config,
        results=result.summary,
        artifact_path=run_dir,
        warehouse=wh,
        run_id=run_id,
    )
    return BacktestRunResult(run_id=recorded, result=result, artifact_dir=run_dir)


@dataclass(frozen=True)
class PairedRunResult:
    """A strategy run and the buy-and-hold benchmark computed beside it."""

    primary: BacktestRunResult
    benchmark: BacktestRunResult | None


def run_with_benchmark(
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
) -> PairedRunResult:
    """Run `strategy_name`, then buy-and-hold-sealed over the IDENTICAL
    starting state (same window, cash, holdings, impact) so the two equity
    curves differ only by trading policy. The benchmark record carries
    ``benchmark_for: <primary run_id>``; that tag is the only linkage.

    Returns benchmark=None when the primary IS the benchmark — buy-and-hold
    over the sealed universe on default params — since pairing a run with a
    copy of itself buys nothing. A buy-and-hold run with a NON-default
    n_assets is a different allocation, so it still gets the benchmark beside
    it.
    """
    primary = run_single_backtest(
        root=root,
        strategy_name=strategy_name,
        params=params,
        cash=cash,
        holdings=holdings,
        start=start,
        end=end,
        impact=impact,
        warmup_days=warmup_days,
        kind=kind,
    )
    if (
        strategy_name == "buy-and-hold"
        and kind == "sealed"
        and resolve_params(strategy_name, params) == resolve_params(strategy_name, {})
    ):
        return PairedRunResult(primary=primary, benchmark=None)
    benchmark = run_single_backtest(
        root=root,
        strategy_name="buy-and-hold",
        params={},
        cash=cash,
        holdings=holdings,
        start=start,
        end=end,
        impact=impact,
        warmup_days=warmup_days,
        kind="sealed",
        config_extra={"benchmark_for": primary.run_id},
    )
    return PairedRunResult(primary=primary, benchmark=benchmark)
