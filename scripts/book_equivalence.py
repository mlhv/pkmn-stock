"""Real-data equivalence gate for book execution (Plan B Task 7).

Book execution fills orders against modelled resting orders (synthetic, built
from the day's tcgcsv quote). This is a gate run once over real data, not a
pytest; its output goes in docs/benchmarks/pokemon-book.md.

    uv run python scripts/book_equivalence.py --start 2024-03-01 --end 2026-06-30

(a) Pricing equivalence. ``_engine.ladder_equivalence`` over every row of the
    window: the test-only exact ladder (1e9 ticks per dollar) taken out one
    unit at a time through the OrderBook, against CostModel.buy_impact /
    sell_impact, for every (used, qty) with used + qty <= depth. Gate:
    max_abs_err <= 1e-6 and depth_mismatches == 0 (exit 1 otherwise; never
    loosened). depth_mismatches counts rows whose book depth differs from
    the cost model's (ask: max_daily_qty(buy_base(market, low)); bid:
    max_daily_qty(market)), since the comparison itself runs over the depth
    the book reports.
(b) End to end. The five native strategies (default params, $10,000, impact
    on, warmup 0 as ``pkmn backtest``) under execution="formula" and the
    test-only "book-exact-test", through NativeBacktest. On asset-days where
    only one side filled (in both runs), the fills must match on day, asset
    and quantity, with impact within 1e-6 (exit 1 otherwise). Fills on
    two-sided asset-days (a buy and a sell of one asset on one day) are
    counted, not compared: the formula shares one depth counter across both
    sides of an asset-day, the book gives each side its own full depth, so
    they are expected to differ there. ml-ranker and ml-ranker-v2 are skipped
    (75-140 min each through the Python callback bridge).
(c) Cent vs formula. Total return, fill count and total impact under "book"
    (whole cents, the user default) vs "formula". Capability check, default
    params, NOT a research result — Plan D owns the re-run.

Prints a human summary and, last, one JSON object (``--json PATH`` also
writes it).
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np

from pkmn_quant import _engine
from pkmn_quant.config import Paths
from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.backtest import Result
from pkmn_quant.engine.costs import CostModel
from pkmn_quant.engine.native import NativeBacktest, NativeStrategySpec
from pkmn_quant.engine.portfolio import Fill
from pkmn_quant.engine.prepared import PreparedMarket

CASH = 10_000.0
WARMUP = 0  # pkmn backtest's default
TOL = 1e-6
STRATEGIES: list[tuple[str, NativeStrategySpec]] = [
    ("buy-and-hold", NativeStrategySpec("buy-and-hold", {}, kind="sealed")),
    ("sealed-accumulation", NativeStrategySpec("sealed-accumulation", {})),
    ("dip-buyer", NativeStrategySpec("dip-buyer", {})),
    ("xs-momentum", NativeStrategySpec("xs-momentum", {})),
    ("cost-aware-reversion", NativeStrategySpec("cost-aware-reversion", {})),
]


def git_stamp(root: Path) -> dict[str, Any]:
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, check=True
        ).stdout.strip()
    )
    return {"git_sha": sha, "git_dirty": dirty}


def part_a(p: PreparedMarket, cm: CostModel) -> dict[str, Any]:
    tiers = cm.liquidity_tiers
    t0 = time.perf_counter()
    out = _engine.ladder_equivalence(
        row_market=p.row_market,
        row_mid=p.row_mid,
        row_low=p.row_low,
        tier_thresholds=np.array([t for t, _ in tiers], dtype=np.float64),
        tier_qtys=np.array([q for _, q in tiers], dtype=np.int64),
        fallback_max_qty=cm.fallback_max_qty,
    )
    res: dict[str, Any] = dict(out)
    res["input_rows"] = int(p.row_market.shape[0])
    res["seconds"] = time.perf_counter() - t0
    w = int(res["worst_row"])
    if w >= 0:
        res["worst_quote"] = {
            "asset": str(p.asset_list[int(p.row_asset[w])]),
            "day_index": int(p.row_day[w]),
            "market": float(p.row_market[w]),
            "mid": float(p.row_mid[w]),
            "low": float(p.row_low[w]),
        }
    res["pass"] = float(res["max_abs_err"]) <= TOL and int(res["depth_mismatches"]) == 0
    return res


def by_asset_day(fills: list[Fill]) -> dict[tuple[date, Any], list[Fill]]:
    out: dict[tuple[date, Any], list[Fill]] = defaultdict(list)
    for f in fills:
        out[(f.day, f.asset)].append(f)
    return out


def two_sided(fs: list[Fill]) -> bool:
    return any(f.quantity > 0 for f in fs) and any(f.quantity < 0 for f in fs)


def compare_b(formula: Result, exact: Result) -> dict[str, Any]:
    fa, fb = by_asset_day(formula.fills), by_asset_day(exact.fills)
    keys = set(fa) | set(fb)
    two = {k for k in keys if two_sided(fa.get(k, [])) or two_sided(fb.get(k, []))}
    mismatches: list[str] = []
    max_err = 0.0
    compared = 0
    for k in sorted(keys - two, key=lambda k: (k[0], str(k[1]))):
        xs, ys = fa.get(k, []), fb.get(k, [])
        if len(xs) != len(ys):
            mismatches.append(f"{k}: {len(xs)} formula fills vs {len(ys)} book-exact-test fills")
            continue
        for x, y in zip(xs, ys, strict=True):
            compared += 1
            err = abs(x.impact - y.impact)
            max_err = max(max_err, err)
            if (x.day, x.asset, x.quantity) != (y.day, y.asset, y.quantity) or err > TOL:
                mismatches.append(f"{k}: {x} vs {y}")
    return {
        "fills_formula": len(formula.fills),
        "fills_book_exact_test": len(exact.fills),
        "asset_days": len(keys),
        "two_sided_asset_days": len(two),
        "fills_on_two_sided_formula": sum(len(fa.get(k, [])) for k in two),
        "fills_on_two_sided_book_exact_test": sum(len(fb.get(k, [])) for k in two),
        "one_sided_fills_compared": compared,
        "max_abs_impact_err": max_err,
        "mismatches": len(mismatches),
        "first_mismatches": mismatches[:5],
        "pass": not mismatches,
    }


def summary_c(r: Result) -> dict[str, Any]:
    return {
        "total_return": r.summary["total_return"],
        "fills": len(r.fills),
        "total_impact": sum(f.impact for f in r.fills),
        "final_equity": float(r.equity_curve["equity"][-1]),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--start", default="2024-03-01")
    ap.add_argument("--end", default="2026-06-30")
    ap.add_argument("--root", type=Path, default=Path("."))
    ap.add_argument("--json", type=Path, default=None, help="also write the JSON result here")
    args = ap.parse_args()
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)

    wh = Warehouse(Paths(root=args.root))
    cm = CostModel(impact_enabled=True)
    t0 = time.perf_counter()
    prepared = PreparedMarket.prepare(wh, start, end, warmup_days=WARMUP)
    prep_s = time.perf_counter() - t0
    result: dict[str, Any] = {
        "date": date.today().isoformat(),
        "host": socket.gethostname(),
        **git_stamp(args.root),
        "window": [args.start, args.end],
        "cash": CASH,
        "warmup_days": WARMUP,
        "prepare_seconds": prep_s,
    }

    print("== (a) pricing equivalence: exact ladder through the book vs the formula")
    a = part_a(prepared, cm)
    result["a"] = a
    print(json.dumps(a, indent=2))
    if not a["pass"]:
        print(
            f"FAIL (a): max_abs_err {a['max_abs_err']!r} (gate {TOL}), "
            f"depth_mismatches {a['depth_mismatches']} (gate 0)"
        )
        print(json.dumps(result))
        return 1
    print("PASS (a)")

    def run(spec: NativeStrategySpec, execution: str) -> tuple[Result, float]:
        t = time.perf_counter()
        r = NativeBacktest(
            warehouse=wh,
            strategy=spec,
            cost_model=cm,
            start=start,
            end=end,
            initial_cash=CASH,
            warmup_days=WARMUP,
            prepared=prepared,
            execution=execution,
        ).run()
        return r, time.perf_counter() - t

    print("== (b) end to end: formula vs book-exact-test (ml-ranker, ml-ranker-v2 skipped)")
    result["b"] = {}
    result["c"] = {}
    ok_b = True
    for name, spec in STRATEGIES:
        formula, tf = run(spec, "formula")
        exact, te = run(spec, "book-exact-test")
        b = compare_b(formula, exact)
        b["seconds_formula"], b["seconds_book_exact_test"] = tf, te
        result["b"][name] = b
        ok_b &= b["pass"]
        print(f"{'PASS' if b['pass'] else 'FAIL'}  {name}: {json.dumps(b)}")
        book, tb = run(spec, "book")
        result["c"][name] = {
            "formula": summary_c(formula),
            "book": summary_c(book),
            "seconds_book": tb,
        }
    result["b_pass"] = ok_b

    print(
        "== (c) cent vs formula — capability check, default params, NOT a research "
        "result — Plan D owns the re-run"
    )
    print(
        "| strategy | formula return | book return | formula fills | book fills "
        "| formula impact $ | book impact $ |"
    )
    print("|---|---:|---:|---:|---:|---:|---:|")
    for name, c in result["c"].items():
        f, k = c["formula"], c["book"]
        print(
            f"| {name} | {f['total_return']:+.4%} | {k['total_return']:+.4%} | {f['fills']} "
            f"| {k['fills']} | {f['total_impact']:,.2f} | {k['total_impact']:,.2f} |"
        )

    line = json.dumps(result)
    if args.json is not None:
        args.json.write_text(json.dumps(result, indent=2) + "\n")
    print(line)
    return 0 if ok_b else 1


if __name__ == "__main__":
    sys.exit(main())
