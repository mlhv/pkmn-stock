"""Typer CLI entry points."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl
import typer

from pkmn_quant.config import Paths
from pkmn_quant.data.ingest import ingest_range

if TYPE_CHECKING:
    from pkmn_quant.data.warehouse import Warehouse

app = typer.Typer(no_args_is_help=True, help="Pokemon card quant toolkit.")

DEFAULT_SIGNALS_CASH = 10_000.0

portfolio_app = typer.Typer(no_args_is_help=True, help="Record and inspect real positions.")
app.add_typer(portfolio_app, name="portfolio")


def _portfolio_deps(root: Path) -> tuple[Warehouse, pl.DataFrame, Path]:
    """(warehouse, products, ledger file) — shared by the portfolio subcommands."""
    from pkmn_quant.data.warehouse import Warehouse
    from pkmn_quant.live.ledger import ledger_path

    paths = Paths(root=root)
    warehouse = Warehouse(paths)
    if not paths.products.exists():
        raise typer.BadParameter(f"no warehouse at {root}; run 'pkmn ingest' first")
    return warehouse, warehouse.load_products(), ledger_path(root)


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
    root: Path = typer.Option(Path("."), help="Project root holding the data/ directory."),
) -> None:
    """Record a cash deposit."""
    _, products, path = _portfolio_deps(root)
    try:
        day = dt.date.fromisoformat(date).isoformat() if date else dt.date.today().isoformat()
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    _append_or_die(path, {"date": day, "kind": "deposit", "amount": amount}, products)


@portfolio_app.command()
def withdraw(
    amount: float = typer.Option(..., help="Cash removed from the portfolio."),
    date: str | None = typer.Option(None, help="Event date (YYYY-MM-DD); default today."),
    root: Path = typer.Option(Path("."), help="Project root holding the data/ directory."),
) -> None:
    """Record a cash withdrawal."""
    _, products, path = _portfolio_deps(root)
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
) -> None:
    _, products, path = _portfolio_deps(root)
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
    root: Path = typer.Option(Path("."), help="Project root holding the data/ directory."),
) -> None:
    """Record a real purchase."""
    _trade("buy", product_id, sub_type, qty, price, fees, date, root)


@portfolio_app.command()
def sell(
    product_id: int = typer.Option(..., help="TCGplayer product id."),
    sub_type: str = typer.Option("Normal", help="Printing sub-type."),
    qty: int = typer.Option(..., help="Units sold."),
    price: float = typer.Option(..., help="Per-unit sale price."),
    fees: float = typer.Option(0.0, help="Total fees + shipping kept by the marketplace."),
    date: str | None = typer.Option(None, help="Trade date (YYYY-MM-DD); default today."),
    root: Path = typer.Option(Path("."), help="Project root holding the data/ directory."),
) -> None:
    """Record a real sale."""
    _trade("sell", product_id, sub_type, qty, price, fees, date, root)


@portfolio_app.command()
def show(
    root: Path = typer.Option(Path("."), help="Project root holding the data/ directory."),
) -> None:
    """Positions, cash, and P&L valued at the latest warehouse marks."""
    from pkmn_quant.engine.data import MarketData
    from pkmn_quant.live.ledger import LedgerError, load_portfolio, make_snapshot
    from pkmn_quant.live.signals import DEFAULT_WARMUP_DAYS

    warehouse, products, path = _portfolio_deps(root)
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
    stats = ingest_range(Paths(root=root), start_date, end_date)
    for s in stats:
        typer.echo(f"{s.day}: {s.rows_clean} clean rows, {s.rows_quarantined} quarantined")
    if not stats:
        typer.echo("Nothing to do - all days already ingested.")


@app.command()
def backtest(
    start: str = typer.Option(..., help="Backtest start date (YYYY-MM-DD)."),
    end: str = typer.Option(..., help="Backtest end date (YYYY-MM-DD)."),
    cash: float = typer.Option(10_000.0, help="Initial cash."),
    kind: str = typer.Option("sealed", help="Universe for buy-and-hold: sealed|single."),
    root: Path = typer.Option(Path("."), help="Project root holding the data/ directory."),
) -> None:
    """Run the buy-and-hold benchmark backtest over the warehouse."""
    from pkmn_quant.data.warehouse import Warehouse
    from pkmn_quant.engine.backtest import Backtest
    from pkmn_quant.engine.costs import CostModel
    from pkmn_quant.strategies.buy_and_hold import BuyAndHold

    try:
        start_date = dt.date.fromisoformat(start)
        end_date = dt.date.fromisoformat(end)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    result = Backtest(
        warehouse=Warehouse(Paths(root=root)),
        strategy=BuyAndHold(kind=kind),
        cost_model=CostModel(),
        start=start_date,
        end=end_date,
        initial_cash=cash,
    ).run()

    run_dir = root / "data" / "results" / f"{result.strategy_name}-{start}-{end}"
    if run_dir.exists():
        typer.echo(f"warning: overwriting existing results in {run_dir}", err=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    result.equity_curve.write_parquet(run_dir / "equity.parquet")
    fills_df = pl.DataFrame(
        [
            {
                "day": f.day,
                "product_id": f.asset.product_id,
                "sub_type": f.asset.sub_type,
                "quantity": f.quantity,
                "price": f.price,
                "fees": f.fees,
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
        },
    )
    fills_df.write_parquet(run_dir / "fills.parquet")

    typer.echo(f"strategy: {result.strategy_name}  ({len(result.fills)} fills)")
    for key, value in result.summary.items():
        typer.echo(f"{key}: {value:.4f}")
    typer.echo(f"results written to {run_dir}")


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
    root: Path = typer.Option(Path("."), help="Project root holding the data/ directory."),
) -> None:
    """Walk-forward analysis: optimize in-sample, evaluate out-of-sample."""
    from collections.abc import Callable

    from pkmn_quant.data.warehouse import Warehouse
    from pkmn_quant.engine.costs import CostModel
    from pkmn_quant.research.artifacts import write_walkforward_json
    from pkmn_quant.research.folds import Fold
    from pkmn_quant.research.registry import REGISTRY
    from pkmn_quant.research.report import render_markdown
    from pkmn_quant.research.search import Params, SearchSpec, optimize_params
    from pkmn_quant.research.walkforward import VALID_OBJECTIVE_METRICS, run_walkforward

    entry = REGISTRY.get(strategy)
    if entry is None:
        raise typer.BadParameter(f"unknown strategy {strategy!r}; known: {sorted(REGISTRY)}")
    if objective_metric not in VALID_OBJECTIVE_METRICS:
        raise typer.BadParameter(
            f"unknown objective metric {objective_metric!r};"
            f" choose from {sorted(VALID_OBJECTIVE_METRICS)}"
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

    result = run_walkforward(
        warehouse=Warehouse(Paths(root=root)),
        strategy_factory=entry_checked.factory,
        optimizer=optimizer,
        cost_model=CostModel(),
        start=start_date,
        end=end_date,
        is_days=is_days,
        oos_days=oos_days,
        initial_cash=cash,
        objective_metric=objective_metric,
        warmup_days=warmup_days,
    )

    run_dir = root / "data" / "results" / f"wf-{strategy}-{start}-{end}"
    if run_dir.exists():
        typer.echo(f"warning: overwriting existing results in {run_dir}", err=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    result.stitched_curve.write_parquet(run_dir / "stitched_equity.parquet")
    (run_dir / "report.md").write_text(render_markdown(result, strategy_name=strategy))
    write_walkforward_json(run_dir, result, strategy_name=strategy)

    typer.echo(f"strategy: {strategy}  folds: {len(result.folds)}")
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
    warmup_days: int = typer.Option(
        365, help="History days loaded before the latest date for signal lookbacks."
    ),
    root: Path = typer.Option(Path("."), help="Project root holding the data/ directory."),
) -> None:
    """Run a strategy in live mode against the latest ingested prices."""
    from pkmn_quant.data.warehouse import Warehouse
    from pkmn_quant.live.ledger import LedgerError, load_portfolio
    from pkmn_quant.live.report import render_signals_markdown, signals_to_json
    from pkmn_quant.live.signals import SignalsError, generate_signals

    if portfolio_flag and cash is not None:
        raise typer.BadParameter("--cash and --portfolio are mutually exclusive")
    results_dir = root / "data" / "results"
    try:
        if portfolio_flag:
            # _portfolio_deps guards against a missing warehouse and returns
            # (warehouse, products, ledger_path); use it so missing-warehouse
            # gets a clean BadParameter instead of a raw FileNotFoundError.
            warehouse, products, lpath = _portfolio_deps(root)
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
        )
    except (SignalsError, LedgerError) as exc:
        raise typer.BadParameter(str(exc)) from exc

    markdown = render_signals_markdown(report)
    out_dir = results_dir / f"signals-{strategy}-{report.as_of.isoformat()}"
    if out_dir.exists():
        typer.echo(f"warning: overwriting existing results in {out_dir}", err=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "signals.md").write_text(markdown)
    (out_dir / "signals.json").write_text(signals_to_json(report))

    typer.echo(markdown)
    typer.echo(f"artifacts written to {out_dir}", err=True)


@app.command()
def version() -> None:
    """Print the pkmn-quant version."""
    from pkmn_quant import __version__

    typer.echo(f"pkmn-quant {__version__}")


if __name__ == "__main__":
    app()
