from datetime import date
from pathlib import Path

import polars as pl
import pytest
from typer.testing import CliRunner

from pkmn_quant.cli import app
from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA
from pkmn_quant.data.warehouse import Warehouse
from tests.helpers import price_row

D1, D2, D3 = date(2025, 6, 1), date(2025, 6, 2), date(2025, 6, 3)

row = price_row


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


def seed_impact(root: Path) -> None:
    """Like seed(), but with a real (uncrossed) mid so impact is nonzero.

    price_row hardcodes mid=2.0 which is crossed against market>2 (impact
    clamps to zero); override mid per day.
    """
    w = Warehouse(Paths(root=root))
    for day, market, mid in ((D1, 10.0, 13.0), (D2, 12.0, 16.0), (D3, 15.0, 18.0)):
        r = row(day, 1, market)
        r["mid"] = mid
        w.write_prices(day, pl.DataFrame([r], schema=PRICE_SCHEMA))
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


def run_cli(root: Path, *extra: str) -> object:
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
            *extra,
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

    Runs --no-impact: these numbers pin the flat-cost engine.

    Hand-verified arithmetic (CostModel defaults: fee 12.75%, shipping $1,
    liquidity tier for $12 price -> cap 8):
      D1: no fills; BuyAndHold sees mark 10.0, budget 100 -> orders 10 units.
          Equity = 100 (all cash).
      D2: fill at print 12.0. Clips: liquidity cap 8; cash floor((100-1)/12)=8.
          -> buy 8 @ 12, fees $1. Cash = 100 - 96 - 1 = 3. Equity = 3 + 8*12 = 99.
      D3: no orders. Equity = 3 + 8*15 = 123.
    """
    seed(tmp_path)
    run_cli(tmp_path, "--no-impact")
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
    assert f["impact"] == pytest.approx(0.0)


def test_backtest_golden_numbers_with_impact(tmp_path: Path) -> None:
    """Golden regression for the impact-on engine (CLI default).

    Hand-verified arithmetic (CostModel defaults + impact_enabled; $12 price
    -> liquidity cap Q=8):
      D1: no fills; BuyAndHold sees mark 10.0, budget 100 -> orders 10 units.
          Equity = 100 (all cash).
      D2: print 12.0, mid 16.0 -> spread 4. Flat clip: min(10, cap 8,
          floor((100-1)/12)=8) = 8. Impact(8) = 4*8*8/(2*8) = 16;
          8*12+1+16 = 113 > 100 -> shrink. Impact(7) = 4*7*7/16 = 12.25;
          7*12+1+12.25 = 97.25 <= 100 -> fill 7 @ print 12, fees 1,
          impact 12.25. Cash = 2.75. Equity = 2.75 + 7*12 = 86.75.
      D3: holding -> no orders. Equity = 2.75 + 7*15 = 107.75.
    """
    seed_impact(tmp_path)
    result = run_cli(tmp_path)
    assert result.exit_code == 0, result.output
    out_dir = tmp_path / "data" / "results"
    run_dir = next(iter(out_dir.iterdir()))
    equity = pl.read_parquet(run_dir / "equity.parquet")["equity"].to_list()
    assert equity == pytest.approx([100.0, 86.75, 107.75])
    fills = pl.read_parquet(run_dir / "fills.parquet")
    assert fills.height == 1
    f = fills.row(0, named=True)
    assert f["quantity"] == 7
    assert f["price"] == pytest.approx(12.0)
    assert f["fees"] == pytest.approx(1.0)
    assert f["impact"] == pytest.approx(12.25)


def test_backtest_bad_dates_clean_error(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app, ["backtest", "--start", "garbage", "--end", "2025-06-03", "--root", str(tmp_path)]
    )
    assert result.exit_code != 0
    assert "Traceback" not in result.output
