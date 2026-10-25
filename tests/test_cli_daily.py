import json
import os
import shutil
import time
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import polars as pl
import pytest
from typer.testing import CliRunner, Result

import pkmn_quant.live.notify as notify
from pkmn_quant.cli import app
from pkmn_quant.config import Paths
from pkmn_quant.data.ingest import IngestStats
from pkmn_quant.data.live import Capture, LiveIngestError, LiveIngestReport
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

    daily_dirs = sorted((tmp_path / "data" / "results").glob("daily-2*"))
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
# Live ingest wiring and the alert policy.
#
# run_live_ingest is monkeypatched to return scripted reports, and the alert
# clock is injected by monkeypatching cli._now. The process TZ is pinned to UTC
# (utc_tz fixture) so cli's local time, _now().astimezone(), equals the UTC
# value the test scripts: "21:00" below is 21:00 local for capture_at_risk.
# Seeded newest stored day is 2025-05-01, so the stale threshold is
# 2025-05-03 00:00 UTC (20:00 on the newest day + 28h).
# ---------------------------------------------------------------------------


@pytest.fixture
def utc_tz() -> Iterator[None]:
    old = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    time.tzset()
    yield
    if old is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = old
    time.tzset()


@pytest.fixture(scope="module")
def prepared_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Seeded warehouse plus one walk-forward artifact, built once per module."""
    root = tmp_path_factory.mktemp("daily-template")
    seed(root)
    run_walkforward(CliRunner(), root)
    return root


@pytest.fixture
def root(prepared_template: Path, tmp_path: Path, utc_tz: None) -> Path:
    dest = tmp_path / "root"
    shutil.copytree(prepared_template, dest)
    return dest


@pytest.fixture
def probes(monkeypatch: pytest.MonkeyPatch) -> list[date]:
    calls: list[date] = []

    def fake_probe(client: Any, day: date) -> bool:
        calls.append(day)
        return False

    monkeypatch.setattr("pkmn_quant.cli.probe_archive", fake_probe)
    return calls


def at(monkeypatch: pytest.MonkeyPatch, when: datetime) -> None:
    monkeypatch.setattr("pkmn_quant.cli._now", lambda: when)


def utc(y: int, m: int, d: int, h: int) -> datetime:
    return datetime(y, m, d, h, 0, tzinfo=UTC)


def cap(day: date, failed: tuple[int, ...] = (), collision: bool = False) -> Capture:
    return Capture(
        day=day,
        update_ts=datetime(day.year, day.month, day.day, 20, 5, tzinfo=UTC),
        held=(1, 2),
        absent=(3,),
        failed=failed,
        moved_on=False,
        collision=collision,
    )


def live_report(
    capture: Capture | None = None,
    written: tuple[date, ...] = (),
    partial: dict[date, tuple[int, ...]] | None = None,
    collision: bool = False,
    catalog_failures: tuple[int, ...] = (),
    abandoned: tuple[date, ...] = (),
    finalize_errors: dict[date, str] | None = None,
) -> LiveIngestReport:
    return LiveIngestReport(
        capture=capture,
        written=tuple(IngestStats(day=d, rows_clean=1, rows_quarantined=0) for d in written),
        partial=partial or {},
        collision=collision,
        catalog_failures=catalog_failures,
        abandoned=abandoned,
        finalize_errors=finalize_errors or {},
    )


def script(monkeypatch: pytest.MonkeyPatch, *steps: LiveIngestReport | Exception) -> list[Paths]:
    """run_live_ingest returns (or raises) each step in turn. A report that
    writes days also writes those price partitions, as the real one would."""
    remaining = list(steps)
    calls: list[Paths] = []

    def fake(paths: Paths, *args: Any, **kwargs: Any) -> LiveIngestReport:
        calls.append(paths)
        step = remaining.pop(0)
        if isinstance(step, Exception):
            raise step
        w = Warehouse(paths)
        for stats in step.written:
            w.write_prices(
                stats.day, pl.DataFrame([price_row(stats.day, 1, 100.0)], schema=PRICE_SCHEMA)
            )
        return step

    monkeypatch.setattr("pkmn_quant.cli.run_live_ingest", fake)
    return calls


def deposit(root: Path, paper: bool = False) -> None:
    args = ["portfolio", "deposit", "--amount", "1000", "--date", "2025-01-02"]
    r = CliRunner().invoke(app, [*args, *(["--paper"] if paper else []), "--root", str(root)])
    assert r.exit_code == 0, r.output


def daily(root: Path, *extra: str) -> Result:
    return CliRunner().invoke(app, ["daily", *extra, "--root", str(root)])


def meta_of(root: Path, paper: bool = False) -> dict[str, Any]:
    suffix = "-paper" if paper else ""
    dirs = sorted((root / "data" / "results").glob(f"daily-*{suffix}/daily.json"))
    data: dict[str, Any] = json.loads(dirs[-1].read_text())
    return data


def test_daily_new_day_runs_signals_and_records_live(
    root: Path, sent: list[tuple[str, str]], probes: list[date], monkeypatch: pytest.MonkeyPatch
) -> None:
    at(monkeypatch, utc(2025, 5, 3, 13))
    calls = script(monkeypatch, live_report(cap(date(2025, 5, 2)), written=(date(2025, 5, 2),)))

    result = daily(root)
    assert result.exit_code == 0, result.output
    assert len(calls) == 1 and calls[0].root == root

    meta = meta_of(root)
    assert meta["status"] == "ok"
    assert meta["as_of"] == "2025-05-02"  # signals ran on the new day
    assert meta["signals_skipped"] is False
    assert meta["live"] == {
        "update_day": "2025-05-02",
        "held": 2,
        "absent": 1,
        "failed": 0,
        "written": ["2025-05-02"],
        "partial": {},
        "collision": False,
        "abandoned": [],
        "new_day": True,
    }
    state = json.loads((root / "data" / "results" / "daily-state.json").read_text())
    assert state["signals_as_of"] == {"real": "2025-05-02"}
    assert sent == []  # nothing actionable without cash, and nothing wrong


def test_daily_same_as_of_twice_does_not_renotify(
    root: Path, sent: list[tuple[str, str]], probes: list[date], monkeypatch: pytest.MonkeyPatch
) -> None:
    deposit(root)
    at(monkeypatch, utc(2025, 5, 2, 9))
    script(monkeypatch, live_report(), live_report())

    first = daily(root)
    assert first.exit_code == 0, first.output
    buys = [b for t, b in sent if "buys" in b]
    assert len(buys) == 1
    first_meta = meta_of(root)
    assert first_meta["n_buys"] >= 1

    at(monkeypatch, utc(2025, 5, 2, 13))
    second = daily(root)
    assert second.exit_code == 0, second.output
    assert [b for t, b in sent if "buys" in b] == buys  # no second buy/sell push
    meta = meta_of(root)
    assert meta["status"] == "ok"
    assert meta["signals_skipped"] is True
    # Same day, same as_of: the acting run's counts survive the skip run.
    assert meta["n_buys"] == first_meta["n_buys"]
    assert meta["n_sells"] == first_meta["n_sells"]
    assert meta["as_of"] == "2025-05-01"
    assert meta["live"]["new_day"] is False
    assert meta["live"]["update_day"] is None  # day already stored, nothing fetched


def test_daily_paper_same_as_of_records_fills_once(
    root: Path, sent: list[tuple[str, str]], probes: list[date], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review Focus 5: four scheduled runs on the same data record fills once."""
    deposit(root, paper=True)
    at(monkeypatch, utc(2025, 5, 2, 9))
    script(monkeypatch, *(live_report() for _ in range(4)))
    ledger = root / "data" / "portfolio" / "paper.jsonl"

    first = daily(root, "--paper")
    assert first.exit_code == 0, first.output
    after_first = ledger.read_text().strip().splitlines()
    assert len(after_first) >= 2  # deposit + at least one recorded fill

    for hour in (13, 17, 21):
        at(monkeypatch, utc(2025, 5, 2, hour))
        r = daily(root, "--paper")
        assert r.exit_code == 0, r.output
    assert ledger.read_text().strip().splitlines() == after_first
    assert meta_of(root, paper=True)["signals_skipped"] is True


