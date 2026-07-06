"""Machine-readable walk-forward artifacts: the bridge from research to live.

walkforward.json schema:
{"strategy": str,
 "folds": [{"is_start": "YYYY-MM-DD", "is_end": ..., "oos_start": ..., "oos_end": ...,
            "params": {name: number}, "is_summary": {...}, "oos_summary": {...}}],
 "summary": {metric: number}}
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pkmn_quant.research.walkforward import WalkForwardResult

Params = dict[str, float | int]


@dataclass(frozen=True)
class FoldRecord:
    is_start: str
    is_end: str
    oos_start: str
    oos_end: str
    params: Params
    is_summary: dict[str, float]
    oos_summary: dict[str, float]


@dataclass(frozen=True)
class WalkForwardRun:
    strategy: str
    folds: list[FoldRecord]
    summary: dict[str, float]


def write_walkforward_json(run_dir: Path, result: WalkForwardResult, strategy_name: str) -> None:
    payload = {
        "strategy": strategy_name,
        "folds": [
            {
                "is_start": f.fold.is_start.isoformat(),
                "is_end": f.fold.is_end.isoformat(),
                "oos_start": f.fold.oos_start.isoformat(),
                "oos_end": f.fold.oos_end.isoformat(),
                "params": f.params,
                "is_summary": f.is_summary,
                "oos_summary": f.oos_summary,
            }
            for f in result.folds
        ],
        "summary": result.summary,
    }
    (run_dir / "walkforward.json").write_text(json.dumps(payload, indent=2) + "\n")


def load_walkforward_json(run_dir: Path) -> WalkForwardRun:
    raw = json.loads((run_dir / "walkforward.json").read_text())
    return WalkForwardRun(
        strategy=str(raw["strategy"]),
        folds=[
            FoldRecord(
                is_start=str(f["is_start"]),
                is_end=str(f["is_end"]),
                oos_start=str(f["oos_start"]),
                oos_end=str(f["oos_end"]),
                params=dict(f["params"]),
                is_summary={str(k): float(v) for k, v in f["is_summary"].items()},
                oos_summary={str(k): float(v) for k, v in f["oos_summary"].items()},
            )
            for f in raw["folds"]
        ],
        summary={str(k): float(v) for k, v in raw["summary"].items()},
    )


def find_latest_wf_run(results_dir: Path, strategy: str) -> Path | None:
    """Latest run dir for a strategy: lexicographically last wf-{strategy}-* dir
    containing walkforward.json. Run dirs embed ISO dates, so lexicographic ==
    chronological for a fixed strategy prefix."""
    if not results_dir.exists():
        return None
    candidates = sorted(
        p
        for p in results_dir.iterdir()
        if p.is_dir() and p.name.startswith(f"wf-{strategy}-") and (p / "walkforward.json").exists()
    )
    return candidates[-1] if candidates else None
