"""Headless dashboard smoke tests (streamlit.testing.v1.AppTest).

Run with the dashboard group: uv run --group dashboard pytest tests/test_dashboard.py
Under plain `uv run pytest` these skip (streamlit not installed).
"""

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
