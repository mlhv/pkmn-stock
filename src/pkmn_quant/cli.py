"""Typer CLI entry points."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import typer

from pkmn_quant.config import Paths
from pkmn_quant.data.ingest import ingest_range

app = typer.Typer(no_args_is_help=True, help="Pokemon card quant toolkit.")


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
def version() -> None:
    """Print the pkmn-quant version."""
    from pkmn_quant import __version__

    typer.echo(f"pkmn-quant {__version__}")


if __name__ == "__main__":
    app()
