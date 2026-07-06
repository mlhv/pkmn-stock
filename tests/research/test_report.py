from datetime import date

import polars as pl

from pkmn_quant.research.folds import Fold
from pkmn_quant.research.report import render_markdown
from pkmn_quant.research.walkforward import FoldResult, WalkForwardResult


def test_render_markdown_contains_fold_table_and_summary() -> None:
    fold = Fold(date(2024, 1, 1), date(2024, 6, 28), date(2024, 6, 29), date(2024, 8, 27))
    fr = FoldResult(
        fold=fold,
        params={"x": 1},
        is_summary={"total_return": 0.5, "sharpe": 2.0},
        oos_summary={"total_return": 0.1, "sharpe": 0.8},
        oos_curve=pl.DataFrame({"date": [date(2024, 6, 29)], "equity": [1000.0]}),
    )
    wf = WalkForwardResult(
        folds=[fr],
        stitched_curve=pl.DataFrame({"date": [date(2024, 6, 29)], "equity": [1000.0]}),
        summary={
            "stitched_total_return": 0.1,
            "is_total_return_mean": 0.5,
            "oos_total_return_mean": 0.1,
            "overfitting_gap": 0.4,
        },
    )
    md = render_markdown(wf, strategy_name="dip-buyer")
    assert "dip-buyer" in md
    assert "2024-06-29" in md  # fold OOS start appears in table
    assert "overfitting_gap" in md
    assert "0.4" in md


def test_params_formatted_compactly() -> None:
    from pkmn_quant.research.report import format_params

    assert format_params({"min_drawdown": 0.3287900192959751, "min_age_days": 33}) == (
        "min_drawdown=0.3288, min_age_days=33"
    )
    assert format_params({}) == "-"
