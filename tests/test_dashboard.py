"""Headless dashboard smoke tests (streamlit.testing.v1.AppTest).

Run with the dashboard group: uv run --group dashboard pytest tests/test_dashboard.py
Under plain `uv run pytest` these skip (streamlit not installed).
"""

import json
from pathlib import Path

import pytest

from tests.test_cli_daily import seed

apptest_mod = pytest.importorskip("streamlit.testing.v1", reason="dashboard group not installed")
AppTest = apptest_mod.AppTest

DASHBOARD = str(Path(__file__).resolve().parents[1] / "app" / "dashboard.py")


def _write_paper_ledger(root: Path) -> None:
    p = root / "data" / "portfolio" / "paper.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        '{"date": "2025-01-02", "kind": "deposit", "amount": 1000.0}\n'
        '{"date": "2025-01-03", "kind": "buy", "product_id": 1, "sub_type": "Normal",'
        ' "qty": 2, "price": 35.0, "fees": 1.0}\n'
    )


def _write_real_ledger(root: Path) -> None:
    p = root / "data" / "portfolio" / "ledger.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        '{"date": "2025-01-02", "kind": "deposit", "amount": 1000.0}\n'
        '{"date": "2025-01-03", "kind": "buy", "product_id": 1, "sub_type": "Normal",'
        ' "qty": 2, "price": 35.0, "fees": 1.0}\n'
    )


def test_paper_toggle_renders_paper_holdings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Toggle to Paper: the seeded paper position renders without error."""
    seed(tmp_path)  # warehouse: product 1 "Crashed Box", prices through 2025-05-01
    _write_paper_ledger(tmp_path)
    monkeypatch.chdir(tmp_path)  # dashboard resolves ROOT = Path(".") against cwd
    at = AppTest.from_file(DASHBOARD, default_timeout=30).run()
    assert not at.exception
    [ledger_radio] = [r for r in at.radio if r.label == "Ledger"]
    at = ledger_radio.set_value("Paper").run()
    assert not at.exception
    assert any(
        "product" in df.value.columns and "Crashed Box" in df.value["product"].values
        for df in at.dataframe
    )


def test_real_mode_renders_holdings_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No toggle: the default Real selection renders a seeded real ledger
    through the same replay -> snapshot -> chart path the refactor touched."""
    seed(tmp_path)
    _write_real_ledger(tmp_path)
    monkeypatch.chdir(tmp_path)
    at = AppTest.from_file(DASHBOARD, default_timeout=30).run()
    assert not at.exception
    assert any(
        "product" in df.value.columns and "Crashed Box" in df.value["product"].values
        for df in at.dataframe
    )


def test_paper_empty_state_shows_deposit_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No paper ledger: toggling to Paper shows the --paper hint, no crash."""
    seed(tmp_path)
    monkeypatch.chdir(tmp_path)
    at = AppTest.from_file(DASHBOARD, default_timeout=30).run()
    [ledger_radio] = [r for r in at.radio if r.label == "Ledger"]
    at = ledger_radio.set_value("Paper").run()
    assert not at.exception
    assert any("--paper" in str(i.value) for i in at.info)


def test_benchmark_runs_excludes_non_benchmark_backtests(tmp_path, monkeypatch) -> None:
    """A triggered sealed-accumulation run must not appear in the benchmark
    overlay selector just because it has an equity.parquet."""
    import app.dashboard as dash

    results = tmp_path / "data" / "results"
    for name in ("20260727T100000Z-aaa111", "20260727T100001Z-bbb222"):
        (results / name).mkdir(parents=True)
        (results / name / "equity.parquet").write_bytes(b"")
    reg = tmp_path / "data" / "runs" / "registry.jsonl"
    reg.parent.mkdir(parents=True)
    rows = [
        {
            "run_id": "20260727T100000Z-aaa111",
            "recorded_at": "2026-07-27T10:00:00+00:00",
            "command": "backtest",
            "strategy": "sealed-accumulation",
            "git_sha": None,
            "git_dirty": False,
            "config_hash": "x",
            "config": {},
            "data_fingerprint": {},
            "results": {},
            "artifact_path": str(results / "20260727T100000Z-aaa111"),
        },
        {
            "run_id": "20260727T100001Z-bbb222",
            "recorded_at": "2026-07-27T10:00:01+00:00",
            "command": "backtest",
            "strategy": "buy-and-hold-sealed",
            "git_sha": None,
            "git_dirty": False,
            "config_hash": "y",
            "config": {},
            "data_fingerprint": {},
            "results": {},
            "artifact_path": str(results / "20260727T100001Z-bbb222"),
        },
    ]
    reg.write_text("".join(json.dumps(r) + "\n" for r in rows))
    monkeypatch.setattr(dash, "ROOT", tmp_path)
    monkeypatch.setattr(dash, "RESULTS", results)

    names = [p.name for p in dash.benchmark_runs()]
    assert names == ["20260727T100001Z-bbb222"]
