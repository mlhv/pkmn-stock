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


def find_benchmark(root: Path, run_id: str) -> RunRecord | None:
    """The paired buy-and-hold run, located by the `benchmark_for` tag its
    config carries. Returns None for runs predating the pairing (Plan 2c) or
    for a buy-and-hold-sealed run, which is its own benchmark."""
    for r in load_runs(root):
        if r.command == "backtest" and r.config.get("benchmark_for") == run_id:
            return r
    return None


def _equity_points(art: Path) -> list[dict[str, Any]]:
    try:
        curve = pl.read_parquet(art / "equity.parquet").sort("date")
    except FileNotFoundError:
        raise NotFound("equity artifact is missing") from None
    return [
        {"date": d.isoformat(), "equity": float(e)}
        for d, e in zip(curve["date"].to_list(), curve["equity"].to_list(), strict=True)
    ]


def _seeded_assets(config: dict[str, Any]) -> set[tuple[int, str]]:
    return {(h["product_id"], h["sub_type"]) for h in config.get("holdings") or []}


def _fills_frame(art: Path) -> pl.DataFrame:
    try:
        return pl.read_parquet(art / "fills.parquet").sort("day")
    except FileNotFoundError:
        raise NotFound("fills artifact is missing") from None


def load_backtest(root: Path, run_id: str) -> dict[str, Any]:
    """Detail payload: config echo, summary, equity curve, paired benchmark
    curve (or None), fill count, and the days seeded positions were sold."""
    art = _artifact_dir(root, run_id, "backtest")
    record = get_run(root, run_id)
    fills = _fills_frame(art)
    seeded = _seeded_assets(record.config)
    exits = sorted(
        {
            d.isoformat()
            for d, pid, sub, qty in zip(
                fills["day"].to_list(),
                fills["product_id"].to_list(),
                fills["sub_type"].to_list(),
                fills["quantity"].to_list(),
                strict=True,
            )
            if qty < 0 and (pid, sub) in seeded
        }
    )
    bench = find_benchmark(root, run_id)
    benchmark = None
    if bench is not None:
        bench_art = Path(bench.artifact_path)
        if bench_art.exists():
            benchmark = {
                "run_id": bench.run_id,
                "strategy": bench.strategy,
                "equity_curve": _equity_points(bench_art),
            }
    return {
        "run_id": run_id,
        "strategy": record.strategy,
        "config": record.config,
        "summary": record.results,
        "equity_curve": _equity_points(art),
        "benchmark": benchmark,
        "n_fills": fills.height,
        "seeded_exits": exits,
    }


def load_fills(root: Path, run_id: str, offset: int, limit: int) -> dict[str, Any]:
    art = _artifact_dir(root, run_id, "backtest")
    record = get_run(root, run_id)
    seeded = _seeded_assets(record.config)
    fills = _fills_frame(art)
    page = fills.slice(offset, limit)
    rows = [
        {
            "day": r["day"].isoformat(),
            "product_id": r["product_id"],
            "sub_type": r["sub_type"],
            "quantity": r["quantity"],
            "price": r["price"],
            "fees": r["fees"],
            "impact": r["impact"],
            "seeded": r["quantity"] < 0 and (r["product_id"], r["sub_type"]) in seeded,
        }
        for r in page.iter_rows(named=True)
    ]
    return {
        "run_id": run_id,
        "offset": offset,
        "limit": limit,
        "n_fills": fills.height,
        "fills": rows,
    }


def strategy_catalog() -> list[dict[str, Any]]:
    from pkmn_quant.live.report import THESIS
    from pkmn_quant.research.registry import REGISTRY

    return [{"name": n, "thesis": THESIS.get(n, "")} for n in sorted(REGISTRY)]
