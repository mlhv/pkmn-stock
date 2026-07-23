"""Read-only data access for the API: registry index + artifact loading."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import polars as pl

from pkmn_quant.research.runs import RunRecord, load_runs


class NotFound(Exception):
    """A run, artifact directory, or artifact file that doesn't exist.

    `str()` is the clean, human-readable detail message the API handlers
    pass straight through to `HTTPException(status_code=404, detail=...)` —
    never a filesystem path, never a raw exception repr.
    """


def list_runs(
    root: Path, command: str | None = None, strategy: str | None = None
) -> list[RunRecord]:
    """Registry records, newest first, optionally filtered."""
    runs = load_runs(root)
    if command is not None:
        runs = [r for r in runs if r.command == command]
    if strategy is not None:
        runs = [r for r in runs if r.strategy == strategy]
    return sorted(runs, key=lambda r: r.recorded_at, reverse=True)


def get_run(root: Path, run_id: str) -> RunRecord:
    """One record by id; NotFound if unknown."""
    for r in load_runs(root):
        if r.run_id == run_id:
            return r
    raise NotFound(f"unknown run_id: {run_id}")


def _artifact_dir(root: Path, run_id: str, expect_command: str) -> Path:
    r = get_run(root, run_id)  # raises NotFound -> handler maps to 404
    if r.command != expect_command:
        raise NotFound(f"{run_id} is a {r.command} run, not {expect_command}")
    art = Path(r.artifact_path)
    if not art.exists():
        raise NotFound(f"artifact for {run_id} is missing")
    return art


def load_walkforward(root: Path, run_id: str) -> dict[str, Any]:
    """Raw walkforward.json (carries the rigor block, unlike WalkForwardRun)
    plus the stitched equity curve. Returns a plain dict the handler shapes."""
    art = _artifact_dir(root, run_id, "walkforward")
    try:
        raw: dict[str, Any] = json.loads((art / "walkforward.json").read_text())
        curve = pl.read_parquet(art / "stitched_equity.parquet").sort("date")
    except FileNotFoundError:
        raise NotFound(f"artifact for {run_id} is missing") from None
    raw["equity_curve"] = [
        {"date": d.isoformat(), "equity": float(e)}
        for d, e in zip(curve["date"].to_list(), curve["equity"].to_list(), strict=True)
    ]
    return raw


def load_evaluate(root: Path, run_id: str) -> dict[str, Any]:
    art = _artifact_dir(root, run_id, "evaluate")
    try:
        result: dict[str, Any] = json.loads((art / "evaluate.json").read_text())
    except FileNotFoundError:
        raise NotFound(f"artifact for {run_id} is missing") from None
    return result


def strategy_catalog() -> list[dict[str, Any]]:
    from pkmn_quant.live.report import THESIS
    from pkmn_quant.research.registry import REGISTRY

    return [{"name": n, "thesis": THESIS.get(n, "")} for n in sorted(REGISTRY)]