def test_daily_transient_capture_failure_is_not_alerted(
    root: Path, sent: list[tuple[str, str]], probes: list[date], monkeypatch: pytest.MonkeyPatch
) -> None:
    at(monkeypatch, utc(2025, 5, 2, 13))
    script(monkeypatch, live_report(cap(date(2025, 5, 2), failed=(7, 9))))

    result = daily(root)
    assert result.exit_code == 0, result.output
    assert sent == []
    meta = meta_of(root)
    assert meta["status"] == "ok"
    assert meta["live"]["failed"] == 2
    assert meta["live"]["new_day"] is False


def test_daily_capture_at_risk_alerts_once_then_recovers(
    root: Path, sent: list[tuple[str, str]], probes: list[date], monkeypatch: pytest.MonkeyPatch
) -> None:
    day = date(2025, 5, 2)
    script(
        monkeypatch,
        live_report(cap(day, failed=(7,))),
        live_report(cap(day, failed=(7,))),
        live_report(cap(day), written=(day,)),
    )

    at(monkeypatch, utc(2025, 5, 2, 21))
    r1 = daily(root)
    assert r1.exit_code == 1, r1.output  # an alert-level problem holds
    assert len(sent) == 1
    title, body = sent[0]
    assert "capture incomplete" in title and "7" in body and "2025-05-02" in body

    at(monkeypatch, utc(2025, 5, 2, 22))
    r2 = daily(root)
    assert r2.exit_code == 1, r2.output  # still at risk, but rate-limited: no push
    assert len(sent) == 1

    at(monkeypatch, utc(2025, 5, 2, 23))
    r3 = daily(root)
    assert r3.exit_code == 0, r3.output
    assert len(sent) == 2
    assert sent[1][0] == f"{title}: recovered"


