from datetime import date, timedelta
from pathlib import Path

import polars as pl
from typer.testing import CliRunner

from pkmn_quant.cli import app
from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA
from pkmn_quant.data.warehouse import Warehouse
from tests.helpers import price_row


def seed(root: Path) -> None:
    w = Warehouse(Paths(root=root))
    start = date(2025, 1, 1)
    for i in range(121):
        d = start + timedelta(days=i)
        price = 200.0 if i < 30 else 100.0
        w.write_prices(d, pl.DataFrame([price_row(d, 1, price)], schema=PRICE_SCHEMA))
    w.write_products(
        pl.DataFrame(
            {
                "product_id": [1],
                "group_id": [1],
                "name": ["Crashed Box"],
                "rarity": [None],
                "kind": ["sealed"],
                "released_on": [start],
            }
        )
    )


def test_signals_cli_end_to_end(tmp_path: Path) -> None:
    seed(tmp_path)
    runner = CliRunner()
    wf = runner.invoke(
        app,
        [
            "walkforward",
            "--strategy",
            "sealed-accumulation",
            "--start",
            "2025-01-01",
            "--end",
            "2025-04-11",
            "--is-days",
            "30",
            "--oos-days",
            "30",
            "--trials",
            "2",
            "--cash",
            "1000",
            "--root",
            str(tmp_path),
        ],
    )
    assert wf.exit_code == 0, wf.output

    result = runner.invoke(
        app,
        [
            "signals",
            "--strategy",
            "sealed-accumulation",
            "--cash",
            "1000",
            "--root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "sealed-accumulation" in result.output
    out_dirs = [
        p for p in (tmp_path / "data" / "results").iterdir() if p.name.startswith("signals-")
    ]
    assert len(out_dirs) == 1
    assert out_dirs[0].name == "signals-sealed-accumulation-2025-05-01"  # latest seeded day
    assert (out_dirs[0] / "signals.md").exists()
    assert (out_dirs[0] / "signals.json").exists()


def test_signals_cli_without_walkforward_clean_error(tmp_path: Path) -> None:
    seed(tmp_path)
    result = CliRunner().invoke(
        app, ["signals", "--strategy", "sealed-accumulation", "--root", str(tmp_path)]
    )
    assert result.exit_code != 0
    assert "pkmn walkforward" in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)
