from datetime import date, timedelta
from pathlib import Path

import polars as pl
from typer.testing import CliRunner

from pkmn_quant.cli import app
from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA
from pkmn_quant.data.warehouse import Warehouse
from tests.helpers import price_row


def seed_forty_days(root: Path) -> None:
    w = Warehouse(Paths(root=root))
    start = date(2025, 1, 1)
    for i in range(40):
        d = start + timedelta(days=i)
        w.write_prices(d, pl.DataFrame([price_row(d, 1, 100.0 + i)], schema=PRICE_SCHEMA))
    w.write_products(
        pl.DataFrame(
            {
                "product_id": [1],
                "group_id": [1],
                "name": ["Box"],
                "rarity": [None],
                "kind": ["sealed"],
                "released_on": [start],
            }
        )
    )


def test_walkforward_cli_runs_and_writes_report(tmp_path: Path) -> None:
    seed_forty_days(tmp_path)
    result = CliRunner().invoke(
        app,
        [
            "walkforward",
            "--strategy",
            "sealed-accumulation",
            "--start",
            "2025-01-01",
            "--end",
            "2025-02-09",
            "--is-days",
            "10",
            "--oos-days",
            "10",
            "--trials",
            "2",
            "--cash",
            "1000",
            "--root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    out = tmp_path / "data" / "results"
    run_dir = next(iter(out.iterdir()))
    assert (run_dir / "report.md").exists()
    assert (run_dir / "stitched_equity.parquet").exists()
    assert (run_dir / "walkforward.json").exists()
    assert "overfitting_gap" in (run_dir / "report.md").read_text()


def test_walkforward_unknown_strategy_clean_error(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "walkforward",
            "--strategy",
            "nope",
            "--start",
            "2025-01-01",
            "--end",
            "2025-02-09",
            "--root",
            str(tmp_path),
        ],
    )
    assert result.exit_code != 0
    assert "nope" in result.output and "Traceback" not in result.output
    # CliRunner swallows exceptions into result.exception; only SystemExit
    # (typer's clean exit path) is acceptable.
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_walkforward_cli_warmup_days_clamps_to_available_data(tmp_path: Path) -> None:
    """--warmup-days 120 with only 40 days of data must not raise.

    This exercises the warm-up clamping path: the fixture has 40 days, so a
    120-day warm-up before the IS start finds no earlier warehouse data.  The
    load silently returns whatever is available and the run completes normally.
    """
    seed_forty_days(tmp_path)
    result = CliRunner().invoke(
        app,
        [
            "walkforward",
            "--strategy",
            "sealed-accumulation",
            "--start",
            "2025-01-01",
            "--end",
            "2025-02-09",
            "--is-days",
            "10",
            "--oos-days",
            "10",
            "--trials",
            "2",
            "--cash",
            "1000",
            "--warmup-days",
            "120",
            "--root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output


def test_walkforward_unknown_objective_metric_clean_error(tmp_path: Path) -> None:
    seed_forty_days(tmp_path)
    result = CliRunner().invoke(
        app,
        [
            "walkforward",
            "--strategy",
            "sealed-accumulation",
            "--start",
            "2025-01-01",
            "--end",
            "2025-02-09",
            "--objective-metric",
            "sharpe_ratio",
            "--root",
            str(tmp_path),
        ],
    )
    assert result.exit_code != 0
    assert "sharpe_ratio" in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_walkforward_unknown_engine_clean_error(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "walkforward",
            "--strategy",
            "sealed-accumulation",
            "--start",
            "2025-01-01",
            "--end",
            "2025-02-09",
            "--engine",
            "bogus",
            "--root",
            str(tmp_path),
        ],
    )
    assert result.exit_code != 0
    assert "bogus" in result.output and "Traceback" not in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_walkforward_negative_workers_clean_error(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "walkforward",
            "--strategy",
            "sealed-accumulation",
            "--start",
            "2025-06-01",
            "--end",
            "2025-06-03",
            "--workers",
            "-2",
            "--root",
            str(tmp_path),
        ],
    )
    assert result.exit_code != 0
    assert "workers" in result.output
    assert "Traceback" not in result.output


def test_walkforward_records_requested_and_resolved_workers(tmp_path: Path) -> None:
    """The registry runtime field carries both the requested workers value and
    the thread count it resolved to (auto depends on fold count and machine)."""
    import os

    from pkmn_quant.research.folds import make_folds
    from pkmn_quant.research.runs import load_runs

    seed_forty_days(tmp_path)
    result = CliRunner().invoke(
        app,
        [
            "walkforward",
            "--strategy",
            "sealed-accumulation",
            "--start",
            "2025-01-01",
            "--end",
            "2025-02-09",
            "--is-days",
            "10",
            "--oos-days",
            "10",
            "--trials",
            "2",
            "--cash",
            "1000",
            "--root",
            str(tmp_path),
        ],  # no --workers flag: CLI default 0 = auto
    )
    assert result.exit_code == 0, result.output
    n_folds = len(make_folds(date(2025, 1, 1), date(2025, 2, 9), is_days=10, oos_days=10))
    (record,) = load_runs(tmp_path)
    assert record.runtime == {
        "workers": 0,
        "workers_resolved": min(n_folds, os.cpu_count() or 1),
    }


def test_walkforward_report_carries_bootstrap_ci(tmp_path: Path) -> None:
    """report.md gains the CI band + caveat; walkforward.json gains rigor."""
    import json

    seed_forty_days(tmp_path)
    result = CliRunner().invoke(
        app,
        [
            "walkforward",
            "--strategy",
            "sealed-accumulation",
            "--start",
            "2025-01-01",
            "--end",
            "2025-02-09",
            "--is-days",
            "10",
            "--oos-days",
            "10",
            "--trials",
            "2",
            "--cash",
            "1000",
            "--root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    run_dir = next((tmp_path / "data" / "results").iterdir())
    report = (run_dir / "report.md").read_text()
    assert "95% CI" in report
    assert "inherit" in report  # the mark-smoothing caveat extension
    rigor = json.loads((run_dir / "walkforward.json").read_text())["rigor"]
    ci = rigor["stitched_total_return_ci"]
    assert ci["lo"] <= ci["point"] <= ci["hi"]
    assert ci["seed"] == 42
