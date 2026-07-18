"""Walk-forward wall-clock: python serial vs cpp serial vs cpp parallel.

One run per config (a walkforward is minutes, not microseconds — best-of-N
would triple an already-long benchmark; treat small deltas as noise).
Includes the Plan 11 acceptance check: cpp serial and cpp parallel results
must be exactly equal. Run from the repo root (needs data/).
"""

from __future__ import annotations

import sys
import time
from datetime import date
from pathlib import Path

from pkmn_quant.config import Paths
from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.costs import CostModel
from pkmn_quant.research.registry import REGISTRY
from pkmn_quant.research.search import SearchSpec, optimize_params
from pkmn_quant.research.walkforward import Fold, Params, WalkForwardResult, run_walkforward

START, END = date(2024, 3, 1), date(2026, 6, 30)
STRATEGY = "sealed-accumulation"
TRIALS = 15
SEED = 42


def run_one(engine: str, workers: int) -> tuple[float, WalkForwardResult]:
    entry = REGISTRY[STRATEGY]

    def optimizer(fold: Fold, evaluate: object) -> Params:
        spec = SearchSpec(space=entry.space, n_trials=TRIALS, seed=SEED)
        return optimize_params(spec, evaluate)  # type: ignore[arg-type]

    t0 = time.perf_counter()
    result = run_walkforward(
        warehouse=Warehouse(Paths(root=Path("."))),
        strategy_factory=entry.factory,
        optimizer=optimizer,
        cost_model=CostModel(impact_enabled=True),
        start=START,
        end=END,
        is_days=180,
        oos_days=60,
        initial_cash=10_000.0,
        warmup_days=120,
        engine=engine,
        strategy_name=STRATEGY if engine == "cpp" else None,
        workers=workers,
    )
    return time.perf_counter() - t0, result


def main() -> int:
    t_cpp_par, r_cpp_par = run_one("cpp", 0)
    t_cpp_ser, r_cpp_ser = run_one("cpp", 1)
    t_py, _ = run_one("python", 1)

    print("| config | wall-clock (s) | speedup vs python |")
    print("|---|---|---|")
    print(f"| python, serial | {t_py:.1f} | 1.0x |")
    print(f"| cpp, serial | {t_cpp_ser:.1f} | {t_py / t_cpp_ser:.1f}x |")
    print(f"| cpp, workers=auto | {t_cpp_par:.1f} | {t_py / t_cpp_par:.1f}x |")

    ok = (
        r_cpp_ser.stitched_curve["equity"].to_list() == r_cpp_par.stitched_curve["equity"].to_list()
        and r_cpp_ser.summary == r_cpp_par.summary
        and [f.params for f in r_cpp_ser.folds] == [f.params for f in r_cpp_par.folds]
    )
    print(f"\nserial == parallel (bit-for-bit): {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
