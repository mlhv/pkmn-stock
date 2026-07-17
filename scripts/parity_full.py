"""Full-data parity acceptance: every strategy, both engines, 874 days.

Bit-for-bit or bust (spec 2026-07-14). Exit 0 only if every comparison is
exact. Run from the repo root (needs data/). --ml adds the ml-ranker bridge
run (slow: sklearn trains in-loop).
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path

from pkmn_quant.config import Paths
from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.backtest import Backtest, Result
from pkmn_quant.engine.costs import CostModel
from pkmn_quant.engine.native import NativeBacktest, NativeStrategySpec
from pkmn_quant.engine.strategy import Strategy
from pkmn_quant.strategies.buy_and_hold import BuyAndHold
from pkmn_quant.strategies.cost_aware_reversion import CostAwareReversion
from pkmn_quant.strategies.dip_buyer import DipBuyer
from pkmn_quant.strategies.ml_ranker import MLRanker
from pkmn_quant.strategies.momentum import CrossSectionalMomentum
from pkmn_quant.strategies.sealed_accumulation import SealedAccumulation

START, END = date(2024, 3, 1), date(2026, 6, 30)
CASH = 10_000.0
WARMUP = 120

RULE_STRATEGIES: list[tuple[str, Strategy]] = [
    ("buy-and-hold", BuyAndHold(kind="sealed")),
    ("sealed-accumulation", SealedAccumulation()),
    ("dip-buyer", DipBuyer()),
    ("xs-momentum", CrossSectionalMomentum()),
    ("cost-aware-reversion", CostAwareReversion()),
]


def compare(name: str, py: Result, cpp: Result) -> bool:
    ok = True
    eq_py = py.equity_curve["equity"].to_list()
    eq_cpp = cpp.equity_curve["equity"].to_list()
    if eq_py != eq_cpp:
        first = next(i for i, (a, b) in enumerate(zip(eq_py, eq_cpp, strict=True)) if a != b)
        print(f"  EQUITY DIVERGES at index {first}: {eq_py[first]!r} != {eq_cpp[first]!r}")
        ok = False
    if len(py.fills) != len(cpp.fills):
        print(f"  FILL COUNT differs: {len(py.fills)} vs {len(cpp.fills)}")
        ok = False
    else:
        for i, (a, b) in enumerate(zip(py.fills, cpp.fills, strict=True)):
            same = (
                a.day == b.day
                and a.asset == b.asset
                and a.quantity == b.quantity
                and a.price == b.price
                and a.fees == b.fees
                and a.impact == b.impact
            )
            if not same:
                print(f"  FILL {i} differs: {a} vs {b}")
                ok = False
                break
    print(f"{'PASS' if ok else 'FAIL'}  {name}  ({len(py.fills)} fills)")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ml", action="store_true", help="include ml-ranker (slow)")
    parser.add_argument("--impact", action="store_true", default=True)
    args = parser.parse_args()

    wh = Warehouse(Paths(root=Path(".")))
    cm = CostModel(impact_enabled=args.impact)
    all_ok = True
    for name, strategy in RULE_STRATEGIES:
        t0 = time.perf_counter()
        py = Backtest(
            warehouse=wh,
            strategy=strategy,
            cost_model=cm,
            start=START,
            end=END,
            initial_cash=CASH,
            warmup_days=WARMUP,
        ).run()
        t_py = time.perf_counter() - t0
        spec = (
            NativeStrategySpec("buy-and-hold", {}, kind="sealed")
            if name == "buy-and-hold"
            else NativeStrategySpec(name, {})
        )
        t0 = time.perf_counter()
        cpp = NativeBacktest(
            warehouse=wh,
            strategy=spec,
            cost_model=cm,
            start=START,
            end=END,
            initial_cash=CASH,
            warmup_days=WARMUP,
        ).run()
        t_cpp = time.perf_counter() - t0
        print(f"[{name}] python {t_py:.2f}s / cpp {t_cpp:.2f}s")
        all_ok &= compare(name, py, cpp)

    if args.ml:
        t0 = time.perf_counter()
        py = Backtest(
            warehouse=wh,
            strategy=MLRanker(),
            cost_model=cm,
            start=START,
            end=END,
            initial_cash=CASH,
            warmup_days=WARMUP,
        ).run()
        t_py = time.perf_counter() - t0
        t0 = time.perf_counter()
        cpp = NativeBacktest(
            warehouse=wh,
            strategy=MLRanker(),
            cost_model=cm,
            start=START,
            end=END,
            initial_cash=CASH,
            warmup_days=WARMUP,
        ).run()
        t_cpp = time.perf_counter() - t0
        print(f"[ml-ranker (bridge)] python {t_py:.2f}s / cpp {t_cpp:.2f}s")
        all_ok &= compare("ml-ranker (bridge)", py, cpp)

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
