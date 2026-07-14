import json
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA
from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.research.runs import (
    config_hash,
    load_runs,
    record_run,
    registry_path,
)
from tests.helpers import price_row

D1 = date(2025, 6, 1)


@pytest.fixture
def warehouse(tmp_path: Path) -> Warehouse:
    w = Warehouse(Paths(root=tmp_path))
    w.write_prices(D1, pl.DataFrame([price_row(D1, 1, 10.0)], schema=PRICE_SCHEMA))
    return w


def test_config_hash_is_key_order_independent() -> None:
    a = {"start": "2024-03-01", "end": "2026-06-30", "trials": 15}
    b = {"trials": 15, "end": "2026-06-30", "start": "2024-03-01"}
    assert config_hash(a) == config_hash(b)
    assert config_hash(a) != config_hash({**a, "trials": 16})


def test_record_and_load_round_trip(tmp_path: Path, warehouse: Warehouse) -> None:
    run_id = record_run(
        root=tmp_path,
        command="backtest",
        strategy="buy-and-hold-sealed",
        config={"start": "2025-06-01", "end": "2025-06-03"},
        results={"total_return": 0.23},
        artifact_path=tmp_path / "data" / "results" / "x",
        warehouse=warehouse,
    )
    assert run_id is not None
    records = load_runs(tmp_path)
    assert len(records) == 1
    r = records[0]
    assert r.run_id == run_id
    assert r.command == "backtest"
    assert r.strategy == "buy-and-hold-sealed"
    assert r.results == {"total_return": 0.23}
    assert r.config_hash == config_hash({"start": "2025-06-01", "end": "2025-06-03"})
    assert r.data_fingerprint == {"min_date": "2025-06-01", "max_date": "2025-06-01", "rows": 1}
    # tmp_path is not a git repo -> unknown sha, treated as dirty.
    assert r.git_sha is None
    assert r.git_dirty is True


def test_recording_failure_warns_but_never_raises(
    tmp_path: Path, warehouse: Warehouse, capsys: pytest.CaptureFixture[str]
) -> None:
    # Make the registry path unwritable: create it as a DIRECTORY.
    registry_path(tmp_path).mkdir(parents=True)
    run_id = record_run(
        root=tmp_path,
        command="backtest",
        strategy="s",
        config={},
        results={},
        artifact_path=tmp_path,
        warehouse=warehouse,
    )
    assert run_id is None
    assert "run tracking failed" in capsys.readouterr().err


def test_load_runs_missing_file_is_empty(tmp_path: Path) -> None:
    assert load_runs(tmp_path) == []


def _record_dict(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "run_id": "20250601T000000Z-abcdef",
        "recorded_at": "2025-06-01T00:00:00+00:00",
        "command": "backtest",
        "strategy": "buy-and-hold-sealed",
        "git_sha": None,
        "git_dirty": True,
        "config_hash": "deadbeef",
        "config": {},
        "data_fingerprint": {},
        "results": {"total_return": 0.1},
        "artifact_path": "data/results/x",
    }
    base.update(overrides)
    return base


def test_load_runs_ignores_unknown_extra_key(tmp_path: Path) -> None:
    path = registry_path(tmp_path)
    path.parent.mkdir(parents=True)
    record = _record_dict(future_field="added-in-a-later-version")
    path.write_text(json.dumps(record) + "\n")

    records = load_runs(tmp_path)

    assert len(records) == 1
    assert records[0].run_id == "20250601T000000Z-abcdef"
    assert not hasattr(records[0], "future_field")


def test_load_runs_skips_corrupt_line_with_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = registry_path(tmp_path)
    path.parent.mkdir(parents=True)
    good = _record_dict(run_id="good-1")
    missing_key = _record_dict(run_id="bad-missing")
    del missing_key["results"]
    lines = [
        json.dumps(good),
        "{not valid json",
        json.dumps(missing_key),
    ]
    path.write_text("\n".join(lines) + "\n")

    records = load_runs(tmp_path)

    assert [r.run_id for r in records] == ["good-1"]
    err = capsys.readouterr().err
    assert "line 2" in err
    assert "line 3" in err
