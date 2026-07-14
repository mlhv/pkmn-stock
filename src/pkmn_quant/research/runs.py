"""Experiment tracking: append-only JSONL registry of research runs.

Every completed `pkmn backtest` / `pkmn walkforward` appends one record so
any number in the findings doc is reproducible from its config hash + data
fingerprint (optuna is seeded, so same hash + same data => same results).
Recording never fails a run: bookkeeping errors warn on stderr and the
research result survives.

Naming note: research/registry.py is the STRATEGY registry; this module is
the RUN registry, named to match the `pkmn runs` CLI.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pkmn_quant.data.warehouse import Warehouse


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    recorded_at: str
    command: str
    strategy: str
    git_sha: str | None
    git_dirty: bool
    config_hash: str
    config: dict[str, Any]
    data_fingerprint: dict[str, Any]
    results: dict[str, float]
    artifact_path: str


def registry_path(root: Path) -> Path:
    return root / "data" / "runs" / "registry.jsonl"


def config_hash(config: dict[str, Any]) -> str:
    """SHA-256 of the canonical serialization: sorted keys, no whitespace."""
    canonical = json.dumps(config, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def git_info(root: Path) -> tuple[str | None, bool]:
    """(HEAD sha, dirty flag); (None, True) when git is unavailable."""
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, check=True
        ).stdout
        return sha, bool(status.strip())
    except (OSError, subprocess.CalledProcessError):
        return None, True


def data_fingerprint(warehouse: Warehouse) -> dict[str, Any]:
    """Cheap identity of the price data a run saw: date range + row count."""
    row = warehouse.query(
        'SELECT min("date") AS min_date, max("date") AS max_date, count(*) AS n_rows FROM prices'
    ).row(0, named=True)
    return {
        "min_date": str(row["min_date"]),
        "max_date": str(row["max_date"]),
        "rows": int(row["n_rows"]),
    }


def record_run(
    root: Path,
    command: str,
    strategy: str,
    config: dict[str, Any],
    results: dict[str, float],
    artifact_path: Path,
    warehouse: Warehouse,
) -> str | None:
    """Append one record; returns run_id, or None after warning. Never raises."""
    try:
        now = datetime.now(UTC)
        run_id = now.strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)
        sha, dirty = git_info(root)
        record = {
            "run_id": run_id,
            "recorded_at": now.isoformat(),
            "command": command,
            "strategy": strategy,
            "git_sha": sha,
            "git_dirty": dirty,
            "config_hash": config_hash(config),
            "config": config,
            "data_fingerprint": data_fingerprint(warehouse),
            "results": results,
            "artifact_path": str(artifact_path),
        }
        path = registry_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
        return run_id
    except Exception as exc:  # bookkeeping must never kill a research run
        print(f"warning: run tracking failed ({exc}); results are unaffected", file=sys.stderr)
        return None


def load_runs(root: Path) -> list[RunRecord]:
    """Parse the registry, oldest first. Missing file = []."""
    path = registry_path(root)
    if not path.is_file():
        return []
    records: list[RunRecord] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        records.append(RunRecord(**json.loads(line)))
    return records
