import json
from datetime import date
from pathlib import Path

import polars as pl

from pkmn_quant.research.artifacts import (
    WalkForwardRun,
    find_latest_wf_run,
    load_walkforward_json,
    write_walkforward_json,
)
from pkmn_quant.research.folds import Fold
from pkmn_quant.research.walkforward import FoldResult, WalkForwardResult


def _result() -> WalkForwardResult:
    fold = Fold(date(2024, 1, 1), date(2024, 6, 28), date(2024, 6, 29), date(2024, 8, 27))
    fr = FoldResult(
        fold=fold,
        params={"hold_days": 30, "take_profit": 1.25},
        is_summary={"total_return": 0.5},
        oos_summary={"total_return": 0.1},
        oos_curve=pl.DataFrame({"date": [date(2024, 6, 29)], "equity": [1000.0]}),
    )
    return WalkForwardResult(
        folds=[fr],
        stitched_curve=pl.DataFrame({"date": [date(2024, 6, 29)], "equity": [1000.0]}),
        summary={"stitched_total_return": 0.1, "overfitting_gap": 0.4},
    )


def test_json_round_trip(tmp_path: Path) -> None:
    write_walkforward_json(tmp_path, _result(), strategy_name="dip-buyer")
    raw = json.loads((tmp_path / "walkforward.json").read_text())
    assert raw["strategy"] == "dip-buyer"

    run = load_walkforward_json(tmp_path)
    assert isinstance(run, WalkForwardRun)
    assert run.strategy == "dip-buyer"
    assert run.folds[0].params == {"hold_days": 30, "take_profit": 1.25}
    assert isinstance(run.folds[0].params["hold_days"], int)  # ints survive round-trip typed
    assert run.folds[0].oos_start == "2024-06-29"
    assert run.summary["overfitting_gap"] == 0.4


def test_find_latest_wf_run_picks_lexicographically_last(tmp_path: Path) -> None:
    for name in ["wf-dip-buyer-2024-01-01-2024-06-30", "wf-dip-buyer-2024-01-01-2025-06-30"]:
        d = tmp_path / name
        d.mkdir()
        write_walkforward_json(d, _result(), strategy_name="dip-buyer")
    (tmp_path / "wf-xs-momentum-2024-01-01-2026-06-30").mkdir()  # no json inside; other strategy

    found = find_latest_wf_run(tmp_path, "dip-buyer")
    assert found is not None and found.name == "wf-dip-buyer-2024-01-01-2025-06-30"
    assert find_latest_wf_run(tmp_path, "xs-momentum") is None
    assert find_latest_wf_run(tmp_path / "missing", "dip-buyer") is None