def test_daily_stale_warehouse_alerts(
    root: Path, sent: list[tuple[str, str]], probes: list[date], monkeypatch: pytest.MonkeyPatch
) -> None:
    script(monkeypatch, live_report(), live_report())
    at(monkeypatch, utc(2025, 5, 10, 9))
    r1 = daily(root)
    assert r1.exit_code == 1, r1.output
    stale = [(t, b) for t, b in sent if "no new prices" in t]
    assert len(stale) == 1 and "2025-05-01" in stale[0][1]
    assert len(sent) == 1

    at(monkeypatch, utc(2025, 5, 10, 13))
    daily(root)
    assert len(sent) == 1  # once, then at most daily


def test_daily_partial_and_collision_alert(
    root: Path, sent: list[tuple[str, str]], probes: list[date], monkeypatch: pytest.MonkeyPatch
) -> None:
    day = date(2025, 5, 2)
    script(
        monkeypatch,
        live_report(
            cap(day, collision=True), written=(day,), partial={day: (7, 9)}, collision=True
        ),
    )
    at(monkeypatch, utc(2025, 5, 3, 13))
    result = daily(root)
    assert result.exit_code == 1, result.output

    partial = [b for t, b in sent if "partial day" in t]
    assert len(partial) == 1
    assert "2025-05-02" in partial[0] and "7" in partial[0] and "9" in partial[0]
    collision = [b for t, b in sent if "two updates" in t]
    assert len(collision) == 1 and "2025-05-02" in collision[0]
    assert len(sent) == 2


def test_daily_live_ingest_error_records_and_alerts_when_at_risk(
    root: Path, sent: list[tuple[str, str]], probes: list[date], monkeypatch: pytest.MonkeyPatch
) -> None:
    script(
        monkeypatch,
        LiveIngestError("last-updated.txt: unparseable"),
        LiveIngestError("last-updated.txt: unparseable"),
    )

    at(monkeypatch, utc(2025, 5, 2, 13))  # early and fresh: recorded, not alerted
    r1 = daily(root)
    assert r1.exit_code == 0, r1.output
    meta = meta_of(root)
    assert meta["status"] == "error"
    assert "unparseable" in meta["error"]
    assert meta["live"] is None
    assert sent == []
    # signals still ran despite the ingest error
    assert (root / "data" / "results" / "daily-2025-05-02" / "signals.md").exists()

    at(monkeypatch, utc(2025, 5, 2, 21))  # at risk: one "capture" alert naming the error
    r2 = daily(root)
    assert r2.exit_code == 1, r2.output
    meta = meta_of(root)
    assert meta["status"] == "error" and "unparseable" in meta["error"]
    assert len(sent) == 1
    assert "capture incomplete" in sent[0][0] and "unparseable" in sent[0][1]
    state = json.loads((root / "data" / "results" / "daily-state.json").read_text())
    assert "capture" in state["active"] and "stale" not in state["active"]


