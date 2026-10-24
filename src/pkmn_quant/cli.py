"""Typer CLI entry points."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

import polars as pl
import typer

from pkmn_quant.config import Paths
from pkmn_quant.data import tcgcsv
from pkmn_quant.data.ingest import ingest_range, refresh_products
from pkmn_quant.research.execution import execution_line

if TYPE_CHECKING:
    from pkmn_quant.data.warehouse import Warehouse
    from pkmn_quant.engine.native import SeededHolding
    from pkmn_quant.research.horizon import HorizonReport
    from pkmn_quant.research.runs import RunRecord

app = typer.Typer(no_args_is_help=True, help="Pokemon card quant toolkit.")

DEFAULT_SIGNALS_CASH = 10_000.0

# Holding-period buckets, in report order. These mirror research/horizon.py's
# private _BUCKET_ORDER verbatim; listed here so `pkmn horizon` can print
# EVERY bucket, including ones with no trades (HorizonReport only carries the
# occupied ones). tests/test_cli_horizon.py pins the two together so they
# cannot drift.
HORIZON_BUCKETS = ("<1m", "1-3m", "3-6m", ">6m")
# The bucket this command exists to produce: the owner wants a strategy that
# enters and exits within 1-3 months, so that median edge is the headline.
HORIZON_HEADLINE_BUCKET = "1-3m"
# Registry-key-safe names for the bucket labels above ("<"/">"/"-" out).
HORIZON_BUCKET_SLUGS = {"<1m": "lt_1m", "1-3m": "1_3m", "3-6m": "3_6m", ">6m": "gt_6m"}

portfolio_app = typer.Typer(no_args_is_help=True, help="Record and inspect real positions.")
app.add_typer(portfolio_app, name="portfolio")

runs_app = typer.Typer(no_args_is_help=True, help="Inspect the experiment run registry.")
app.add_typer(runs_app, name="runs")

wire_app = typer.Typer(
    no_args_is_help=True,
    help=(
        "Pokemon quote wire files (PKW1): daily tcgcsv quotes in whole cents. A "
        "replay builds modelled resting orders (synthetic, built from the day's "
        "tcgcsv quote) from them."
    ),
)
app.add_typer(wire_app, name="wire")


@runs_app.command("list")
def runs_list(
    strategy: str | None = typer.Option(None, help="Filter by strategy name."),
    root: Path = typer.Option(Path("."), help="Project root holding the data/ directory."),
) -> None:
    """Recorded research runs, newest first."""
    from pkmn_quant.research.runs import load_runs

    records = load_runs(root)
    if strategy:
        records = [r for r in records if r.strategy == strategy]
    if not records:
        typer.echo("no runs recorded")
        return
    for r in reversed(records):
        sha = (r.git_sha or "no-git")[:7] + ("*" if r.git_dirty else "")
        ret = r.results.get("total_return", r.results.get("stitched_total_return"))
        ret_s = f"{ret:+.4f}" if ret is not None else "   -   "
        typer.echo(f"{r.run_id}  {r.command:<11}  {r.strategy:<24}  total_return {ret_s}  {sha}")


@runs_app.command("show")
def runs_show(
    run_id: str = typer.Argument(..., help="Run id, or any unique prefix."),
    root: Path = typer.Option(Path("."), help="Project root holding the data/ directory."),
) -> None:
    """Full JSON record of one run."""
    import dataclasses

    from pkmn_quant.research.runs import load_runs

    matches = [r for r in load_runs(root) if r.run_id.startswith(run_id)]
    if not matches:
        raise typer.BadParameter(f"no run matching {run_id!r}; see `pkmn runs list`")
    if len(matches) > 1:
        ids = ", ".join(r.run_id for r in matches)
        raise typer.BadParameter(f"ambiguous run id {run_id!r}: matches {ids}")
    typer.echo(json.dumps(dataclasses.asdict(matches[0]), indent=2, sort_keys=True))


def _portfolio_deps(root: Path, paper: bool = False) -> tuple[Warehouse, pl.DataFrame, Path]:
    """(warehouse, products, ledger file) — shared by the portfolio subcommands.

    Pass paper=True to route to the paper ledger (data/portfolio/paper.jsonl).
    """
    from pkmn_quant.data.warehouse import Warehouse
    from pkmn_quant.live.ledger import ledger_path

    paths = Paths(root=root)
    warehouse = Warehouse(paths)
    if not paths.products.exists():
        raise typer.BadParameter(f"no warehouse at {root}; run 'pkmn ingest' first")
    return warehouse, warehouse.load_products(), ledger_path(root, paper=paper)


def _append_or_die(path: Path, event: dict[str, object], products: pl.DataFrame) -> None:
    from pkmn_quant.live.ledger import LedgerError, append_event

    try:
        append_event(path, event, products)
    except LedgerError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"recorded: {json.dumps(event)}")


@portfolio_app.command()
def deposit(
    amount: float = typer.Option(..., help="Cash added to the portfolio."),
    date: str | None = typer.Option(None, help="Event date (YYYY-MM-DD); default today."),
    paper: bool = typer.Option(False, "--paper", help="Use the paper ledger."),
    root: Path = typer.Option(Path("."), help="Project root holding the data/ directory."),
) -> None:
    """Record a cash deposit."""
    _, products, path = _portfolio_deps(root, paper=paper)
    try:
        day = dt.date.fromisoformat(date).isoformat() if date else dt.date.today().isoformat()
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    _append_or_die(path, {"date": day, "kind": "deposit", "amount": amount}, products)


@portfolio_app.command()
def withdraw(
    amount: float = typer.Option(..., help="Cash removed from the portfolio."),
    date: str | None = typer.Option(None, help="Event date (YYYY-MM-DD); default today."),
    paper: bool = typer.Option(False, "--paper", help="Use the paper ledger."),
    root: Path = typer.Option(Path("."), help="Project root holding the data/ directory."),
) -> None:
    """Record a cash withdrawal."""
    _, products, path = _portfolio_deps(root, paper=paper)
    try:
        day = dt.date.fromisoformat(date).isoformat() if date else dt.date.today().isoformat()
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    _append_or_die(path, {"date": day, "kind": "withdraw", "amount": amount}, products)


def _trade(
    kind: str,
    product_id: int,
    sub_type: str,
    qty: int,
    price: float,
    fees: float,
    date: str | None,
    root: Path,
    paper: bool = False,
) -> None:
    _, products, path = _portfolio_deps(root, paper=paper)
    try:
        day = dt.date.fromisoformat(date).isoformat() if date else dt.date.today().isoformat()
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    _append_or_die(
        path,
        {
            "date": day,
            "kind": kind,
            "product_id": product_id,
            "sub_type": sub_type,
            "qty": qty,
            "price": price,
            "fees": fees,
        },
        products,
    )


@portfolio_app.command()
def buy(
    product_id: int = typer.Option(..., help="TCGplayer product id (see signals output)."),
    sub_type: str = typer.Option("Normal", help="Printing sub-type, e.g. Normal/Holofoil."),
    qty: int = typer.Option(..., help="Units bought."),
    price: float = typer.Option(..., help="Per-unit price actually paid."),
    fees: float = typer.Option(0.0, help="Total non-price cost (shipping etc.)."),
    date: str | None = typer.Option(None, help="Trade date (YYYY-MM-DD); default today."),
    paper: bool = typer.Option(False, "--paper", help="Use the paper ledger."),
    root: Path = typer.Option(Path("."), help="Project root holding the data/ directory."),
) -> None:
    """Record a real purchase."""
    _trade("buy", product_id, sub_type, qty, price, fees, date, root, paper=paper)


@portfolio_app.command()
def sell(
    product_id: int = typer.Option(..., help="TCGplayer product id."),
    sub_type: str = typer.Option("Normal", help="Printing sub-type."),
    qty: int = typer.Option(..., help="Units sold."),
    price: float = typer.Option(..., help="Per-unit sale price."),
    fees: float = typer.Option(0.0, help="Total fees + shipping kept by the marketplace."),
    date: str | None = typer.Option(None, help="Trade date (YYYY-MM-DD); default today."),
    paper: bool = typer.Option(False, "--paper", help="Use the paper ledger."),
    root: Path = typer.Option(Path("."), help="Project root holding the data/ directory."),
) -> None:
    """Record a real sale."""
    _trade("sell", product_id, sub_type, qty, price, fees, date, root, paper=paper)


@portfolio_app.command()
def show(
    paper: bool = typer.Option(False, "--paper", help="Use the paper ledger."),
    root: Path = typer.Option(Path("."), help="Project root holding the data/ directory."),
) -> None:
    """Positions, cash, and P&L valued at the latest warehouse marks."""
    from pkmn_quant.engine.data import MarketData
    from pkmn_quant.live.ledger import LedgerError, load_portfolio, make_snapshot
    from pkmn_quant.live.signals import DEFAULT_WARMUP_DAYS

    warehouse, products, path = _portfolio_deps(root, paper=paper)
    try:
        pf = load_portfolio(path, products)
        if not pf.positions and pf.cash == 0.0 and pf.realized_pnl == 0.0:
            typer.echo("portfolio is empty — record a deposit first")
            return
        days = warehouse.stored_days()
        if not days:
            raise LedgerError("warehouse has no price data; run `pkmn ingest` first")
        latest = days[-1]
        market = MarketData.from_warehouse(
            warehouse, latest, latest, warmup_days=DEFAULT_WARMUP_DAYS
        )
        names = {
            int(r["product_id"]): str(r["name"])
            for r in products.select("product_id", "name").iter_rows(named=True)
        }
        snap = make_snapshot(pf, market.marks_on(latest), names)
    except LedgerError as exc:
        raise typer.BadParameter(str(exc)) from exc

    typer.echo(f"as of {latest}")
    for r in snap.positions:
        typer.echo(
            f"{r.product_id:>8}  {r.name} ({r.sub_type})  x{r.quantity}"
            f"  avg ${r.avg_cost:.2f}  mark ${r.mark:.2f}"
            f"  unrealized ${r.unrealized_pnl:+.2f}"
        )
    typer.echo(f"cash: ${snap.cash:.2f}")
    typer.echo(f"realized P&L: ${snap.realized_pnl:+.2f}")
    typer.echo(f"equity: ${snap.equity:.2f}")


@app.command()
def ingest(
    start: str = typer.Option(..., help="First date to ingest (YYYY-MM-DD)."),
    end: str = typer.Option(..., help="Last date to ingest (YYYY-MM-DD)."),
    root: Path = typer.Option(Path("."), help="Project root holding the data/ directory."),
) -> None:
    """Download tcgcsv daily archives and load them into the warehouse."""
    try:
        start_date = dt.date.fromisoformat(start)
        end_date = dt.date.fromisoformat(end)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    report = ingest_range(Paths(root=root), start_date, end_date)
    for s in report.days:
        typer.echo(f"{s.day}: {s.rows_clean} clean rows, {s.rows_quarantined} quarantined")
    if not report.days:
        typer.echo("Nothing to do - all days already ingested.")
    if report.catalog_failures:
        typer.echo(
            f"warning: catalog refresh: {len(report.catalog_failures)} group(s) failed to "
            "fetch; their previously catalogued rows were kept",
            err=True,
        )


@wire_app.command("encode")
def wire_encode(
    start: str = typer.Option(..., help="First day (YYYY-MM-DD)."),
    end: str = typer.Option(..., help="Last day (YYYY-MM-DD), inclusive."),
    out: Path = typer.Option(..., help="Output file, e.g. data/wire/<start>_<end>.pkw."),
    root: Path = typer.Option(Path("."), help="Project root holding the data/ directory."),
) -> None:
    """Encode the warehouse's quotes over [start, end] to a wire file."""
    import time

    from pkmn_quant.data.warehouse import Warehouse
    from pkmn_quant.wire import encode_window

    try:
        start_date = dt.date.fromisoformat(start)
        end_date = dt.date.fromisoformat(end)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    out.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    try:
        n = encode_window(Warehouse(Paths(root=root)), start_date, end_date, out)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    seconds = time.perf_counter() - t0
    typer.echo(f"wrote {out}")
    typer.echo(f"messages: {n}")
    typer.echo(f"bytes: {out.stat().st_size}")
    typer.echo(f"seconds: {seconds:.3f}")


