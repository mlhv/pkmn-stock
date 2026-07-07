import datetime as dt
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from typer.testing import CliRunner

import pkmn_quant.live.notify as notify
from pkmn_quant.cli import app
from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA
from pkmn_quant.data.warehouse import Warehouse
from tests.helpers import price_row


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(notify, "send_notification", lambda t, b: calls.append((t, b)))
    return calls


def seed(root: Path) -> None:
    """Product crashes 200 -> 100. released_on is set 181 days before the last
    seeded day (2025-05-01) so the product always satisfies the min_age_days
    constraint regardless of which params optuna picks (search space 30-180)."""
    w = Warehouse(Paths(root=root))
    start = date(2025, 1, 1)
    # released_on must be <= ctx.today - min_age_days for all possible params
    # (min_age_days up to 180); 2025-05-01 - 181 days = 2024-11-01.
    released_on = date(2024, 11, 1)
    for i in range(121):
        d = start + timedelta(days=i)
        price = 200.0 if i < 30 else 100.0
        w.write_prices(d, pl.DataFrame([price_row(d, 1, price)], schema=PRICE_SCHEMA))
    w.write_products(
        pl.DataFrame(
            {
                "product_id": [1],
                "group_id": [1],
                "name": ["Crashed Box"],
                "rarity": [None],
                "kind": ["sealed"],
                "released_on": [released_on],
            }
        )
    )


def run_walkforward(runner: CliRunner, root: Path) -> None:
    r = runner.invoke(
        app,
        [
            "walkforward",
            "--strategy",
            "sealed-accumulation",
            "--start",
            "2025-01-01",
            "--end",
            "2025-04-11",
            "--is-days",
            "30",
            "--oos-days",
            "30",
            "--trials",
            "2",
            "--cash",
            "1000",
            "--root",
            str(root),
        ],
    )
    assert r.exit_code == 0, r.output


def test_daily_writes_artifacts_and_notifies_when_actionable(
    tmp_path: Path, sent: list[tuple[str, str]]
) -> None:
    seed(tmp_path)
    runner = CliRunner()
    run_walkforward(runner, tmp_path)
    for args in (["portfolio", "deposit", "--amount", "1000", "--date", "2025-01-02"],):
        r = runner.invoke(app, [*args, "--root", str(tmp_path)])
        assert r.exit_code == 0, r.output

    result = runner.invoke(app, ["daily", "--skip-ingest", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output

    daily_dirs = sorted((tmp_path / "data" / "results").glob("daily-*"))
    assert len(daily_dirs) == 1
    meta = json.loads((daily_dirs[0] / "daily.json").read_text())
    assert meta["status"] == "ok"
    assert meta["strategy"] == "sealed-accumulation"
    assert meta["as_of"] == "2025-05-01"
    assert meta["n_buys"] >= 1  # the crashed box qualifies for entry
    assert (daily_dirs[0] / "signals.md").exists()
    assert (daily_dirs[0] / "signals.json").exists()
    assert len(sent) == 1  # actionable -> exactly one notification


def test_daily_silent_when_nothing_actionable(tmp_path: Path, sent: list[tuple[str, str]]) -> None:
    """No cash in the ledger -> no affordable entries -> no notification."""
    seed(tmp_path)
    runner = CliRunner()
    run_walkforward(runner, tmp_path)
    result = runner.invoke(app, ["daily", "--skip-ingest", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    meta = json.loads(next((tmp_path / "data" / "results").glob("daily-*/daily.json")).read_text())
    assert meta["status"] == "ok" and meta["n_buys"] == 0 and meta["n_sells"] == 0
    assert sent == []


def test_daily_failure_writes_error_status_and_notifies(
    tmp_path: Path, sent: list[tuple[str, str]]
) -> None:
    """No walk-forward artifact -> SignalsError -> status error, nonzero exit."""
    seed(tmp_path)
    result = CliRunner().invoke(app, ["daily", "--skip-ingest", "--root", str(tmp_path)])
    assert result.exit_code != 0
    assert result.exception is None or isinstance(result.exception, SystemExit)
    meta = json.loads(next((tmp_path / "data" / "results").glob("daily-*/daily.json")).read_text())
    assert meta["status"] == "error"
    assert "walkforward" in meta["error"] or "walk-forward" in meta["error"]
    assert len(sent) == 1


# ---------------------------------------------------------------------------
# Item 5a: ingest path — boundary test
# ---------------------------------------------------------------------------


def test_daily_calls_ingest_when_warehouse_is_stale(
    tmp_path: Path,
    sent: list[tuple[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the warehouse's last stored day is old, daily must call ingest_range
    with start = last_day+1 and end = yesterday."""
    seed(tmp_path)
    runner = CliRunner()
    run_walkforward(runner, tmp_path)
    # Deposit so signals succeed (portfolio mode needs cash)
    deposit_args = ["portfolio", "deposit", "--amount", "1000", "--date", "2025-01-02"]
    r = runner.invoke(app, [*deposit_args, "--root", str(tmp_path)])
    assert r.exit_code == 0, r.output

    ingest_calls: list[tuple[Any, ...]] = []

    def fake_ingest(paths: Any, start: date, end: date) -> list[Any]:
        ingest_calls.append((paths, start, end))
        return []

    monkeypatch.setattr("pkmn_quant.cli.ingest_range", fake_ingest)

    # The warehouse's last day is 2025-05-01 (seeded).  yesterday is computed
    # at runtime; we compute it the same way here to keep the assertion exact.
    yesterday = dt.date.today() - dt.timedelta(days=1)
    expected_start = date(2025, 5, 2)  # days[-1] + 1 day

    result = runner.invoke(app, ["daily", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output

    assert len(ingest_calls) == 1, f"expected 1 ingest call, got {len(ingest_calls)}"
    _, call_start, call_end = ingest_calls[0]
    assert call_start == expected_start
    assert call_end == yesterday


# ---------------------------------------------------------------------------
# Item 5b: ingest failure test
# ---------------------------------------------------------------------------


def test_daily_ingest_failure_exits_1_and_records_error(
    tmp_path: Path,
    sent: list[tuple[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ingest_range raises, daily must exit 1 and record status error;
    signals.md should still be written (signals ran despite ingest failure)
    and the notification body should mention the ingest error."""
    seed(tmp_path)
    runner = CliRunner()
    run_walkforward(runner, tmp_path)
    deposit_args = ["portfolio", "deposit", "--amount", "1000", "--date", "2025-01-02"]
    r = runner.invoke(app, [*deposit_args, "--root", str(tmp_path)])
    assert r.exit_code == 0, r.output

    def fake_ingest_fail(paths: Any, start: date, end: date) -> list[Any]:
        raise RuntimeError("boom")

    monkeypatch.setattr("pkmn_quant.cli.ingest_range", fake_ingest_fail)

    result = runner.invoke(app, ["daily", "--root", str(tmp_path)])
    assert result.exit_code == 1

    meta = json.loads(next((tmp_path / "data" / "results").glob("daily-*/daily.json")).read_text())
    assert meta["status"] == "error"
    assert "ingest failed" in meta["error"]

    # signals.md is present — signals ran successfully despite the ingest error
    daily_dir = next((tmp_path / "data" / "results").glob("daily-*"))
    assert (daily_dir / "signals.md").exists()

    # At least one notification must mention the ingest problem
    ingest_notifs = [t for t, b in sent if "ingest" in t.lower() or "ingest" in b.lower()]
    assert len(ingest_notifs) >= 1
