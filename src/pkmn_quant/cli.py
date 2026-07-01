"""Typer CLI entry points."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

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
def version() -> None:
    """Print the pkmn-quant version."""
    from pkmn_quant import __version__

    typer.echo(f"pkmn-quant {__version__}")


if __name__ == "__main__":
    app()
