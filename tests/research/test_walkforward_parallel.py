"""Serial == parallel, bit-for-bit — the Plan 11 acceptance property.

seed_rich (60 days) with is=20/oos=10 yields 4 folds; a trivial fixed-params
optimizer keeps runs fast while still exercising evaluate + IS + OOS per fold.
"""

from datetime import timedelta
from pathlib import Path

import pytest

from pkmn_quant.config import Paths
from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.costs import CostModel
from pkmn_quant.engine.strategy import Strategy
from pkmn_quant.research.walkforward import Params, WalkForwardResult, run_walkforward
from pkmn_quant.strategies.dip_buyer import DipBuyer
from tests.test_native_parity import START, seed_rich

FIXED: Params = {
    "dip_window_days": 5,
    "dip_threshold": 0.10,
    "hold_days": 7,
    "take_profit": 1.05,
}


def factory(p: Params) -> Strategy:
    return DipBuyer(
        dip_window_days=int(p["dip_window_days"]),
        dip_threshold=float(p["dip_threshold"]),
        hold_days=int(p["hold_days"]),
        take_profit=float(p["take_profit"]),
    )


def optimizer(fold: object, evaluate: object) -> Params:
    evaluate(dict(FIXED))  # type: ignore[operator]  # exercise the IS evaluate path
    return dict(FIXED)


def run_wf(root: Path, workers: int, strategy_name: str) -> WalkForwardResult:
    return run_walkforward(
        warehouse=Warehouse(Paths(root=root)),
        strategy_factory=factory,
        optimizer=optimizer,
        cost_model=CostModel(impact_enabled=True),
        start=START,
        end=START + timedelta(days=59),
        is_days=20,
        oos_days=10,
        initial_cash=1000.0,
        warmup_days=10,
        engine="cpp",
        strategy_name=strategy_name,
        workers=workers,
    )


def assert_wf_equal(a: WalkForwardResult, b: WalkForwardResult) -> None:
    assert a.stitched_curve["date"].to_list() == b.stitched_curve["date"].to_list()
    assert a.stitched_curve["equity"].to_list() == b.stitched_curve["equity"].to_list()
    assert a.summary == b.summary
    assert len(a.folds) == len(b.folds)
    for fa, fb in zip(a.folds, b.folds, strict=True):
        assert fa.fold == fb.fold
        assert fa.params == fb.params
        assert fa.is_summary == fb.is_summary
        assert fa.oos_summary == fb.oos_summary
        assert fa.oos_curve["equity"].to_list() == fb.oos_curve["equity"].to_list()


def test_parallel_matches_serial_native(tmp_path: Path) -> None:
    seed_rich(tmp_path, n_days=60)
    serial = run_wf(tmp_path, workers=1, strategy_name="dip-buyer")
    parallel = run_wf(tmp_path, workers=4, strategy_name="dip-buyer")
    assert len(serial.folds) == 4
    assert serial.stitched_curve.height > 0
    assert_wf_equal(serial, parallel)


def test_parallel_matches_serial_bridge(tmp_path: Path) -> None:
    """A non-native strategy_name forces the callback bridge in every fold."""
    seed_rich(tmp_path, n_days=60)
    serial = run_wf(tmp_path, workers=1, strategy_name="bridge-test")
    parallel = run_wf(tmp_path, workers=4, strategy_name="bridge-test")
    assert_wf_equal(serial, parallel)


def test_parallel_is_deterministic_across_repetitions(tmp_path: Path) -> None:
    seed_rich(tmp_path, n_days=60)
    runs = [run_wf(tmp_path, workers=4, strategy_name="dip-buyer") for _ in range(3)]
    assert_wf_equal(runs[0], runs[1])
    assert_wf_equal(runs[0], runs[2])


def test_auto_workers_matches_serial(tmp_path: Path) -> None:
    seed_rich(tmp_path, n_days=60)
    serial = run_wf(tmp_path, workers=1, strategy_name="dip-buyer")
    auto = run_wf(tmp_path, workers=0, strategy_name="dip-buyer")
    assert_wf_equal(serial, auto)


def test_negative_workers_raises(tmp_path: Path) -> None:
    seed_rich(tmp_path, n_days=60)
    with pytest.raises(ValueError, match="workers"):
        run_wf(tmp_path, workers=-1, strategy_name="dip-buyer")


def test_fold_worker_exception_propagates(tmp_path: Path) -> None:
    seed_rich(tmp_path, n_days=60)

    def broken_factory(p: Params) -> Strategy:
        raise RuntimeError("boom in fold worker")

    with pytest.raises(RuntimeError, match="boom in fold worker"):
        run_walkforward(
            warehouse=Warehouse(Paths(root=tmp_path)),
            strategy_factory=broken_factory,
            optimizer=optimizer,
            cost_model=CostModel(),
            start=START,
            end=START + timedelta(days=59),
            is_days=20,
            oos_days=10,
            initial_cash=1000.0,
            engine="cpp",
            strategy_name="bridge-test",  # forces factory use -> raises
            workers=4,
        )
