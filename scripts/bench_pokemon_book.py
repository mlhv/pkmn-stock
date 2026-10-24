"""Measured cost of book execution (Plan B Task 7), macOS host.

Book execution fills against modelled resting orders (synthetic, built from
the day's tcgcsv quote). Three measurements, all wall-clock on this host:

1. Encode: ``pkmn wire encode`` over the whole warehouse (2024-02-08 to the
   last ingested day) into data/wire/full.pkw; messages and bytes from the
   command's own output, wall-clock around the subprocess (includes
   interpreter start-up and the warehouse load).
2. Backtest cost: buy-and-hold and dip-buyer over 2024-03-01..2026-06-30
   ($10,000, impact on, warmup 0, default params), formula vs book, 3
   interleaved runs each (F B F B F B), best of 3. Two figures per run:
   "total" is NativeBacktest.run() loading and preparing the market itself
   (what a CLI run pays), "engine" is the same run handed one shared
   PreparedMarket (the walk-forward's situation), so it isolates the C++ run
   plus result repackaging.
3. Walk-forward cost: one ``pkmn walkforward --strategy sealed-accumulation
   --trials 15 --workers 1`` in book and once in formula, under
   ``caffeinate -dimsu``. n=1 each: no spread, no best-of. They run with
   ``--root`` at a scratch directory whose data/warehouse is a symlink to the
   real warehouse, so their artifacts and registry records never land in the
   real data/results or data/runs (nothing there can confuse a later
   ``pkmn evaluate``); the scratch directory is reported.

    uv run python scripts/bench_pokemon_book.py [--skip-encode] [--skip-backtest]
        [--skip-walkforward] [--json PATH]

Prints a JSON block last (git SHA + dirty flag, date, host).
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import socket
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path
from typing import Any

from pkmn_quant.config import Paths
from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.costs import CostModel
from pkmn_quant.engine.native import NativeBacktest, NativeStrategySpec
from pkmn_quant.engine.prepared import PreparedMarket

ROOT = Path(__file__).resolve().parent.parent
START, END = date(2024, 3, 1), date(2026, 6, 30)
CASH = 10_000.0
WARMUP = 0
REPS = 3
CASES = [
    ("buy-and-hold", NativeStrategySpec("buy-and-hold", {}, kind="sealed")),
    ("dip-buyer", NativeStrategySpec("dip-buyer", {})),
]


def git_stamp() -> dict[str, Any]:
    def git(*a: str) -> str:
        return subprocess.run(
            ["git", *a], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()

    return {"git_sha": git("rev-parse", "HEAD"), "git_dirty": bool(git("status", "--porcelain"))}


def host() -> dict[str, Any]:
    chip = subprocess.run(
        ["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True
    ).stdout.strip()
    return {
        "hostname": socket.gethostname(),
        "machine": chip or platform.machine(),
        "os": platform.platform(),
        "python": platform.python_version(),
    }


def bench_encode(wh: Warehouse) -> dict[str, Any]:
    first, last = wh.query("select min(date) as a, max(date) as b from prices").row(0)
    out = ROOT / "data" / "wire" / "full.pkw"
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "uv", "run", "pkmn", "wire", "encode",
        "--start", first.isoformat(), "--end", last.isoformat(), "--out", str(out),
    ]  # fmt: skip
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=True)
    wall = time.perf_counter() - t0

    def field(name: str) -> str:
        m = re.search(rf"^{name}: (\S+)$", proc.stdout, re.M)
        if m is None:
            raise RuntimeError(f"no {name!r} in encode output:\n{proc.stdout}")
        return m.group(1)

    return {
        "command": " ".join(cmd[2:]),
        "start": first.isoformat(),
        "end": last.isoformat(),
        "messages": int(field("messages")),
        "bytes": int(field("bytes")),
        "encode_reported_seconds": float(field("seconds")),
        "wall_seconds": wall,
    }


def bench_backtest(wh: Warehouse) -> dict[str, Any]:
    cm = CostModel(impact_enabled=True)
    shared = PreparedMarket.prepare(wh, START, END, warmup_days=WARMUP)

    def one(spec: NativeStrategySpec, execution: str, prepared: PreparedMarket | None) -> float:
        t0 = time.perf_counter()
        NativeBacktest(
            warehouse=wh,
            strategy=spec,
            cost_model=cm,
            start=START,
            end=END,
            initial_cash=CASH,
            warmup_days=WARMUP,
            prepared=prepared,
            execution=execution,
        ).run()
        return time.perf_counter() - t0

    out: dict[str, Any] = {}
    for name, spec in CASES:
        res: dict[str, Any] = {}
        for label, prepared in (("total", None), ("engine", shared)):
            times: dict[str, list[float]] = {"formula": [], "book": []}
            for _ in range(REPS):
                for execution in ("formula", "book"):
                    times[execution].append(one(spec, execution, prepared))
            res[label] = {
                "runs_s": times,
                "best_s": {k: min(v) for k, v in times.items()},
                "book_over_formula_best": min(times["book"]) / min(times["formula"]),
            }
        out[name] = res
    return out


def bench_walkforward() -> dict[str, Any]:
    scratch = Path(tempfile.mkdtemp(prefix="pkmn-bench-wf-"))
    (scratch / "data").mkdir()
    (scratch / "data" / "warehouse").symlink_to(ROOT / "data" / "warehouse")
    out: dict[str, Any] = {"scratch_root": str(scratch), "n": 1}
    for execution in ("book", "formula"):
        cmd = [
            "caffeinate", "-dimsu", "uv", "run", "pkmn", "walkforward",
            "--strategy", "sealed-accumulation",
            "--start", START.isoformat(), "--end", END.isoformat(),
            "--trials", "15", "--workers", "1",
            "--execution", execution, "--root", str(scratch),
        ]  # fmt: skip
        t0 = time.perf_counter()
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        wall = time.perf_counter() - t0
        if proc.returncode != 0:
            raise RuntimeError(f"walkforward {execution} failed:\n{proc.stdout}\n{proc.stderr}")
        out[execution] = {
            "command": " ".join(cmd[cmd.index("pkmn") :]),
            "wall_seconds": wall,
            "stdout_tail": proc.stdout.strip().splitlines()[-12:],
        }
    out["book_over_formula"] = out["book"]["wall_seconds"] / out["formula"]["wall_seconds"]
    out["artifacts"] = sorted(str(p.relative_to(scratch)) for p in (scratch / "data").glob("*/*"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--skip-encode", action="store_true")
    ap.add_argument("--skip-backtest", action="store_true")
    ap.add_argument("--skip-walkforward", action="store_true")
    ap.add_argument("--json", type=Path, default=None, help="also write the JSON result here")
    args = ap.parse_args()

    wh = Warehouse(Paths(root=ROOT))
    result: dict[str, Any] = {"date": date.today().isoformat(), **git_stamp(), "host": host()}
    if not args.skip_encode:
        print("== encode", file=sys.stderr)
        result["encode"] = bench_encode(wh)
        print(json.dumps(result["encode"]), file=sys.stderr)
    if not args.skip_backtest:
        print("== backtest cost (interleaved, best of 3)", file=sys.stderr)
        result["backtest"] = bench_backtest(wh)
        print(json.dumps(result["backtest"]), file=sys.stderr)
    if not args.skip_walkforward:
        print("== walk-forward cost (n=1 each)", file=sys.stderr)
        result["walkforward"] = bench_walkforward()
    text = json.dumps(result, indent=2)
    if args.json is not None:
        args.json.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
