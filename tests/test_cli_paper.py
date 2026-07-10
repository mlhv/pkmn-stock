import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import pkmn_quant.live.notify as notify
from pkmn_quant.cli import app
from tests.test_cli_daily import run_walkforward, seed


def test_paper_daily_auto_records_fills(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notify, "send_notification", lambda t, b: None)
    seed(tmp_path)
    runner = CliRunner()
    run_walkforward(runner, tmp_path)
    r = runner.invoke(
        app,
        [
            "portfolio",
            "deposit",
            "--amount",
            "1000",
            "--date",
            "2025-01-02",
            "--paper",
            "--root",
            str(tmp_path),
        ],
    )
    assert r.exit_code == 0, r.output

    result = runner.invoke(app, ["daily", "--skip-ingest", "--paper", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output

    paper = tmp_path / "data" / "portfolio" / "paper.jsonl"
    lines = paper.read_text().strip().splitlines()
    assert len(lines) >= 2  # deposit + at least one auto-recorded buy
    buy_event = json.loads(lines[1])
    assert buy_event["kind"] == "buy"
    # CostModel.shipping_per_line == 1.0; executor sets fees = shipping for buys
    assert buy_event["fees"] == 1.0
    assert not (tmp_path / "data" / "portfolio" / "ledger.jsonl").exists()  # real untouched

    # Output goes to the -paper suffixed directory (Fix 5)
    daily_dirs = list((tmp_path / "data" / "results").glob("daily-*"))
    assert len(daily_dirs) == 1
    assert daily_dirs[0].name.endswith("-paper")

    # Paper label on every surface
    daily_dir = daily_dirs[0]
    assert "PAPER" in (daily_dir / "signals.md").read_text()
    assert json.loads((daily_dir / "daily.json").read_text())["paper"] is True


def test_paper_daily_records_sell_with_fee_formula(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-seed the paper ledger with a buy at 35; latest mark is 100.

    100 >= 35 * take_profit for every take_profit in the search space
    (max 2.5 -> threshold 87.5), so the SELL fires regardless of which
    params optuna picked.

    Liquidity check: DEFAULT_LIQUIDITY_TIERS = ((5.0,20),(50.0,8),(200.0,3)).
    Mark 100 falls in the (50.0, 8) tier -> cap 8/day.  qty 2 < 8, so the
    full position is sold and no clipping occurs.

    Expected sell fees = qty * price * fee_rate + shipping
                       = 2 * 100.0 * 0.1275 + 1.0 = 25.5 + 1.0 = 26.5
    """
    monkeypatch.setattr(notify, "send_notification", lambda t, b: None)
    seed(tmp_path)
    runner = CliRunner()
    run_walkforward(runner, tmp_path)

    # Seed the paper ledger: deposit then buy 2 units at 35
    for args in (
        [
            "portfolio",
            "deposit",
            "--amount",
            "1000",
            "--date",
            "2025-01-02",
            "--paper",
            "--root",
            str(tmp_path),
        ],
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
            "35",
            "--date",
            "2025-01-03",
            "--paper",
            "--root",
            str(tmp_path),
        ],
    ):
        r = runner.invoke(app, args)
        assert r.exit_code == 0, r.output

    result = runner.invoke(app, ["daily", "--skip-ingest", "--paper", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output

    paper = tmp_path / "data" / "portfolio" / "paper.jsonl"
    lines = paper.read_text().strip().splitlines()

    # Find the auto-recorded sell line (kind == "sell")
    sell_lines = [json.loads(ln) for ln in lines if json.loads(ln).get("kind") == "sell"]
    assert sell_lines, "no auto-recorded sell; check fixture produces SELL signal"

    sell = sell_lines[0]
    assert sell["kind"] == "sell"
    assert sell["qty"] > 0
    qty = sell["qty"]
    price = sell["price"]
    expected_fees = round(qty * price * 0.1275 + 1.0, 2)
    assert sell["fees"] == expected_fees


def test_paper_daily_works_when_deposit_postdates_warehouse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real-world shape: warehouse data ends (as_of 2025-05-01) BEFORE the user
    funds the paper account (2025-06-01). Fills must be dated the run date so
    the replay's date-sort keeps them after the deposit; dating them as_of
    replays buys before the money exists and rejects the batch."""
    monkeypatch.setattr(notify, "send_notification", lambda t, b: None)
    seed(tmp_path)
    runner = CliRunner()
    run_walkforward(runner, tmp_path)
    r = runner.invoke(
        app,
        [
            "portfolio",
            "deposit",
            "--amount",
            "1000",
            "--date",
            "2025-06-01",
            "--paper",
            "--root",
            str(tmp_path),
        ],
    )
    assert r.exit_code == 0, r.output
    result = runner.invoke(app, ["daily", "--skip-ingest", "--paper", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    lines = (tmp_path / "data" / "portfolio" / "paper.jsonl").read_text().strip().splitlines()
    assert len(lines) >= 2
    buy = json.loads(lines[1])
    assert buy["kind"] == "buy"
    import datetime as dt

    assert buy["date"] == dt.date.today().isoformat()  # run date, not as_of


def test_paper_signals_writes_to_paper_suffixed_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """signals --paper must write artifacts to a -paper suffixed directory."""
    monkeypatch.setattr(notify, "send_notification", lambda t, b: None)
    seed(tmp_path)
    runner = CliRunner()
    run_walkforward(runner, tmp_path)
    r = runner.invoke(
        app,
        [
            "portfolio",
            "deposit",
            "--amount",
            "1000",
            "--date",
            "2025-01-02",
            "--paper",
            "--root",
            str(tmp_path),
        ],
    )
    assert r.exit_code == 0, r.output

    result = runner.invoke(
        app,
        ["signals", "--strategy", "sealed-accumulation", "--paper", "--root", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output

    # The artifact directory must carry the -paper suffix.
    signal_dirs = list((tmp_path / "data" / "results").glob("signals-sealed-accumulation-*"))
    assert len(signal_dirs) == 1, f"expected 1 signals dir, got {signal_dirs}"
    assert signal_dirs[0].name.endswith("-paper"), (
        f"signals dir should end with -paper, got {signal_dirs[0].name}"
    )

    signals_md = signal_dirs[0] / "signals.md"
    assert signals_md.exists()
    assert "(PAPER)" in signals_md.read_text()


def test_paper_show_reads_paper_ledger(tmp_path: Path) -> None:
    seed(tmp_path)
    runner = CliRunner()
    r = runner.invoke(
        app,
        [
            "portfolio",
            "deposit",
            "--amount",
            "777",
            "--date",
            "2025-01-02",
            "--paper",
            "--root",
            str(tmp_path),
        ],
    )
    assert r.exit_code == 0, r.output
    show = runner.invoke(app, ["portfolio", "show", "--paper", "--root", str(tmp_path)])
    assert show.exit_code == 0, show.output
    assert "777.00" in show.output
