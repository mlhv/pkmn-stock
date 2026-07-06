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


def test_missing_warehouse_gives_clean_error(tmp_path: Path) -> None:
    """Any portfolio command against an empty root must exit nonzero with a clean message.

    'pkmn portfolio deposit' calls _portfolio_deps which checks for the products
    parquet before calling load_products(); if missing it should raise BadParameter
    with "no warehouse" rather than letting pl.read_parquet produce a raw traceback.
    """
    result = CliRunner().invoke(
        app,
        ["portfolio", "deposit", "--amount", "100", "--root", str(tmp_path)],
    )
    assert result.exit_code != 0
    assert "no warehouse" in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_bad_date_gives_clean_error(tmp_path: Path) -> None:
    """--date not-a-date must exit nonzero without leaking 'ledger line' internals.

    The date is validated at the CLI layer via dt.date.fromisoformat before the
    event dict is built, so the error comes from typer.BadParameter rather than
    from the ledger parser (which would say "ledger line 1: ...").
    """
    seed(tmp_path)
    result = CliRunner().invoke(
        app,
        [
            "portfolio",
            "deposit",
            "--amount",
            "100",
            "--date",
            "not-a-date",
            "--root",
            str(tmp_path),
        ],
    )
    assert result.exit_code != 0
    assert "ledger line" not in result.output


def test_full_roundtrip_realized_pnl(tmp_path: Path) -> None:
    """deposit → buy → sell → withdraw → show: realized P&L appears in show output.

    Hand-derivation (seeded warehouse: product 1 at price 100.0 for 10 days
    from 2025-01-01):

      deposit  $1000 on 2025-01-02
      buy  2 @ $90 fees $2 on 2025-01-03:
        cash = 1000 - (2*90 + 2) = $818
        realized_pnl = -2 (buy fees debited immediately per Portfolio._buy)
        position: qty=2, avg_cost=90.0
      sell 2 @ $120 fees $5 on 2025-01-05:
        proceeds = 2*120 = $240
        cash = 818 + (240 - 5) = $1053
        realized_pnl = -2 + (240 - 2*90 - 5) = -2 + 55 = +$53
        positions: empty
      withdraw $1053 on 2025-01-06:
        cash = $0; realized_pnl still $53

    show must not say "empty" (realized_pnl != 0) and must print "+$53.00".
    """
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

    r3 = runner.invoke(
        app,
        [
            "portfolio",
            "sell",
            "--product-id",
            "1",
            "--sub-type",
            "Normal",
            "--qty",
            "2",
            "--price",
            "120",
            "--fees",
            "5",
            "--date",
            "2025-01-05",
            "--root",
            str(tmp_path),
        ],
    )
    assert r3.exit_code == 0, r3.output

    r4 = runner.invoke(
        app,
        [
            "portfolio",
            "withdraw",
            "--amount",
            "1053",
            "--date",
            "2025-01-06",
            "--root",
            str(tmp_path),
        ],
    )
    assert r4.exit_code == 0, r4.output

    r5 = runner.invoke(app, ["portfolio", "show", "--root", str(tmp_path)])
    assert r5.exit_code == 0, r5.output
    assert "empty" not in r5.output.lower()
    assert "+53.00" in r5.output  # realized P&L = +$53.00
