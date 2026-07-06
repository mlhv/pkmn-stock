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
    for i in range(10):
        d = start + timedelta(days=i)
        w.write_prices(d, pl.DataFrame([price_row(d, 1, 100.0)], schema=PRICE_SCHEMA))
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


def test_deposit_buy_show_roundtrip(tmp_path: Path) -> None:
    seed(tmp_path)
    runner = CliRunner()
    r1 = runner.invoke(
        app,
        [
            "portfolio",
            "deposit",
            "--amount",
            "1000",
            "--date",
            "2025-01-02",
            "--root",
            str(tmp_path),
        ],
    )
    assert r1.exit_code == 0, r1.output
    r2 = runner.invoke(
        app,
        [
            "portfolio",
            "buy",
            "--product-id",
            "1",
            "--sub-type",
            "Normal",
            "--qty",
            "2",
            "--price",
            "90",
            "--fees",
            "2",
            "--date",
            "2025-01-03",
            "--root",
            str(tmp_path),
        ],
    )
    assert r2.exit_code == 0, r2.output
    r3 = runner.invoke(app, ["portfolio", "show", "--root", str(tmp_path)])
    assert r3.exit_code == 0, r3.output
    assert "Crashed Box" in r3.output
    assert "818.00" in r3.output  # cash 1000 - 180 - 2
    assert "20.00" in r3.output  # unrealized (100-90)*2
    assert (tmp_path / "data" / "portfolio" / "ledger.jsonl").exists()


def test_invalid_entry_rejected_cleanly(tmp_path: Path) -> None:
    seed(tmp_path)
    result = CliRunner().invoke(
        app,
        [
            "portfolio",
            "buy",
            "--product-id",
            "1",
            "--sub-type",
            "Normal",
            "--qty",
            "1",
            "--price",
            "90",
            "--root",
            str(tmp_path),
        ],  # no deposit
    )
    assert result.exit_code != 0
    assert "negative" in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert not (tmp_path / "data" / "portfolio" / "ledger.jsonl").exists()


def test_show_empty_portfolio(tmp_path: Path) -> None:
    seed(tmp_path)
    result = CliRunner().invoke(app, ["portfolio", "show", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "empty" in result.output.lower()