@app.command("refresh-products")
def refresh_products_cmd(
    root: Path = typer.Option(Path("."), help="Project root holding the data/ directory."),
) -> None:
    """Re-fetch the product catalog for every set, without ingesting prices.

    Names come from every tcgcsv group, not just the price-tracked ones:
    TCGplayer re-files products between groups, so a product priced under a
    tracked set can end up listed under an untracked promo group. A product
    with no catalog row has no rarity, so it is tagged kind "other" and is
    invisible to every kind-filtered strategy.

    The fetched catalog is merged into the stored one (fresh rows win), so a
    group that fails to fetch keeps its previous products rather than
    vanishing; the number of failed groups is reported.
    """
    from pkmn_quant.data.warehouse import Warehouse

    with tcgcsv.make_client() as client:
        groups = tcgcsv.fetch_groups(client)
        typer.echo(f"fetching product catalog for {len(groups)} sets...")
        refresh = refresh_products(client, Warehouse(Paths(root=root)), groups)
    typer.echo(f"wrote {refresh.rows} products")
    if refresh.failed_groups:
        typer.echo(
            f"warning: {len(refresh.failed_groups)} of {len(groups)} groups failed to fetch "
            f"({', '.join(str(g) for g in refresh.failed_groups)}); "
            "their previously catalogued rows were kept",
            err=True,
        )


def _read_holdings_csv(path: Path) -> list[SeededHolding]:
    import csv

    from pkmn_quant.engine.native import SeededHolding
    from pkmn_quant.engine.portfolio import Asset

    out: list[SeededHolding] = []
    with path.open() as fh:
        for i, r in enumerate(csv.DictReader(fh), start=1):
            try:
                out.append(
                    SeededHolding(
                        asset=Asset(product_id=int(r["product_id"]), sub_type=r["sub_type"]),
                        quantity=int(r["quantity"]),
                        avg_cost=float(r["avg_cost"]),
                        opened_on=dt.date.fromisoformat(r["opened_on"]),
                    )
                )
            except KeyError as exc:
                raise typer.BadParameter(f"holdings row {i}: missing column {exc}") from exc
            except ValueError as exc:
                raise typer.BadParameter(f"holdings row {i}: {exc}") from exc
    return out


def _execution_line(mode: str) -> str:
    return execution_line(mode)


