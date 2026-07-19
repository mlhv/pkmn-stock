"""pkmn evaluate: cross-strategy rigor over synthetic walkforward artifacts."""

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl
from typer.testing import CliRunner

from pkmn_quant.cli import app
from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA
from pkmn_quant.data.warehouse import Warehouse
from tests.helpers import price_row

START = date(2025, 1, 1)


def _write_curve(run_dir: Path, name: str, equity: list[float], as_wf: bool = True) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    days = [START + timedelta(days=i) for i in range(len(equity))]
    frame = pl.DataFrame({"date": days, "equity": equity})
    if as_wf:
        frame.write_parquet(run_dir / "stitched_equity.parquet")
        (run_dir / "walkforward.json").write_text(
            json.dumps({"strategy": name, "folds": [], "summary": {}})
        )
    else:
        frame.write_parquet(run_dir / "equity.parquet")


def seed_everything(root: Path, n_days: int = 200) -> None:
    # a real (tiny) warehouse so record_run's data fingerprint works
    w = Warehouse(Paths(root=root))
    w.write_prices(START, pl.DataFrame([price_row(START, 1, 10.0)], schema=PRICE_SCHEMA))
    rng = np.random.default_rng(0)
    results = root / "data" / "results"
    bench = list(100.0 * np.cumprod(1.0 + rng.normal(0.002, 0.01, n_days)))
    _write_curve(results / "buy-and-hold-sealed-x", "buy-and-hold", bench, as_wf=False)
    for name, drift in [("alpha", 0.0), ("beta", -0.001)]:
        eq = list(100.0 * np.cumprod(1.0 + rng.normal(drift, 0.01, n_days)))
        _write_curve(results / f"wf-{name}-x", name, eq)


def run_eval(root: Path, *extra: str) -> object:
    return CliRunner().invoke(app, ["evaluate", "--root", str(root), "--n-boot", "500", *extra])


def test_evaluate_end_to_end(tmp_path: Path) -> None:
    seed_everything(tmp_path)
    result = run_eval(tmp_path)
    assert result.exit_code == 0, result.output
    out = next((tmp_path / "data" / "results").glob("evaluate-*"))
    report = (out / "report.md").read_text()
    assert "Reality Check" in report and "deflated" in report.lower()
    assert "inherit" in report  # caveat present
    payload = json.loads((out / "evaluate.json").read_text())
    assert set(payload["strategies"]) == {"alpha", "beta"}
    for s in payload["strategies"].values():
        assert s["ci"]["lo"] <= s["ci"]["point"] <= s["ci"]["hi"]
        assert 0.0 <= s["dsr"] <= 1.0
    assert 0.0 <= payload["reality_check_p"] <= 1.0
    assert payload["params"] == {"n_boot": 500, "mean_block": 10.0, "seed": 42}


def test_evaluate_is_deterministic(tmp_path: Path) -> None:
    seed_everything(tmp_path)
    assert run_eval(tmp_path).exit_code == 0
    first = json.loads(
        (next((tmp_path / "data" / "results").glob("evaluate-*")) / "evaluate.json").read_text()
    )
    assert run_eval(tmp_path).exit_code == 0  # overwrites same-day dir
    second = json.loads(
        (next((tmp_path / "data" / "results").glob("evaluate-*")) / "evaluate.json").read_text()
    )
    assert first == second


def test_evaluate_records_run(tmp_path: Path) -> None:
    from pkmn_quant.research.runs import load_runs

    seed_everything(tmp_path)
    assert run_eval(tmp_path).exit_code == 0
    (record,) = load_runs(tmp_path)
    assert record.command == "evaluate"
    assert "reality_check_p" in record.results
    assert record.config["n_boot"] == 500


def test_evaluate_needs_two_strategies(tmp_path: Path) -> None:
    seed_everything(tmp_path)
    import shutil

    shutil.rmtree(tmp_path / "data" / "results" / "wf-beta-x")
    result = run_eval(tmp_path)
    assert result.exit_code != 0
    assert "2 strategies" in result.output
    assert "Traceback" not in result.output


def test_evaluate_clean_error_without_artifacts(tmp_path: Path) -> None:
    (tmp_path / "data" / "results").mkdir(parents=True)
    result = run_eval(tmp_path)
    assert result.exit_code != 0
    assert "no walk-forward artifacts" in result.output
    assert "Traceback" not in result.output


def test_evaluate_thin_overlap_errors(tmp_path: Path) -> None:
    seed_everything(tmp_path, n_days=30)  # < 60 common days
    result = run_eval(tmp_path)
    assert result.exit_code != 0
    assert "overlap" in result.output


def test_evaluate_rejects_zero_n_boot(tmp_path: Path) -> None:
    seed_everything(tmp_path)
    result = CliRunner().invoke(app, ["evaluate", "--root", str(tmp_path), "--n-boot", "0"])
    assert result.exit_code != 0
    assert "Traceback" not in result.output
