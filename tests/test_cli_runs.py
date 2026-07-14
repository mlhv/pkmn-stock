import json
from pathlib import Path

from typer.testing import CliRunner

from pkmn_quant.cli import app
from pkmn_quant.research.runs import load_runs, registry_path
from tests.test_cli_backtest import run_cli, seed


def test_backtest_records_a_run(tmp_path: Path) -> None:
    seed(tmp_path)
    result = run_cli(tmp_path)
    assert result.exit_code == 0, result.output
    records = load_runs(tmp_path)
    assert len(records) == 1
    assert records[0].command == "backtest"
    assert "run recorded: " + records[0].run_id in result.output


def test_runs_list_and_show(tmp_path: Path) -> None:
    seed(tmp_path)
    run_cli(tmp_path)
    run_id = load_runs(tmp_path)[0].run_id

    listed = CliRunner().invoke(app, ["runs", "list", "--root", str(tmp_path)])
    assert listed.exit_code == 0, listed.output
    assert run_id in listed.output

    shown = CliRunner().invoke(app, ["runs", "show", run_id[:8], "--root", str(tmp_path)])
    assert shown.exit_code == 0, shown.output
    payload = json.loads(shown.output)
    assert payload["run_id"] == run_id


def test_runs_show_unknown_id_clean_error(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["runs", "show", "nope", "--root", str(tmp_path)])
    assert result.exit_code != 0
    assert "no run matching" in result.output


def test_tracking_failure_does_not_fail_backtest(tmp_path: Path) -> None:
    seed(tmp_path)
    registry_path(tmp_path).mkdir(parents=True)  # unwritable: path is a dir
    result = run_cli(tmp_path)
    assert result.exit_code == 0, result.output
    assert "run tracking failed" in result.output
