from datetime import date
from pathlib import Path

import polars as pl
import pytest
from typer.testing import CliRunner

from pkmn_quant.cli import app
from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA
from pkmn_quant.data.warehouse import Warehouse

D1, D2, D3 = date(2025, 6, 1), date(2025, 6, 2), date(2025, 6, 3)


def row(day: date, product_id: int, market: float) -> dict[str, object]:
    return {
        "date": day,
        "product_id": product_id,
        "sub_type": "Normal",
        "low": 1.0,
        "mid": 2.0,
        "high": 3.0,
        "market": market,
    }


def seed(root: Path) -> None:
    w = Warehouse(Paths(root=root))
    w.write_prices(D1, pl.DataFrame([row(D1, 1, 10.0)], schema=PRICE_SCHEMA))
    w.write_prices(D2, pl.DataFrame([row(D2, 1, 12.0)], schema=PRICE_SCHEMA))
    w.write_prices(D3, pl.DataFrame([row(D3, 1, 15.0)], schema=PRICE_SCHEMA))
    w.write_products(
        pl.DataFrame(
            {
                "product_id": [1],
                "group_id": [1],
                "name": ["Box"],
                "rarity": [None],
                "kind": ["sealed"],
                "released_on": [D1],
            }
        )
    )


def run_cli(root: Path) -> object:
    return CliRunner().invoke(
        app,
        [
            "backtest",
            "--start",
            "2025-06-01",
            "--end",
            "2025-06-03",
            "--cash",
            "100",
            "--root",
            str(root),
        ],
    )


def test_backtest_cli_runs_and_writes_results(tmp_path: Path) -> None:
    seed(tmp_path)
    result = run_cli(tmp_path)
    assert result.exit_code == 0, result.output
    assert "total_return" in result.output
    out_dir = tmp_path / "data" / "results"
    runs = list(out_dir.iterdir())
    assert len(runs) == 1
    assert (runs[0] / "equity.parquet").exists()
    assert (runs[0] / "fills.parquet").exists()


def test_backtest_golden_numbers(tmp_path: Path) -> None:
    """Golden regression: any engine change that alters results fails here.

    Hand-verified arithmetic (CostModel defaults: fee 12.75%, shipping $1,
    liquidity tier for $12 price -> cap 8):
      D1: no fills; BuyAndHold sees mark 10.0, budget 100 -> orders 10 units.
          Equity = 100 (all cash).
      D2: fill at print 12.0. Clips: liquidity cap 8; cash floor((100-1)/12)=8.
          -> buy 8 @ 12, fees $1. Cash = 100 - 96 - 1 = 3. Equity = 3 + 8*12 = 99.
      D3: no orders. Equity = 3 + 8*15 = 123.
    """
    seed(tmp_path)
    run_cli(tmp_path)
    out_dir = tmp_path / "data" / "results"
    run_dir = next(iter(out_dir.iterdir()))
    equity = pl.read_parquet(run_dir / "equity.parquet")["equity"].to_list()
    assert equity == pytest.approx([100.0, 99.0, 123.0])
    fills = pl.read_parquet(run_dir / "fills.parquet")
    assert fills.height == 1
    f = fills.row(0, named=True)
    assert f["quantity"] == 8
    assert f["price"] == pytest.approx(12.0)
    assert f["fees"] == pytest.approx(1.0)


def test_backtest_bad_dates_clean_error(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app, ["backtest", "--start", "garbage", "--end", "2025-06-03", "--root", str(tmp_path)]
    )
    assert result.exit_code != 0
    assert "Traceback" not in result.output
