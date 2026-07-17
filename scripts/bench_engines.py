"""Measured engine speedup: python vs cpp, full 874-day range, best of 3.

Prints a markdown table for docs/research-findings-2026-07.md. Both timings
are total wall-clock (Backtest.run() / NativeBacktest.run()), which for the
C++ path includes the polars load + flatten that crosses the boundary once
per run — the C++ loop itself does not shrink that part, so end-to-end
speedup is smaller than the engine-loop-only speedup would be.
"""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path

from pkmn_quant.config import Paths
from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.backtest import Backtest
from pkmn_quant.engine.costs import CostModel
from pkmn_quant.engine.native import NativeBacktest, NativeStrategySpec
from pkmn_quant.strategies.buy_and_hold import BuyAndHold
from pkmn_quant.strategies.dip_buyer import DipBuyer
from pkmn_quant.strategies.sealed_accumulation import SealedAccumulation

START, END = date(2024, 3, 1), date(2026, 6, 30)
CASH = 10_000.0
WARMUP = 120
REPS = 3

CASES = [
    (
        "buy-and-hold",
        lambda: BuyAndHold(kind="sealed"),
        NativeStrategySpec("buy-and-hold", {}, kind="sealed"),
    ),
    ("sealed-accumulation", SealedAccumulation, NativeStrategySpec("sealed-accumulation", {})),
    ("dip-buyer", DipBuyer, NativeStrategySpec("dip-buyer", {})),
]


def best_of(fn: object, reps: int = REPS) -> float:
    times = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()  # type: ignore[operator]
        times.append(time.perf_counter() - t0)
    return min(times)


def main() -> None:
    wh = Warehouse(Paths(root=Path(".")))
    cm = CostModel(impact_enabled=True)
    print("| strategy | python (s) | cpp (s) | speedup |")
    print("|---|---|---|---|")
    for name, make_py, spec in CASES:
        t_py = best_of(
            lambda make_py=make_py: Backtest(
                warehouse=wh,
                strategy=make_py(),
                cost_model=cm,
                start=START,
                end=END,
                initial_cash=CASH,
                warmup_days=WARMUP,
            ).run()
        )
        t_cpp = best_of(
            lambda spec=spec: NativeBacktest(
                warehouse=wh,
                strategy=spec,
                cost_model=cm,
                start=START,
                end=END,
                initial_cash=CASH,
                warmup_days=WARMUP,
            ).run()
        )
        print(f"| {name} | {t_py:.2f} | {t_cpp:.2f} | {t_py / t_cpp:.1f}x |")


if __name__ == "__main__":
    main()