def test_daily_archive_probe_once_per_day_and_alerts_when_back(
    root: Path, sent: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    probed: list[date] = []

    def fake_probe(client: Any, day: date) -> bool:
        probed.append(day)
        return True

    monkeypatch.setattr("pkmn_quant.cli.probe_archive", fake_probe)
    script(monkeypatch, live_report(), live_report(), live_report())

    at(monkeypatch, utc(2025, 5, 2, 9))
    r1 = daily(root)
    assert r1.exit_code == 0, r1.output
    assert probed == [date(2025, 5, 2)]  # newest stored day + 1
    back = [b for t, b in sent if "archive is back" in t]
    assert len(back) == 1 and "2025-05-02" in back[0]

    at(monkeypatch, utc(2025, 5, 2, 13))
    daily(root)
    assert probed == [date(2025, 5, 2)]  # once per local day
    assert len([t for t, _ in sent if "archive is back" in t]) == 1

    at(monkeypatch, utc(2025, 5, 3, 9))
    daily(root)
    assert len(probed) == 2


def test_daily_skip_ingest_records_no_live_and_no_probe(
    root: Path, sent: list[tuple[str, str]], probes: list[date], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = script(monkeypatch)
    at(monkeypatch, utc(2025, 5, 10, 9))  # stale, but --skip-ingest is offline
    result = daily(root, "--skip-ingest")
    assert result.exit_code == 0, result.output
    assert calls == [] and probes == []
    assert meta_of(root)["live"] is None
    assert sent == []


def test_daily_records_and_notifies_catalog_failures(
    root: Path, sent: list[tuple[str, str]], probes: list[date], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A degraded catalog refresh inside the unattended run must leave a
    durable trace (daily.json) and a notification, without failing the run —
    refresh_products merges, so the failed groups kept their previous rows."""
    day = date(2025, 5, 2)
    script(monkeypatch, live_report(cap(day), written=(day,), catalog_failures=(2534,)))
    at(monkeypatch, utc(2025, 5, 3, 9))

    result = daily(root)
    assert result.exit_code == 0, result.output

    meta = meta_of(root)
    assert meta["status"] == "ok"
    assert meta["catalog_failures"] == 1
    catalog_notifs = [b for t, b in sent if "catalog" in t.lower()]
    assert len(catalog_notifs) == 1 and "2534" in catalog_notifs[0]


def test_daily_run_failure_alerts_once_per_day_and_recovers(
    root: Path, sent: list[tuple[str, str]], probes: list[date], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-ingest failure goes through _fail (exit 1), rate-limited, and the
    state file is saved on that path too."""
    shutil.rmtree(next((root / "data" / "results").glob("wf-*")))
    at(monkeypatch, utc(2025, 5, 2, 9))
    r1 = daily(root, "--skip-ingest")
    assert r1.exit_code == 1
    assert len(sent) == 1 and "run failed" in sent[0][0]
    state = json.loads((root / "data" / "results" / "daily-state.json").read_text())
    assert "run_error:real" in state["active"]

    at(monkeypatch, utc(2025, 5, 2, 13))
    assert daily(root, "--skip-ingest").exit_code == 1
    assert len(sent) == 1

    run_walkforward(CliRunner(), root)
    at(monkeypatch, utc(2025, 5, 2, 17))
    r3 = daily(root, "--skip-ingest")
    assert r3.exit_code == 0, r3.output
    assert sent[-1][0] == f"{sent[0][0]}: recovered"


def test_daily_skip_on_a_new_local_day_reports_zero_counts(
    root: Path, sent: list[tuple[str, str]], probes: list[date], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Counts carry forward only within the same daily-<date> dir: the next
    morning, with no new data, nothing was acted on that day."""
    deposit(root)
    day = date(2025, 5, 2)
    script(monkeypatch, live_report(cap(day), written=(day,)), live_report())
    at(monkeypatch, utc(2025, 5, 2, 21))
    assert daily(root).exit_code == 0
    assert meta_of(root)["n_buys"] >= 1
    at(monkeypatch, utc(2025, 5, 3, 9))  # newest is 05-02: not stale until 05-04 00:00
    assert daily(root).exit_code == 0
    meta = json.loads((root / "data" / "results" / "daily-2025-05-03" / "daily.json").read_text())
    assert meta["signals_skipped"] is True and meta["n_buys"] == 0


def test_daily_archive_probe_skips_live_days(
    root: Path, sent: list[tuple[str, str]], probes: list[date], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The probe asks for the day after the newest ARCHIVE day (the first lost
    day), not the day after a live-stored day."""
    live_day = date(2025, 5, 8)
    Warehouse(Paths(root=root)).write_prices(
        live_day, pl.DataFrame([price_row(live_day, 1, 100.0)], schema=PRICE_SCHEMA)
    )
    manifest = Paths(root=root).raw_live / live_day.isoformat() / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}")
    script(monkeypatch, live_report())
    at(monkeypatch, utc(2025, 5, 9, 9))
    assert daily(root).exit_code == 0
    assert probes == [date(2025, 5, 2)]  # archive day 2025-05-01 + 1


def test_daily_failure_writing_artifacts_goes_through_run_error(
    root: Path, sent: list[tuple[str, str]], probes: list[date], monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(report: Any) -> str:
        raise OSError("disk full")

    monkeypatch.setattr("pkmn_quant.live.report.render_signals_markdown", boom)
    at(monkeypatch, utc(2025, 5, 2, 9))
    result = daily(root, "--skip-ingest")
    assert result.exit_code == 1
    meta = meta_of(root)
    assert meta["status"] == "error" and "disk full" in meta["error"]
    assert len(sent) == 1 and "run failed" in sent[0][0]
    state = json.loads((root / "data" / "results" / "daily-state.json").read_text())
    assert "run_error:real" in state["active"]
    assert "real" not in state["signals_as_of"]


# --- final-review fix wave -------------------------------------------------


def test_daily_complete_capture_not_stored_at_21_is_at_risk(
    root: Path, sent: list[tuple[str, str]], probes: list[date], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A complete capture whose day was not written (the feed moved on) is
    still an unstored update at 21:00."""
    script(monkeypatch, live_report(cap(date(2025, 5, 2))))
    at(monkeypatch, utc(2025, 5, 2, 21))
    result = daily(root)
    assert result.exit_code == 1, result.output
    assert len(sent) == 1 and "capture incomplete" in sent[0][0]
    assert "2025-05-02" in sent[0][1]


def test_daily_stale_is_judged_after_the_runs_ingest(
    root: Path, sent: list[tuple[str, str]], probes: list[date], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Morning after a missed update: stale before the ingest, fresh once the
    run writes the day, so no stale alert."""
    day = date(2025, 5, 2)
    script(monkeypatch, live_report(cap(day), written=(day,)))
    at(monkeypatch, utc(2025, 5, 3, 9))
    result = daily(root)
    assert result.exit_code == 0, result.output
    assert [t for t, _ in sent if "no new prices" in t] == []


def test_daily_morning_after_a_missed_update_is_stale(
    root: Path, sent: list[tuple[str, str]], probes: list[date], monkeypatch: pytest.MonkeyPatch
) -> None:
    script(monkeypatch, live_report())
    at(monkeypatch, utc(2025, 5, 3, 9))  # newest 05-01; 05-02's update was missed
    result = daily(root)
    assert result.exit_code == 1, result.output
    assert [t for t, _ in sent] == ["pkmn daily: no new prices"]


def test_daily_abandoned_day_alerts_once_and_is_recorded(
    root: Path, sent: list[tuple[str, str]], probes: list[date], monkeypatch: pytest.MonkeyPatch
) -> None:
    lost, new = date(2025, 5, 2), date(2025, 5, 3)
    mdir = Paths(root=root).raw_live / lost.isoformat()
    mdir.mkdir(parents=True)
    abandoned = {
        "reason": "below floor",
        "at": "2025-05-03T21:00:00+00:00",
        "held": 3,
        "total": 220,
    }
    (mdir / "manifest.json").write_text(json.dumps({"groups": {}, "abandoned": abandoned}))
    script(
        monkeypatch,
        live_report(cap(new), written=(new,), abandoned=(lost,)),
        live_report(),
    )
    at(monkeypatch, utc(2025, 5, 4, 9))
    result = daily(root)
    assert result.exit_code == 1, result.output
    alerts_sent = [(t, b) for t, b in sent if "abandoned" in t]
    assert len(alerts_sent) == 1
    title, msg = alerts_sent[0]
    assert title == "pkmn daily: day abandoned"
    assert "2025-05-02" in msg and "3/220" in msg and "raw files" in msg
    assert meta_of(root)["live"]["abandoned"] == ["2025-05-02"]
    state = json.loads((root / "data" / "results" / "daily-state.json").read_text())
    assert "abandoned:2025-05-02" in state["active"]

    at(monkeypatch, utc(2025, 5, 4, 13))
    assert daily(root).exit_code == 0
    assert len([t for t, _ in sent if "abandoned" in t]) == 1


def test_daily_finalize_error_is_an_ingest_error(
    root: Path, sent: list[tuple[str, str]], probes: list[date], monkeypatch: pytest.MonkeyPatch
) -> None:
    d1, d2 = date(2025, 5, 2), date(2025, 5, 3)
    errs = {d2: "OSError('disk full')"}
    script(
        monkeypatch,
        live_report(cap(d2), written=(d1,), partial={d1: (7,)}, finalize_errors=errs),
        live_report(cap(d2), finalize_errors=errs),
    )
    at(monkeypatch, utc(2025, 5, 3, 13))
    r1 = daily(root)
    assert r1.exit_code == 1, r1.output  # the partial day alerts
    meta = meta_of(root)
    assert meta["status"] == "error" and "disk full" in meta["error"]
    assert "2025-05-03" in meta["error"]
    assert meta["live"]["written"] == ["2025-05-02"]
    assert [t for t, _ in sent] == ["pkmn daily: partial day"]

    at(monkeypatch, utc(2025, 5, 3, 21))
    r2 = daily(root)
    assert r2.exit_code == 1, r2.output
    capture = [b for t, b in sent if "capture incomplete" in t]
    assert len(capture) == 1 and "disk full" in capture[0]


def test_daily_archive_probe_skipped_when_the_next_day_is_stored(
    root: Path, sent: list[tuple[str, str]], probes: list[date], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Archive days through 2025-05-01 plus live 2025-05-02: no gap, no probe."""
    live_day = date(2025, 5, 2)
    Warehouse(Paths(root=root)).write_prices(
        live_day, pl.DataFrame([price_row(live_day, 1, 100.0)], schema=PRICE_SCHEMA)
    )
    manifest = Paths(root=root).raw_live / live_day.isoformat() / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}")
    script(monkeypatch, live_report())
    at(monkeypatch, utc(2025, 5, 2, 21))
    assert daily(root).exit_code == 0
    assert probes == []


def test_daily_push_goes_through_real_ntfy_wiring(
    root: Path, probes: list[date], monkeypatch: pytest.MonkeyPatch
) -> None:
    """No send_notification patch: notify.toml under the root, the real
    configure/send_notification/send_ntfy chain, and only the HTTP faked."""
    monkeypatch.setattr(notify, "_ntfy", None)  # restored to None afterwards
    monkeypatch.setattr(notify.sys, "platform", "linux")  # no osascript banner
    cfg = root / "data" / "config"
    cfg.mkdir(parents=True)
    (cfg / "notify.toml").write_text('[ntfy]\ntopic = "tp-test"\nserver = "https://ntfy.test"\n')
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    def fake_post(url: str, **kw: Any) -> httpx.Response:
        with httpx.Client(transport=httpx.MockTransport(handler)) as c:
            return c.post(url, **kw)

    monkeypatch.setattr(notify.httpx, "post", fake_post)
    script(monkeypatch, live_report())
    at(monkeypatch, utc(2025, 5, 10, 9))  # stale: one alert
    result = daily(root)
    assert result.exit_code == 1, result.output
    (req,) = seen
    assert str(req.url) == "https://ntfy.test"
    payload = json.loads(req.content.decode("utf-8"))
    assert payload["topic"] == "tp-test"
    assert payload["title"] == "pkmn daily: no new prices"
    assert "2025-05-01" in payload["message"]
