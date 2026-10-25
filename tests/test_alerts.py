from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from pkmn_quant.live.alerts import (
    AlertState,
    capture_at_risk,
    is_stale,
    load_state,
    resolve,
    save_state,
    should_alert,
)


def test_first_alert_then_suppressed_then_daily() -> None:
    state = AlertState()
    t0 = datetime(2026, 10, 25, 9, 0, tzinfo=UTC)
    assert should_alert(state, "capture", t0)
    assert not should_alert(state, "capture", t0 + timedelta(hours=1))
    assert not should_alert(state, "capture", t0 + timedelta(hours=23, minutes=59))
    assert should_alert(state, "capture", t0 + timedelta(hours=24))
    # last_sent moved to the second send
    assert not should_alert(state, "capture", t0 + timedelta(hours=25))


def test_keys_are_independent() -> None:
    state = AlertState()
    t0 = datetime(2026, 10, 25, 9, 0, tzinfo=UTC)
    assert should_alert(state, "capture", t0)
    assert should_alert(state, "stale", t0)


def test_resolve_reports_recovery_once() -> None:
    state = AlertState()
    t0 = datetime(2026, 10, 25, 9, 0, tzinfo=UTC)
    should_alert(state, "capture", t0)
    assert resolve(state, "capture") is True
    assert resolve(state, "capture") is False
    assert should_alert(state, "capture", t0 + timedelta(minutes=1))


def test_is_stale_boundary() -> None:
    newest = date(2026, 10, 24)
    assert not is_stale(newest, datetime(2026, 10, 26, 19, 59, tzinfo=UTC))
    assert is_stale(newest, datetime(2026, 10, 26, 20, 1, tzinfo=UTC))
    assert is_stale(None, datetime(2026, 10, 26, 0, 0, tzinfo=UTC))


def test_capture_at_risk_only_late() -> None:
    day = datetime(2026, 10, 25, 13, 0)
    assert not capture_at_risk(True, day)
    assert capture_at_risk(True, day.replace(hour=21))
    assert not capture_at_risk(False, day.replace(hour=21))


def test_state_round_trip_and_corrupt_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert load_state(tmp_path) == AlertState()
    assert capsys.readouterr().err == ""
    state = AlertState(
        active={"capture": "2026-10-25T09:00:00+00:00"},
        signals_as_of={"real": "2026-10-24"},
        archive_probe_on="2026-10-25",
    )
    save_state(tmp_path, state)
    assert (tmp_path / "data" / "results" / "daily-state.json").exists()
    assert load_state(tmp_path) == state

    path = tmp_path / "data" / "results" / "daily-state.json"
    path.write_text("{not json")
    assert load_state(tmp_path) == AlertState()
    assert "daily-state.json" in capsys.readouterr().err

    path.write_text('["wrong shape"]')
    assert load_state(tmp_path) == AlertState()
    assert capsys.readouterr().err != ""


def _write(tmp_path: Path, text: str) -> None:
    path = tmp_path / "data" / "results" / "daily-state.json"
    path.parent.mkdir(parents=True)
    path.write_text(text)


def test_bad_last_sent_is_dropped_and_realerts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(
        tmp_path,
        '{"active": {"garbage": "nope", "naive": "2026-10-25T09:00:00",'
        ' "ok": "2026-10-25T09:00:00+00:00"},'
        ' "signals_as_of": {"real": "2026-10-24", "paper": "xx"}}',
    )
    state = load_state(tmp_path)
    assert set(state.active) == {"ok"}
    assert state.signals_as_of == {"real": "2026-10-24"}
    assert capsys.readouterr().err.count("warning") == 1
    now = datetime(2026, 10, 25, 10, 0, tzinfo=UTC)
    assert should_alert(state, "garbage", now)
    assert should_alert(state, "naive", now)
    assert not should_alert(state, "ok", now)


def test_missing_fields_use_defaults(tmp_path: Path) -> None:
    _write(tmp_path, '{"active": {"capture": "2026-10-25T09:00:00+00:00"}, "extra": 1}')
    state = load_state(tmp_path)
    assert state.active == {"capture": "2026-10-25T09:00:00+00:00"}
    assert state.signals_as_of == {}
    assert state.archive_probe_on is None


def test_wrong_typed_field_falls_back_alone(tmp_path: Path) -> None:
    _write(tmp_path, '{"active": [1], "signals_as_of": {"real": "2026-10-24"}}')
    state = load_state(tmp_path)
    assert state.active == {}
    assert state.signals_as_of == {"real": "2026-10-24"}


def test_is_stale_false_at_exact_boundary() -> None:
    assert not is_stale(date(2026, 10, 24), datetime(2026, 10, 26, 20, 0, tzinfo=UTC))