@app.command()
def backtest(
    start: str = typer.Option(..., help="Backtest start date (YYYY-MM-DD)."),
    end: str = typer.Option(..., help="Backtest end date (YYYY-MM-DD)."),
    strategy: str = typer.Option(
        "buy-and-hold", help="Strategy name (see registry) or buy-and-hold."
    ),
    param: list[str] = typer.Option(
        [], "--param", help="Hyperparameter override k=v (repeatable)."
    ),
    holdings: Path | None = typer.Option(
        None,
        exists=True,
        help="CSV of starting holdings: product_id,sub_type,quantity,avg_cost,opened_on.",
    ),
    cash: float = typer.Option(10_000.0, help="Initial cash."),
    kind: str = typer.Option("sealed", help="Universe for buy-and-hold: sealed|single."),
    impact: bool = typer.Option(
        True,
        "--impact/--no-impact",
        help=(
            "Market impact on fills (default on), priced per --execution. "
            "--no-impact is flat cost and overrides --execution: the run is "
            "recorded as formula."
        ),
    ),
    execution: str = typer.Option(
        "book",
        help=(
            "book (default): fills walk modelled resting orders (synthetic, built "
            "from the day's tcgcsv quote), whole-cent levels. formula: the "
            "continuous walk-the-spread charge. --no-impact overrides both."
        ),
    ),
    root: Path = typer.Option(Path("."), help="Project root holding the data/ directory."),
) -> None:
    """Run a single backtest of any registered strategy with cash + holdings."""
    from pkmn_quant.research.backtest_run import effective_execution, run_single_backtest

    try:
        start_date = dt.date.fromisoformat(start)
        end_date = dt.date.fromisoformat(end)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if kind not in ("sealed", "single"):
        raise typer.BadParameter(f"unknown kind {kind!r}; choose sealed or single")

    params: dict[str, str | float | int] = {}
    for item in param:
        if "=" not in item:
            raise typer.BadParameter(f"--param must be k=v, got {item!r}")
        k, v = item.split("=", 1)
        params[k.strip()] = v.strip()  # resolve_params coerces per ParamSpec.kind

    seeded: list[SeededHolding] = []
    if holdings is not None:
        seeded = _read_holdings_csv(holdings)

    try:
        out = run_single_backtest(
            root=root,
            strategy_name=strategy,
            params=params,
            cash=cash,
            holdings=seeded,
            start=start_date,
            end=end_date,
            impact=impact,
            execution=execution,
            kind=kind,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    result = out.result
    if out.run_id is not None:
        typer.echo(f"run recorded: {out.run_id}")
    typer.echo(f"strategy: {result.strategy_name}  ({len(result.fills)} fills)")
    typer.echo(_execution_line(effective_execution(execution, impact)))
    for key, value in result.summary.items():
        typer.echo(f"{key}: {value:.4f}")
    typer.echo(f"results written to {out.artifact_dir}")


@app.command()
def walkforward(
    strategy: str = typer.Option(..., help="Strategy name: see pkmn_quant.research.registry."),
    start: str = typer.Option(..., help="Range start (YYYY-MM-DD)."),
    end: str = typer.Option(..., help="Range end (YYYY-MM-DD)."),
    is_days: int = typer.Option(180, help="In-sample window length in days."),
    oos_days: int = typer.Option(60, help="Out-of-sample window length in days."),
    trials: int = typer.Option(25, help="Optuna trials per fold."),
    seed: int = typer.Option(42, help="Sampler seed for reproducibility."),
    cash: float = typer.Option(10_000.0, help="Initial cash per fold."),
    warmup_days: int = typer.Option(
        120,
        help="History days loaded before each window for signal lookbacks (observe-only).",
    ),
    objective_metric: str = typer.Option(
        "total_return", help="Metric optuna maximizes in-sample; see VALID_OBJECTIVE_METRICS."
    ),
    impact: bool = typer.Option(
        True,
        "--impact/--no-impact",
        help=(
            "Market impact on fills (default on), priced per --execution. "
            "--no-impact is flat cost and overrides --execution: the run is "
            "recorded as formula."
        ),
    ),
    engine: str = typer.Option(
        "cpp",
        help="Backtest engine: cpp (native, default) or python (reference).",
    ),
    workers: int = typer.Option(
        0,
        help="Fold-level parallelism: 0 = auto (min(folds, cores)), 1 = serial, N = N threads.",
    ),
    execution: str = typer.Option(
        "book",
        help=(
            "book (default): fills walk modelled resting orders (synthetic, built "
            "from the day's tcgcsv quote), whole-cent levels. formula: the "
            "continuous walk-the-spread charge. --no-impact overrides both."
        ),
    ),
    root: Path = typer.Option(Path("."), help="Project root holding the data/ directory."),
) -> None:
    """Walk-forward analysis: optimize in-sample, evaluate out-of-sample."""
    from collections.abc import Callable

    from pkmn_quant.data.warehouse import Warehouse
    from pkmn_quant.engine.costs import CostModel
    from pkmn_quant.research.artifacts import write_walkforward_json
    from pkmn_quant.research.backtest_run import effective_execution
    from pkmn_quant.research.folds import Fold
    from pkmn_quant.research.registry import REGISTRY
    from pkmn_quant.research.report import render_markdown
    from pkmn_quant.research.search import Params, SearchSpec, optimize_params
    from pkmn_quant.research.walkforward import (
        VALID_OBJECTIVE_METRICS,
        resolve_workers,
        run_walkforward,
    )

    entry = REGISTRY.get(strategy)
    if entry is None:
        raise typer.BadParameter(f"unknown strategy {strategy!r}; known: {sorted(REGISTRY)}")
    if objective_metric not in VALID_OBJECTIVE_METRICS:
        raise typer.BadParameter(
            f"unknown objective metric {objective_metric!r};"
            f" choose from {sorted(VALID_OBJECTIVE_METRICS)}"
        )
    if engine not in ("python", "cpp"):
        raise typer.BadParameter(f"unknown engine {engine!r}; choose python or cpp")
    if workers < 0:
        raise typer.BadParameter(f"workers must be >= 0, got {workers}")
    try:
        mode = effective_execution(execution, impact)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if engine == "python" and mode == "book":
        raise typer.BadParameter(
            "--execution book needs the cpp engine; "
            "the Python engine is the frozen formula reference"
        )
    try:
        start_date = dt.date.fromisoformat(start)
        end_date = dt.date.fromisoformat(end)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    # Bind entry after None check to satisfy mypy
    entry_checked = entry

    def optimizer(fold: Fold, evaluate: Callable[[Params], float]) -> Params:
        spec = SearchSpec(space=entry_checked.space, n_trials=trials, seed=seed)
        return optimize_params(spec, evaluate)

    wh = Warehouse(Paths(root=root))
    cm = CostModel(impact_enabled=impact)
    result = run_walkforward(
        warehouse=wh,
        strategy_factory=entry_checked.factory,
        optimizer=optimizer,
        cost_model=cm,
        start=start_date,
        end=end_date,
        is_days=is_days,
        oos_days=oos_days,
        initial_cash=cash,
        objective_metric=objective_metric,
        warmup_days=warmup_days,
        engine=engine,
        strategy_name=strategy,
        workers=workers,
        execution=mode,
    )

    from pkmn_quant.research.stats import bootstrap_ci, daily_returns_from_curve

    daily = daily_returns_from_curve(result.stitched_curve)
    stitched_ci = bootstrap_ci(daily, "total_return") if daily.size >= 2 else None

    # Book runs get a "-book" suffix so a book and a formula walk-forward over
    # the same window sit side by side instead of overwriting each other;
    # formula keeps the legacy name, so every existing artifact stays valid.
    suffix = "-book" if mode == "book" else ""
    run_dir = root / "data" / "results" / f"wf-{strategy}-{start}-{end}{suffix}"
    if run_dir.exists():
        typer.echo(f"warning: overwriting existing results in {run_dir}", err=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    result.stitched_curve.write_parquet(run_dir / "stitched_equity.parquet")
    (run_dir / "report.md").write_text(
        render_markdown(result, strategy_name=strategy, ci=stitched_ci, execution=mode)
    )
    write_walkforward_json(run_dir, result, strategy_name=strategy, ci=stitched_ci, execution=mode)

    from pkmn_quant.research.runs import record_run

    run_id = record_run(
        root=root,
        command="walkforward",
        strategy=strategy,
        config={
            "command": "walkforward",
            "strategy": strategy,
            "start": start,
            "end": end,
            "is_days": is_days,
            "oos_days": oos_days,
            "trials": trials,
            "seed": seed,
            "cash": cash,
            "warmup_days": warmup_days,
            "objective_metric": objective_metric,
            "cost_model": cm.as_dict(),
            "engine": engine,
            "execution": mode,
        },
        results=result.summary,
        artifact_path=run_dir,
        warehouse=wh,
        runtime={
            "workers": workers,
            # len(result.folds) == n_folds in both run_walkforward branches
            # (one FoldResult per fold), so this equals the run's own resolve.
            "workers_resolved": resolve_workers(workers, len(result.folds)),
        },
    )
    if run_id is not None:
        typer.echo(f"run recorded: {run_id}")

    typer.echo(f"strategy: {strategy}  folds: {len(result.folds)}")
    typer.echo(_execution_line(mode))
    for key, value in result.summary.items():
        typer.echo(f"{key}: {value:.4f}")
    typer.echo(f"report written to {run_dir / 'report.md'}")


@app.command()
def signals(
    strategy: str = typer.Option(..., help="Strategy name: see pkmn_quant.research.registry."),
    cash: float | None = typer.Option(
        None, "--cash", help="Hypothetical cash for position sizing (default 10000)."
    ),
    portfolio_flag: bool = typer.Option(
        False, "--portfolio", help="Run against the real ledger (positions + cash)."
    ),
    paper: bool = typer.Option(False, "--paper", help="Use the paper ledger."),
    warmup_days: int = typer.Option(
        365, help="History days loaded before the latest date for signal lookbacks."
    ),
    root: Path = typer.Option(Path("."), help="Project root holding the data/ directory."),
) -> None:
    """Run a strategy in live mode against the latest ingested prices.

    Live and paper pricing use the formula cost model, not book execution;
    research runs default to book, so a recommendation's quoted outlay can
    differ from what a book backtest would have charged.
    """
    from pkmn_quant.data.warehouse import Warehouse
    from pkmn_quant.live.ledger import LedgerError, load_portfolio
    from pkmn_quant.live.report import render_signals_markdown, signals_to_json
    from pkmn_quant.live.signals import SignalsError, generate_signals

    # --paper implies portfolio mode (paper ledger holds positions + cash)
    portfolio_flag = portfolio_flag or paper
    if portfolio_flag and cash is not None:
        raise typer.BadParameter("--cash and --portfolio/--paper are mutually exclusive")
    results_dir = root / "data" / "results"
    try:
        if portfolio_flag:
            # _portfolio_deps guards against a missing warehouse and returns
            # (warehouse, products, ledger_path); use it so missing-warehouse
            # gets a clean BadParameter instead of a raw FileNotFoundError.
            warehouse, products, lpath = _portfolio_deps(root, paper=paper)
            pf = load_portfolio(lpath, products)
        else:
            warehouse = Warehouse(Paths(root=root))
            pf = None
        # None when --portfolio so generate_signals' exactly-one check isn't tripped
        # by the default cash value falling through; default applied only in cash mode.
        resolved_cash = (
            None if portfolio_flag else (cash if cash is not None else DEFAULT_SIGNALS_CASH)
        )
        report = generate_signals(
            warehouse=warehouse,
            strategy_name=strategy,
            results_dir=results_dir,
            cash=resolved_cash,
            portfolio=pf,
            warmup_days=warmup_days,
            paper=paper,
        )
    except (SignalsError, LedgerError) as exc:
        raise typer.BadParameter(str(exc)) from exc

    markdown = render_signals_markdown(report)
    paper_suffix = "-paper" if paper else ""
    out_dir = results_dir / f"signals-{strategy}-{report.as_of.isoformat()}{paper_suffix}"
    if out_dir.exists():
        typer.echo(f"warning: overwriting existing results in {out_dir}", err=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "signals.md").write_text(markdown)
    (out_dir / "signals.json").write_text(signals_to_json(report))

    typer.echo(markdown)
    typer.echo(f"artifacts written to {out_dir}", err=True)


@app.command()
def daily(
    strategy: str = typer.Option("sealed-accumulation", help="Strategy to run against the ledger."),
    skip_ingest: bool = typer.Option(
        False, "--skip-ingest", help="Skip fetching new price days (tests/offline)."
    ),
    paper: bool = typer.Option(False, "--paper", help="Use the paper ledger."),
    impact: bool = typer.Option(
        True,
        "--impact/--no-impact",
        help="Walk-the-spread market impact on paper fills.",
    ),
    root: Path = typer.Option(Path("."), help="Project root holding the data/ directory."),
) -> None:
    """The morning loop: ingest missing days, run signals against the ledger,
    write artifacts, notify when actionable. Designed for launchd/cron.

    Paper mode (--paper): routes all ledger reads/writes to data/portfolio/paper.jsonl
    and auto-records the day's recommended fills using the engine CostModel so the
    strategy trades fake money through the identical pipeline. Every output surface is
    labeled PAPER.  The output directory is ``daily-{date}-paper`` (real runs use
    ``daily-{date}``), and notifications are tagged "(paper)".

    --impact/--no-impact (default: on) toggles walk-the-spread market impact
    when pricing paper fills, matching the backtester's CostModel flag.

    Known optimism vs the backtester:
    - Fills use the carry-forward warehouse mark on the as_of date, not a
      same-day actual print.  On days a card didn't trade, the mark is stale
      and paper may fill at a price no real buyer could have obtained.
    - The backtester uses T+1 fills; paper fills are recorded same-day, so
      paper results will look slightly better than a true OOS simulation.
    """
    import json as _json

    from pkmn_quant.live import notify
    from pkmn_quant.live.ledger import LedgerError, load_portfolio
    from pkmn_quant.live.report import render_signals_markdown, signals_to_json
    from pkmn_quant.live.signals import SignalsError, generate_signals

    today = dt.date.today()
    dir_suffix = "-paper" if paper else ""
    out_dir = root / "data" / "results" / f"daily-{today.isoformat()}{dir_suffix}"
    out_dir.mkdir(parents=True, exist_ok=True)

    notify_title_base = "pkmn daily (paper)" if paper else "pkmn daily"
    catalog_failures: tuple[int, ...] = ()

    def finish(
        status: str, error: str | None, n_buys: int, n_sells: int, as_of: str | None
    ) -> None:
        (out_dir / "daily.json").write_text(
            _json.dumps(
                {
                    "date": today.isoformat(),
                    "strategy": strategy,
                    "status": status,
                    "error": error,
                    "n_buys": n_buys,
                    "n_sells": n_sells,
                    "as_of": as_of,
                    "paper": paper,
                    # Groups whose catalog fetch failed this run. Their previous
                    # rows were kept (ingest.refresh_products merges), but an
                    # unattended run must leave a durable trace of the degradation.
                    "catalog_failures": len(catalog_failures),
                },
                indent=2,
            )
            + "\n"
        )

    def _fail(error: str, keep_artifacts: bool = False) -> None:
        """Write error daily.json, optionally remove stale signal artifacts, notify, exit 1.

        Pass keep_artifacts=True when the signals.md/signals.json are still
        valid (e.g. all-or-nothing ledger write failed after artifacts were
        written — no fills were recorded so the artifacts are consistent).
        """
        finish("error", error, 0, 0, None)
        if not keep_artifacts:
            for stale in ("signals.md", "signals.json"):
                (out_dir / stale).unlink(missing_ok=True)
        notify.send_notification(f"{notify_title_base} FAILED", error)
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(1)

    ingest_error: str | None = None
    try:
        # _portfolio_deps raises BadParameter when no warehouse exists; keep it
        # inside the protected block so that case produces error daily.json.
        warehouse, products, lpath = _portfolio_deps(root, paper=paper)

        if not skip_ingest:
            yesterday = today - dt.timedelta(days=1)
            days = warehouse.stored_days()
            if days and days[-1] < yesterday:
                try:
                    ingested = ingest_range(
                        Paths(root=root), days[-1] + dt.timedelta(days=1), yesterday
                    )
                    catalog_failures = ingested.catalog_failures
                except Exception as exc:  # scheduled run must never die silently
                    ingest_error = f"ingest failed: {exc}"

        pf = load_portfolio(lpath, products)
        report = generate_signals(
            warehouse=warehouse,
            strategy_name=strategy,
            results_dir=root / "data" / "results",
            portfolio=pf,
            paper=paper,
        )
    except (SignalsError, LedgerError, typer.BadParameter) as exc:
        # Combine ingest error with signals/deps error when both occurred.
        error = f"{exc}; {ingest_error}" if ingest_error else str(exc)
        _fail(error)
        return  # unreachable; satisfies type checker after _fail raises Exit
    except Exception as exc:
        # Unexpected errors (polars ComputeError, OSError, strategy bugs, …)
        # must not die silently — wrap same finish/notify/exit path.
        error = f"{exc}; {ingest_error}" if ingest_error else str(exc)
        _fail(error)
        return  # unreachable

    (out_dir / "signals.md").write_text(render_signals_markdown(report))
    (out_dir / "signals.json").write_text(signals_to_json(report))
    n_buys = sum(1 for r in report.recommendations if r.action == "BUY")
    n_sells = sum(1 for r in report.recommendations if r.action == "SELL")

    # Paper mode: auto-record recommended fills through the pure planner
    # (live/paper.py) and one atomic append_events write. A mid-batch
    # validation failure leaves the ledger completely unchanged. In paper
    # mode n_buys/n_sells are re-counted from the recorded batch: the
    # planner clips to liquidity and affordability (sometimes to zero),
    # and daily.json must report what actually happened, not what the
    # strategy wished for.
    if paper and report.recommendations:
        from pkmn_quant.engine.costs import CostModel
        from pkmn_quant.live.ledger import append_events
        from pkmn_quant.live.paper import plan_paper_fills

        batch = plan_paper_fills(
            report.recommendations, pf.cash, today, CostModel(impact_enabled=impact)
        )
        if batch:
            try:
                append_events(lpath, batch, products)
            except LedgerError as exc:
                _fail(f"paper auto-record failed: {exc}", keep_artifacts=True)
                return  # unreachable
        n_buys = sum(1 for e in batch if e["kind"] == "buy")
        n_sells = sum(1 for e in batch if e["kind"] == "sell")

    finish(
        "error" if ingest_error else "ok", ingest_error, n_buys, n_sells, report.as_of.isoformat()
    )

    if n_buys + n_sells > 0:
        notify.send_notification(
            notify_title_base,
            f"{strategy}: {n_buys} buys, {n_sells} sells — see dashboard",
        )
    if catalog_failures:
        # Not fatal — refresh_products merges, so the failed groups kept their
        # previous rows — but an unattended degradation must not be silent.
        msg = (
            f"catalog refresh: {len(catalog_failures)} group(s) failed to fetch "
            f"({', '.join(str(g) for g in catalog_failures)}); previous rows kept"
        )
        notify.send_notification(f"{notify_title_base}: catalog degraded", msg)
        typer.echo(f"warning: {msg}", err=True)
    if ingest_error:
        notify.send_notification(f"{notify_title_base}: ingest problem", ingest_error)
        typer.echo(ingest_error, err=True)
        raise typer.Exit(1)
    typer.echo(f"daily run written to {out_dir}")


@app.command()
def evaluate(
    root: Path = typer.Option(Path("."), help="Project root containing data/results/."),
    benchmark: Path | None = typer.Option(
        None,
        help="Benchmark artifact dir containing equity.parquet; default: the "
        "newest buy-and-hold backtest in the run registry (never a directory glob).",
    ),
    n_boot: int = typer.Option(10_000, min=1, help="Bootstrap resamples."),
    block: float = typer.Option(10.0, help="Mean bootstrap block length, days."),
    seed: int = typer.Option(42, help="Bootstrap seed (results are deterministic)."),
    execution: str = typer.Option(
        "book",
        help=(
            "Execution mode to evaluate: book (default; modelled resting orders, "
            "synthetic, built from the day's tcgcsv quote) or formula. Only wf-* "
            "artifacts recorded in this mode are read (a missing key is formula), "
            "and the benchmark must be in it too."
        ),
    ),
) -> None:
    """Cross-strategy rigor: bootstrap CIs, deflated Sharpe, Reality Check.

    Reads every data/results/wf-*/ walk-forward artifact plus the
    buy-and-hold benchmark curve — resolved from the RUN REGISTRY, and
    required to span the strategies' window — aligns them on common dates,
    and writes an evaluate-<date>/ artifact with report.md + evaluate.json. The
    Reality Check answers "did ANY strategy beat the benchmark, given how
    many we tried"; deflated Sharpe corrects each Sharpe for that same
    selection. Recorded in the experiment registry like every other run.
    """
    import numpy as np

    from pkmn_quant.data.warehouse import Warehouse
    from pkmn_quant.engine.metrics import TRADING_DAYS_PER_YEAR
    from pkmn_quant.research.backtest_run import effective_execution
    from pkmn_quant.research.runs import load_runs, record_run
    from pkmn_quant.research.stats import (
        bootstrap_ci,
        daily_returns_from_curve,
        deflated_sharpe,
        whites_reality_check,
    )

    results_dir = root / "data" / "results"

    def fail(msg: str) -> None:
        typer.echo(f"error: {msg}", err=True)
        raise typer.Exit(1)

    try:
        execution = effective_execution(execution, True)
    except ValueError as exc:
        fail(str(exc))

    # -- discover strategy artifacts in this mode (longest curve wins per strategy) --
    # Book and formula curves are priced by different execution models and
    # never belong in one Reality Check, so only artifacts recorded in the
    # requested mode are read (missing key = formula, as every artifact before
    # Plan B was); the others are skipped, never mixed in.
    curves: dict[str, pl.DataFrame] = {}
    n_other_mode = 0
    for d in sorted(results_dir.glob("wf-*")):
        meta, parquet = d / "walkforward.json", d / "stitched_equity.parquet"
        if not (meta.exists() and parquet.exists()):
            continue
        meta_obj = json.loads(meta.read_text())
        if str(meta_obj.get("execution", "formula")) != execution:
            n_other_mode += 1
            continue
        name = str(meta_obj["strategy"])
        frame = pl.read_parquet(parquet)
        if frame.height < 2:
            typer.echo(f"skipping {d.name}: curve too short", err=True)
            continue
        if name in curves and curves[name].height >= frame.height:
            typer.echo(f"skipping {d.name}: shorter than another {name} artifact", err=True)
            continue
        curves[name] = frame
    if not curves:
        hint = (
            f" ({n_other_mode} artifact(s) in the other mode skipped; pass --execution "
            f"{'formula' if execution == 'book' else 'book'} to evaluate those)"
            if n_other_mode
            else ""
        )
        fail(f"no walk-forward artifacts in {execution} execution under {results_dir}/wf-*{hint}")
    if len(curves) < 2:
        fail(f"Reality Check needs >= 2 strategies, found {len(curves)}")

    # -- benchmark: registry only, exactly like `pkmn horizon` --
    # The directory glob this used to try first is GONE, deliberately. It
    # sorted `data/results/buy-and-hold-sealed-*` and took the longest curve,
    # so any hand-made directory outranked the registry; the one it matched
    # was a stale pre-swap artifact and this command published a Reality Check
    # p of 1.0000 where the correct value was 0.8038. See
    # _benchmark_candidates for why a registry record and a directory name are
    # not interchangeable — that filter also drops the paired sidecar
    # benchmarks tagged `benchmark_for`, which cover somebody else's window
    # (one on disk here spans 61 days) and which the old fallback happily
    # returned.
    # The bar must be priced under the strategies' execution mode too: auto
    # picks the newest in-mode candidate; an explicit directory is looked up in
    # the registry by artifact_path (no record = formula, the pre-Plan-B rule)
    # and refused on a mismatch.
    records = load_runs(root)
    if benchmark is None:
        candidates = _benchmark_candidates(records, execution)
        if not candidates:
            fail(
                f"no {execution}-execution buy-and-hold benchmark backtest in the run "
                "registry; run `pkmn backtest` to make one, or pass --benchmark <dir>"
            )
        benchmark = Path(candidates[0].artifact_path)
    else:
        target = benchmark.resolve()

        def recorded_dirs(r: RunRecord) -> set[Path]:
            # A relative artifact_path is resolved against --root, not only
            # cwd: record_run stores root / "data" / ..., and with the default
            # root "." that is a root-relative path, so `pkmn evaluate --root
            # /repo` run from elsewhere would otherwise miss the record and
            # fall back to "formula". The cwd reading is kept as well for a
            # record written under a non-"." relative root.
            p = Path(r.artifact_path)
            if p.is_absolute():
                return {p.resolve()}
            return {(root / p).resolve(), p.resolve()}

        matches = sorted(
            (r for r in records if target in recorded_dirs(r)),
            key=lambda r: r.recorded_at,
            reverse=True,
        )
        bench_mode = str(matches[0].config.get("execution", "formula")) if matches else "formula"
        if bench_mode != execution:
            fail(
                f"benchmark {benchmark} is a {bench_mode}-execution run but the walk-forward "
                f"artifacts are {execution}; book and formula curves are not comparable"
            )
    bench_frame = pl.read_parquet(benchmark / "equity.parquet")

    # -- the benchmark must SPAN the strategies' window --
    # Same guard as `pkmn horizon`. Without it a short benchmark silently
    # shrinks the comparison instead of being rejected: the intersection below
    # is taken over the benchmark too, so a 61-day buy-and-hold run (there is
    # a real one on disk) clears the >= 60 check and the Reality Check is
    # computed over 61 days of 660-day strategy curves, with no warning.
    strat_common: set[dt.date] = set.intersection(
        *(set(frame["date"].to_list()) for frame in curves.values())
    )
    if not strat_common:
        fail("the walk-forward artifacts share no dates with each other")
    bench_dates = bench_frame["date"].to_list()
    s_first, s_last = min(strat_common), max(strat_common)
    if not bench_dates or min(bench_dates) > s_first or max(bench_dates) < s_last:
        span = f"{min(bench_dates)} .. {max(bench_dates)}" if bench_dates else "empty"
        fail(
            f"benchmark {benchmark.name} covers {span}, which does not span the strategies' "
            f"window {s_first} .. {s_last}; every day outside it would be dropped from the "
            "comparison rather than reported. Pass --benchmark <dir> for a buy-and-hold run "
            "covering the whole window."
        )

    # -- align on common dates --
    common = strat_common & set(bench_dates)
    if len(common) < 60:
        fail(f"date overlap across artifacts is {len(common)} days; need >= 60")
    dates = sorted(common)

    def aligned_returns(frame: pl.DataFrame) -> np.ndarray:
        return daily_returns_from_curve(frame.filter(pl.col("date").is_in(dates)))

    bench_r = aligned_returns(bench_frame)
    strat_r = {name: aligned_returns(frame) for name, frame in sorted(curves.items())}

    # -- metrics --
    ann = float(np.sqrt(TRADING_DAYS_PER_YEAR))
    daily_sharpes = {
        name: (0.0 if r.std(ddof=1) == 0.0 else float(r.mean()) / float(r.std(ddof=1)))
        for name, r in strat_r.items()
    }
    zoo = list(daily_sharpes.values())
    per_strategy: dict[str, dict[str, object]] = {}
    for name, r in strat_r.items():
        ci = bootstrap_ci(r, "total_return", n_boot=n_boot, mean_block=block, seed=seed)
        dsr = deflated_sharpe(r, zoo)
        per_strategy[name] = {
            "total_return": ci.point,
            "ci": {"point": ci.point, "lo": ci.lo, "hi": ci.hi, "level": ci.level},
            "sharpe": daily_sharpes[name] * ann,
            "dsr": None if np.isnan(dsr) else dsr,
        }
    excess = np.vstack([strat_r[name] - bench_r for name in sorted(strat_r)])
    p_value = whites_reality_check(excess, n_boot=n_boot, mean_block=block, seed=seed)

    # -- artifact --
    # Book evaluates get a "-book" suffix (the same rule as the wf-...-book
    # artifacts) so a book and a formula evaluate on one day sit side by side;
    # sharing one directory let the second overwrite the first, leaving the
    # first run's registry record pointing at the other mode's numbers.
    # Formula keeps the legacy name, so existing artifacts stay valid.
    suffix = "-book" if execution == "book" else ""
    out_dir = results_dir / f"evaluate-{dt.date.today().isoformat()}{suffix}"
    if out_dir.exists():
        typer.echo(f"warning: overwriting existing results in {out_dir}", err=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "strategies": per_strategy,
        "reality_check_p": p_value,
        "benchmark": str(benchmark),
        "execution": execution,
        "n_days": len(dates),
        "start": str(dates[0]),
        "end": str(dates[-1]),
        "params": {"n_boot": n_boot, "mean_block": block, "seed": seed},
    }
    (out_dir / "evaluate.json").write_text(json.dumps(payload, indent=2, default=str))

    lines = [
        "# Cross-strategy rigor report",
        "",
        execution_line(execution),
        "",
        f"{len(strat_r)} strategies vs benchmark `{benchmark.name}`, "
        f"{len(dates)} aligned days ({dates[0]} .. {dates[-1]}).",
        "",
        "Sharpe (and therefore the CIs and deflated Sharpe built on these",
        "returns) inherit the mark-smoothing inflation documented in every",
        "walkforward report; treat all bands as optimistic.",
        "",
        "| strategy | OOS total return | 95% CI | Sharpe (ann.) | deflated Sharpe |",
        "|---|---|---|---|---|",
    ]
    for name, s in per_strategy.items():
        ci_d = s["ci"]
        assert isinstance(ci_d, dict)
        dsr_txt = "n/a (zero-variance returns)" if s["dsr"] is None else f"{s['dsr']:.3f}"
        lines.append(
            f"| {name} | {s['total_return']:.2%} "
            f"| [{ci_d['lo']:.2%}, {ci_d['hi']:.2%}] "
            f"| {s['sharpe']:.2f} | {dsr_txt} |"
        )
    lines += [
        "",
        f"**White's Reality Check** (best strategy vs benchmark, jointly over "
        f"{len(strat_r)} strategies): p = {p_value:.4f}",
        f"(stationary block bootstrap: n_boot={n_boot}, mean block {block:g}d, seed {seed})",
        "",
    ]
    (out_dir / "report.md").write_text("\n".join(lines))

    flat: dict[str, float] = {"reality_check_p": p_value}
    for name, s in per_strategy.items():
        flat[f"{name}_total_return"] = float(s["total_return"])  # type: ignore[arg-type]
        if s["dsr"] is not None:
            flat[f"{name}_dsr"] = float(s["dsr"])  # type: ignore[arg-type]
    run_id = record_run(
        root=root,
        command="evaluate",
        strategy=",".join(sorted(strat_r)),
        config={
            "command": "evaluate",
            "strategies": sorted(strat_r),
            "benchmark": str(benchmark),
            "execution": execution,
            "n_boot": n_boot,
            "mean_block": block,
            "seed": seed,
            "start": str(dates[0]),
            "end": str(dates[-1]),
        },
        results=flat,
        artifact_path=out_dir,
        warehouse=Warehouse(Paths(root=root)),
    )
    if run_id is not None:
        typer.echo(f"run recorded: {run_id}")
    typer.echo(execution_line(execution))
    typer.echo(f"Reality Check p = {p_value:.4f}")
    typer.echo(f"results written to {out_dir}")


def _benchmark_candidates(records: list[RunRecord], execution: str) -> list[RunRecord]:
    """Buy-and-hold benchmark runs in ``execution`` mode from the REGISTRY,
    newest first.

    Never a directory glob, and there is deliberately no glob fallback.
    `pkmn evaluate` used to auto-discover its benchmark by globbing
    `data/results/buy-and-hold-sealed-*` and taking the longest curve; the
    directory it matched was a stale pre-swap artifact, so a post-swap
    evaluation was scored against a pre-swap bar and a Reality Check p-value
    of 1.0000 was published where the correct value was 0.8038. A directory
    name carries no identity; a registry record carries the config, the git
    SHA and the data fingerprint of the run that produced the curve, which is
    what makes "is this benchmark comparable to that run?" answerable at all.
    That matters again right now: this branch changed buy-and-hold's
    allocation rule, so every buy-and-hold artifact produced before it is
    measured on a different rule and only the record says which.

    The filter is the same rule /api/runs (`api/data.py`) and the dashboard's
    `benchmark_runs()` already apply: a `backtest` run of a buy-and-hold
    strategy, minus the paired sidecar benchmarks tagged `benchmark_for`
    (those cover somebody else's, often very short, window).

    Plan B adds the mode filter: a record's ``config["execution"]`` (missing
    = formula, as every record before Plan B was) must equal ``execution``,
    so a book run is never scored against a formula bar or vice versa.
    """
    return sorted(
        (
            r
            for r in records
            if r.command == "backtest"
            and r.strategy.startswith("buy-and-hold")
            and not r.config.get("benchmark_for")
            and r.config.get("execution", "formula") == execution
            and (Path(r.artifact_path) / "equity.parquet").is_file()
        ),
        key=lambda r: r.recorded_at,
        reverse=True,
    )


def _horizon_headline(report: HorizonReport) -> str:
    """The one line this command exists to produce: the 1-3m median edge."""
    n = report.holding_buckets.get(HORIZON_HEADLINE_BUCKET, 0)
    if n == 0:
        return f"{HORIZON_HEADLINE_BUCKET} median edge: n/a (no round trips in this bucket)"
    edge = report.edge_by_bucket[HORIZON_HEADLINE_BUCKET]
    return f"{HORIZON_HEADLINE_BUCKET} median edge: {edge:+.2%} over {n} round trip(s)"


def _horizon_report_md(
    report: HorizonReport,
    run: RunRecord,
    bench: RunRecord,
    first_day: dt.date,
    last_day: dt.date,
) -> str:
    n_open = sum(1 for t in report.trips if t.still_open)
    lines = [
        "# Horizon-matched trade report",
        "",
        execution_line(str(run.config.get("execution", "formula"))),
        "",
        f"Backtest run `{run.run_id}` (`{run.strategy}`) vs benchmark run "
        f"`{bench.run_id}` (`{bench.strategy}`), {first_day} .. {last_day}.",
        "",
        f"Benchmark provenance: recorded {bench.recorded_at}, git "
        f"`{bench.git_sha or 'unknown'}`{'*' if bench.git_dirty else ''}. Buy-and-hold's",
        "allocation rule changed on this branch, so the strategy name alone does",
        "not identify the bar -- the sha does.",
        "",
    ]
    if bench.git_sha != run.git_sha:
        lines += [
            f"**Warning: the benchmark was produced at git `{bench.git_sha or 'unknown'}` "
            f"but this backtest at `{run.git_sha or 'unknown'}`.** Check the two curves",
            "were computed under the same buy-and-hold rule before trusting the edges.",
            "",
        ]
    lines += [
        "Every OTHER report in this project scores a strategy against one",
        "28-month buy-and-hold, which measures exposure time as much as skill.",
        "Here each round trip is scored against holding the sealed index over",
        "that trip's OWN entry and exit dates, so a six-week trade is compared",
        "with six weeks of the index. `edge` = trip return - index return over",
        "the same days.",
        "",
        f"**{_horizon_headline(report)}**",
        "",
        "| metric | value |",
        "|---|---|",
        f"| round trips | {len(report.trips)} |",
        f"| still open at {last_day} (closed at that day's mark, net of a "
        f"modelled exit) | {n_open} |",
        f"| median edge | {report.median_edge:+.2%} |",
        f"| mean edge | {report.mean_edge:+.2%} |",
        f"| win rate (edge > 0) | {report.win_rate:.1%} |",
        "",
        "## Holding period",
        "",
        "| bucket | round trips | median edge |",
        "|---|---|---|",
    ]
    for bucket in HORIZON_BUCKETS:
        count = report.holding_buckets.get(bucket, 0)
        edge = f"{report.edge_by_bucket[bucket]:+.2%}" if count else "n/a"
        lines += [f"| {bucket} | {count} | {edge} |"]
    lines += [
        "",
        "Caveats: marks on this data are smoothed (thin markets, carry-forward",
        "marks), so a still-open trip's return is a mark, not a realized exit.",
        "That exit IS charged, though -- fees, shipping and the sell-side walk",
        "through the run's own cost model -- because a closed trip's proceeds",
        "are already net and scoring open ones gross would hand them roughly 13",
        "free points. The modelled exit ignores the per-day liquidity cap, so a",
        "large lot is charged as one same-day sale.",
        "A per-trip edge is not a portfolio return either -- it ignores how much",
        "capital sat idle between trips.",
        "",
    ]
    return "\n".join(lines)


@app.command()
def horizon(
    run_id: str = typer.Argument(..., help="Backtest run id; see `pkmn runs list`."),
    benchmark: str | None = typer.Option(
        None,
        help="Benchmark run id (NOT a directory); default: newest buy-and-hold "
        "backtest in the run registry.",
    ),
    root: Path = typer.Option(Path("."), help="Project root holding the data/ directory."),
) -> None:
    """Per-trade edge against the sealed index over each trade's OWN window.

    Pairs a backtest's fills into FIFO round trips and scores each one against
    holding the sealed index between that trip's entry and exit dates. Answers
    "did this strategy beat the index on the trades it actually made", which a
    stitched return against a 28-month buy-and-hold does not. Writes
    data/results/horizon-<run-id>/{report.md,horizon.json} and records a
    registry entry like every other run.
    """
    from collections.abc import Callable

    from pkmn_quant.data.warehouse import Warehouse
    from pkmn_quant.engine.costs import CostModel
    from pkmn_quant.engine.data import MarketData
    from pkmn_quant.research.horizon import horizon_report
    from pkmn_quant.research.runs import load_runs, record_run

    def fail(msg: str) -> NoReturn:
        typer.echo(f"error: {msg}", err=True)
        raise typer.Exit(1)

    records = load_runs(root)
    by_id = {r.run_id: r for r in records}

    run = by_id.get(run_id)
    if run is None:
        fail(f"no run matching {run_id!r}; see `pkmn runs list`")
    if run.command != "backtest":
        fail(f"run {run_id} is a {run.command} run; horizon needs a backtest run (it pairs fills)")

    art = Path(run.artifact_path)
    for name in ("fills.parquet", "equity.parquet"):
        if not (art / name).is_file():
            fail(f"run {run_id} is missing {name} under {art}")
    fills = pl.read_parquet(art / "fills.parquet")
    if fills.height == 0:
        fail(f"run {run_id} has no fills; there is nothing to pair")
    equity = pl.read_parquet(art / "equity.parquet").sort("date")
    if equity.height == 0:
        fail(f"run {run_id} has an empty equity curve")
    first_day: dt.date = equity["date"][0]
    last_day: dt.date = equity["date"][-1]

    # The run's execution mode (missing = formula, every pre-Plan-B record).
    # The benchmark must share it: book and formula bars are priced by
    # different execution models.
    execution = str(run.config.get("execution", "formula"))

    # -- benchmark: registry only, see _benchmark_candidates --
    if benchmark is not None:
        bench = by_id.get(benchmark)
        if bench is None:
            fail(f"no run matching benchmark {benchmark!r}; see `pkmn runs list`")
        if not (Path(bench.artifact_path) / "equity.parquet").is_file():
            fail(f"benchmark run {benchmark} has no equity.parquet under {bench.artifact_path}")
        bench_mode = str(bench.config.get("execution", "formula"))
        if bench_mode != execution:
            fail(
                f"benchmark run {benchmark} is a {bench_mode}-execution run but run "
                f"{run_id} is {execution}; book and formula runs are not comparable"
            )
    else:
        candidates = _benchmark_candidates(records, execution)
        if not candidates:
            fail(
                f"no {execution}-execution buy-and-hold benchmark backtest in the run "
                "registry; run `pkmn backtest` to make one, or pass --benchmark <run-id> "
                "(a run id, never a directory)"
            )
        bench = candidates[0]

    index = pl.read_parquet(Path(bench.artifact_path) / "equity.parquet").sort("date")
    if index.height == 0:
        fail(f"benchmark run {bench.run_id} has an empty equity curve")
    index_first: dt.date = index["date"][0]
    index_last: dt.date = index["date"][-1]
    # A benchmark that does not SPAN the run's window silently corrupts every
    # trip: horizon.py's _index_at guards only the left edge, so past the
    # curve's end it carries the last available value forward and a trip that
    # ran four months past the benchmark's end is scored as if the index moved
    # 0% over those months. That is reachable on the DEFAULT path, not just
    # --benchmark: the newest buy-and-hold backtest may well be a two-month
    # ad-hoc run. Refuse rather than under-report the bar.
    if index_first > first_day or index_last < last_day:
        fail(
            f"benchmark run {bench.run_id} covers {index_first} .. {index_last}, which does "
            f"not span the run's window {first_day} .. {last_day}; every trip outside that "
            "range would be scored against a flat index. Pass --benchmark <run-id> for a "
            "buy-and-hold run covering the whole window."
        )
    if bench.git_sha != run.git_sha:
        typer.echo(
            f"warning: benchmark run {bench.run_id} was produced at git "
            f"{bench.git_sha or 'unknown'} but this backtest at {run.git_sha or 'unknown'}; "
            "buy-and-hold's allocation rule has changed on this branch, so check the two "
            "curves are comparable",
            err=True,
        )

    # -- final marks: the engine's own mark source (MarketData.marks_on), so a
    # still-open lot is closed at exactly the price the run's last equity point
    # was computed from, not at a separately re-derived price.
    wh = Warehouse(Paths(root=root))
    market = MarketData.from_warehouse(wh, first_day, last_day)
    marks_on_last = market.marks_on(last_day)
    final_marks = {(a.product_id, a.sub_type): v for a, v in marks_on_last.items()}
    # ...and last_day's cheapest listings, so the modelled exit on a still-open
    # lot carries sell-side impact and not only fees. Only assets that printed
    # on last_day have a quote; the rest simply pay no walk.
    final_lows = {
        (a.product_id, a.sub_type): q.low
        for a, q in market.quotes_on(last_day, list(marks_on_last)).items()
        if q.low is not None
    }
    # The run's own cost model: run_single_backtest builds CostModel(
    # impact_enabled=impact) and varies nothing else, and `impact` is in the
    # registry config. A still-open lot is scored net of a modelled exit
    # through it — closed trips already paid fees, shipping and impact, so
    # scoring open ones gross put them ~13 points ahead for free.
    run_costs = CostModel(impact_enabled=bool(run.config.get("impact", True)))
    # A book run's open lots exit through the modelled bid book (synthetic,
    # built from the day's tcgcsv quote), like its closed trips did; units
    # past the day's depth go on the ramp's extension (hypothetical exit only).
    exit_impact: Callable[[float, float | None, int], float] | None = None
    if execution == "book":
        from pkmn_quant.engine.native import book_sell_impact

        def book_exit(mark: float, low: float | None, qty: int) -> float:
            return book_sell_impact(run_costs, mark, low, qty)

        exit_impact = book_exit

    try:
        report = horizon_report(
            fills, index, final_marks, last_day, run_costs, final_lows, exit_impact=exit_impact
        )
    except ValueError as exc:
        fail(str(exc))

    out_dir = root / "data" / "results" / f"horizon-{run_id}"
    if out_dir.exists():
        typer.echo(f"warning: overwriting existing results in {out_dir}", err=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Which BUILD of buy-and-hold produced this bar. Task 1 of this branch
    # rewrote buy-and-hold's allocation rule, so "buy-and-hold-sealed" alone
    # does not identify a benchmark: a pre-Task-1 run is a valid most-recent
    # candidate measured under a different rule. The git sha + recorded_at +
    # both windows make that recoverable from the artifact instead of only
    # from the registry.
    bench_info: dict[str, Any] = {
        "benchmark_run_id": bench.run_id,
        "benchmark_strategy": bench.strategy,
        "benchmark_artifact": bench.artifact_path,
        "benchmark_git_sha": bench.git_sha,
        "benchmark_git_dirty": bench.git_dirty,
        "benchmark_recorded_at": bench.recorded_at,
        "benchmark_config_start": bench.config.get("start"),
        "benchmark_config_end": bench.config.get("end"),
        "benchmark_index_start": index_first.isoformat(),
        "benchmark_index_end": index_last.isoformat(),
    }

    payload: dict[str, Any] = {
        "backtest_run_id": run_id,
        "strategy": run.strategy,
        "git_sha": run.git_sha,
        "execution": execution,
        **bench_info,
        "start": first_day.isoformat(),
        "last_day": last_day.isoformat(),
        "n_trips": len(report.trips),
        "n_open": sum(1 for t in report.trips if t.still_open),
        "median_edge": report.median_edge,
        "mean_edge": report.mean_edge,
        "win_rate": report.win_rate,
        "holding_buckets": report.holding_buckets,
        "edge_by_bucket": report.edge_by_bucket,
        "trips": [
            {
                "product_id": t.product_id,
                "sub_type": t.sub_type,
                "entry": t.entry.isoformat(),
                "exit": t.exit.isoformat(),
                "holding_days": t.holding_days,
                "quantity": t.quantity,
                "strategy_return": t.strategy_return,
                "index_return": t.index_return,
                "edge": t.edge,
                "still_open": t.still_open,
            }
            for t in report.trips
        ],
    }
    (out_dir / "horizon.json").write_text(json.dumps(payload, indent=2, default=str))
    (out_dir / "report.md").write_text(_horizon_report_md(report, run, bench, first_day, last_day))

    flat: dict[str, float] = {
        "n_trips": float(len(report.trips)),
        "median_edge": report.median_edge,
        "mean_edge": report.mean_edge,
        "win_rate": report.win_rate,
    }
    for bucket, edge in report.edge_by_bucket.items():
        flat[f"median_edge_{HORIZON_BUCKET_SLUGS[bucket]}"] = edge
    recorded = record_run(
        root=root,
        command="horizon",
        strategy=run.strategy,
        config={
            "command": "horizon",
            "backtest_run_id": run_id,
            "execution": execution,
            **bench_info,
            "start": first_day.isoformat(),
            "end": last_day.isoformat(),
        },
        results=flat,
        artifact_path=out_dir,
        warehouse=wh,
    )
    if recorded is not None:
        typer.echo(f"run recorded: {recorded}")
    typer.echo(f"{len(report.trips)} round trips vs benchmark {bench.run_id} ({bench.strategy})")
    typer.echo(_execution_line(execution))
    typer.echo(_horizon_headline(report))
    typer.echo(f"median edge (all trips): {report.median_edge:+.2%}")
    typer.echo(f"results written to {out_dir}")


@app.command()
def version() -> None:
    """Print the pkmn-quant version."""
    from pkmn_quant import __version__

    typer.echo(f"pkmn-quant {__version__}")


if __name__ == "__main__":
    app()
