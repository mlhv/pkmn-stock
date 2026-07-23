"""Synthetic data root + app fixture for API tests."""

import json
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest
from fastapi.testclient import TestClient

from pkmn_quant.api import create_app


def _write_registry(root: Path, records: list[dict]) -> None:
    reg = root / "data" / "runs" / "registry.jsonl"
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in records))


def _wf_record(root: Path, run_id: str, strategy: str) -> dict:
    """A walkforward registry record + its artifact dir (json + parquet)."""
    art = root / "data" / "results" / f"wf-{strategy}-2025-01-01-2025-03-01"
    art.mkdir(parents=True, exist_ok=True)
    days = [date(2025, 1, 1) + timedelta(days=i) for i in range(80)]
    equity = [1000.0 * (1.0 + 0.0005 * i) for i in range(80)]
    pl.DataFrame({"date": days, "equity": equity}).write_parquet(art / "stitched_equity.parquet")
    (art / "walkforward.json").write_text(
        json.dumps(
            {
                "strategy": strategy,
                "folds": [
                    {
                        "is_start": "2025-01-01",
                        "is_end": "2025-01-20",
                        "oos_start": "2025-01-21",
                        "oos_end": "2025-01-31",
                        "params": {"top_n": 8},
                        "is_summary": {"total_return": 0.05, "cagr": 0.4},
                        "oos_summary": {"total_return": -0.01, "cagr": -0.1},
                    }
                ],
                "summary": {"stitched_total_return": -0.02, "stitched_cagr": -0.05},
                "rigor": {
                    "stitched_total_return_ci": {
                        "point": -0.02,
                        "lo": -0.09,
                        "hi": 0.04,
                        "level": 0.95,
                        "n_boot": 10000,
                        "mean_block": 10.0,
                        "seed": 42,
                    }
                },
            }
        )
    )
    return {
        "run_id": run_id,
        "recorded_at": "2026-07-01T00:00:00+00:00",
        "command": "walkforward",
        "strategy": strategy,
        "git_sha": "abc1234",
        "git_dirty": False,
        "config_hash": "deadbeef",
        "config": {"strategy": strategy, "trials": 15},
        "data_fingerprint": {"rows": 1000, "min_date": "2024-01-01", "max_date": "2025-03-01"},
        "results": {"stitched_total_return": -0.02},
        "artifact_path": str(art),
        "runtime": {"workers": 0, "workers_resolved": 4},
    }


def _evaluate_record(root: Path, run_id: str) -> dict:
    art = root / "data" / "results" / "evaluate-2026-07-01"
    art.mkdir(parents=True, exist_ok=True)
    (art / "evaluate.json").write_text(
        json.dumps(
            {
                "strategies": {
                    "sealed-accumulation": {
                        "total_return": -0.07,
                        "ci": {"point": -0.07, "lo": -0.21, "hi": 0.08, "level": 0.95},
                        "sharpe": -0.8,
                        "dsr": 0.008,
                    },
                    "ml-ranker": {
                        "total_return": -0.075,
                        "ci": {"point": -0.075, "lo": -0.20, "hi": 0.057, "level": 0.95},
                        "sharpe": -0.73,
                        "dsr": 0.007,
                    },
                },
                "reality_check_p": 1.0,
                "benchmark": "data/results/buy-and-hold-sealed-x",
                "n_days": 660,
                "start": "2024-08-28",
                "end": "2026-06-18",
                "params": {"n_boot": 10000, "mean_block": 10.0, "seed": 42},
            }
        )
    )
    return {
        "run_id": run_id,
        "recorded_at": "2026-07-02T00:00:00+00:00",
        "command": "evaluate",
        "strategy": "sealed-accumulation,ml-ranker",
        "git_sha": "abc1234",
        "git_dirty": False,
        "config_hash": "cafef00d",
        "config": {"n_boot": 10000},
        "data_fingerprint": {"rows": 1000, "min_date": "2024-01-01", "max_date": "2026-06-30"},
        "results": {"reality_check_p": 1.0},
        "artifact_path": str(art),
    }


@pytest.fixture
def seeded_root(tmp_path: Path) -> Path:
    _write_registry(
        tmp_path,
        [
            _wf_record(tmp_path, "20260701T000000Z-aaa111", "sealed-accumulation"),
            _evaluate_record(tmp_path, "20260702T000000Z-bbb222"),
        ],
    )
    return tmp_path


@pytest.fixture
def client(seeded_root: Path) -> TestClient:
    return TestClient(create_app(seeded_root))
