"""Read-only data access for the API: registry index + artifact loading."""

from __future__ import annotations

from pathlib import Path

from pkmn_quant.research.runs import RunRecord, load_runs


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
    """One record by id; KeyError if unknown."""
    for r in load_runs(root):
        if r.run_id == run_id:
            return r
    raise KeyError(run_id)
