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
    assert json.loads(lines[1])["kind"] == "buy"
    assert not (tmp_path / "data" / "portfolio" / "ledger.jsonl").exists()  # real untouched

    # Paper label on every surface
    daily_dir = next((tmp_path / "data" / "results").glob("daily-*"))
    assert "PAPER" in (daily_dir / "signals.md").read_text()
    assert json.loads((daily_dir / "daily.json").read_text())["paper"] is True


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
