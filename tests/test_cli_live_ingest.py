from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from pkmn_quant.cli import app
from pkmn_quant.config import Paths
from pkmn_quant.data.ingest import IngestStats
from pkmn_quant.data.live import Capture, LiveIngestError, LiveIngestReport


def _report(failed: tuple[int, ...], written: tuple[date, ...]) -> LiveIngestReport:
    day = date(2026, 10, 24)
    capture = Capture(
        day=day,
        update_ts=datetime(2026, 10, 24, 20, 5, 44, tzinfo=UTC),
        held=(1, 2, 3),
        absent=(4,),
        failed=failed,
        moved_on=False,
        collision=False,
    )
    return LiveIngestReport(
        capture=capture,
        written=tuple(IngestStats(day=d, rows_clean=10, rows_quarantined=1) for d in written),
        partial={},
        collision=False,
        catalog_failures=(),
    )


def _patch(monkeypatch: pytest.MonkeyPatch, outcome: LiveIngestReport | Exception) -> list[Paths]:
    calls: list[Paths] = []

    def fake(paths: Paths, *args: Any, **kwargs: Any) -> LiveIngestReport:
        calls.append(paths)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr("pkmn_quant.cli.run_live_ingest", fake)
    return calls


def test_live_ingest_command_prints_counts_and_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = CliRunner()

    calls = _patch(monkeypatch, _report(failed=(), written=(date(2026, 10, 24),)))
    complete = runner.invoke(app, ["live-ingest", "--root", str(tmp_path)])
    assert complete.exit_code == 0, complete.output
    assert calls[0].root == tmp_path
    assert "update day: 2026-10-24" in complete.output
    assert "held 3, absent 1, failed 0" in complete.output
    assert "2026-10-24: 10 clean rows, 1 quarantined" in complete.output

    _patch(monkeypatch, _report(failed=(7, 9), written=()))
    incomplete = runner.invoke(app, ["live-ingest", "--root", str(tmp_path)])
    assert incomplete.exit_code == 1
    assert "held 3, absent 1, failed 2" in incomplete.output
    assert "7, 9" in incomplete.output
    assert "no day written" in incomplete.output


def test_live_ingest_command_already_stored_and_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = CliRunner()
    _patch(monkeypatch, LiveIngestReport(None, (), {}, False, ()))
    stored = runner.invoke(app, ["live-ingest", "--root", str(tmp_path)])
    assert stored.exit_code == 0, stored.output
    assert "already stored" in stored.output

    _patch(monkeypatch, LiveIngestError("last-updated.txt: bad body"))
    failed = runner.invoke(app, ["live-ingest", "--root", str(tmp_path)])
    assert failed.exit_code == 1
    assert "bad body" in failed.output
