"""Finalizing captured live days into the warehouse (no network)."""

from __future__ import annotations

import gzip
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from pkmn_quant.config import Paths
from pkmn_quant.data.live import (
    capture_update,
    finalize_day,
    pending_days,
    probe_archive,
    run_live_ingest,
)
from pkmn_quant.data.quality import apply_quality_gates
from pkmn_quant.data.transforms import PRICE_SCHEMA, prices_frame
from pkmn_quant.data.warehouse import Warehouse
from tests.live_helpers import UPDATE, Server, ok, status

UPDATE_25 = "2026-10-25T20:05:44+0000"


def row(pid: int, market: float) -> dict[str, Any]:
    return {
        "productId": pid,
        "subTypeName": "Normal",
        "lowPrice": market * 0.9,
        "midPrice": market,
        "highPrice": market * 1.5,
        "marketPrice": market,
    }


def body(rows: list[dict[str, Any]]) -> bytes:
    return json.dumps({"success": True, "errors": [], "results": rows}).encode()


def server(
    update: str = UPDATE,
    market: float = 100.0,
    bad: tuple[int, ...] = (),
    gids: tuple[int, ...] = (1, 2),
) -> Server:
    s = Server([update])
    s.group_ids = list(gids)
    for gid in gids:
        s.script(gid, status(500) if gid in bad else ok(body([row(gid, market)])))
    return s


def stored(tmp_path: Path) -> list[date]:
    return Warehouse(Paths(tmp_path)).stored_days()


def go(tmp_path: Path, s: Server):
    return run_live_ingest(Paths(tmp_path), s.client(), sleep=lambda _: None)


def test_finalized_day_matches_archive_day_schema(tmp_path: Path) -> None:
    paths = Paths(tmp_path)
    s = server()
    cap = capture_update(s.client(), paths, [1, 2], sleep=lambda _: None)
    stats = finalize_day(paths, cap.day)
    got = Warehouse(paths).load_day(cap.day)
    assert dict(got.schema) == dict(PRICE_SCHEMA)
    rows = {1: [row(1, 100.0)], 2: [row(2, 100.0)]}
    clean, _ = apply_quality_gates(prices_frame(cap.day, rows), None)
    assert got.sort("product_id").equals(clean.sort("product_id"))
    assert stats.rows_clean == 2
    with pytest.raises(ValueError):
        finalize_day(paths, cap.day)


def test_complete_current_update_is_written(tmp_path: Path) -> None:
    report = go(tmp_path, server())
    assert [w.day for w in report.written] == [date(2026, 10, 24)]
    assert report.partial == {}
    assert not report.collision
    assert stored(tmp_path) == [date(2026, 10, 24)]
    assert pending_days(Paths(tmp_path)) == []


def test_incomplete_current_update_is_not_written(tmp_path: Path) -> None:
    report = go(tmp_path, server(bad=(2,)))
    assert report.written == ()
    assert stored(tmp_path) == []
    assert (tmp_path / "data/raw/live/2026-10-24/1.json.gz").exists()
    assert pending_days(Paths(tmp_path)) == [date(2026, 10, 24)]


TEN = tuple(range(1, 11))


def test_older_pending_day_is_finalized_partial_with_missing_recorded(tmp_path: Path) -> None:
    old = server(bad=(10,), gids=TEN)
    capture_update(old.client(), Paths(tmp_path), list(TEN), sleep=lambda _: None)
    report = go(tmp_path, server(UPDATE_25, market=1500.0, gids=TEN))
    assert [w.day for w in report.written] == [date(2026, 10, 24), date(2026, 10, 25)]
    assert report.partial == {date(2026, 10, 24): (10,)}
    wh = Warehouse(Paths(tmp_path))
    assert wh.load_day(date(2026, 10, 24)).height == 9
    # 10-24 is the quality baseline for 10-25: products 1..9 went 100 -> 1500.
    assert report.written[1].rows_quarantined == 9


def test_stored_day_is_never_rewritten(tmp_path: Path) -> None:
    go(tmp_path, server())
    part = tmp_path / "data/warehouse/prices/date=2026-10-24/data.parquet"
    before = part.read_bytes()
    later = server("2026-10-24T23:00:00+0000")
    report = go(tmp_path, later)
    assert report.collision
    assert report.capture is None
    assert report.written == ()
    assert part.read_bytes() == before
    assert later.counts == {}


def test_already_stored_same_update_is_noop(tmp_path: Path) -> None:
    go(tmp_path, server())
    again = server()
    report = go(tmp_path, again)
    assert again.counts == {}
    assert report.written == ()
    assert not report.collision


def test_stored_archive_day_without_manifest_is_noop(tmp_path: Path) -> None:
    paths = Paths(tmp_path)
    Warehouse(paths).write_prices(date(2026, 10, 24), prices_frame(date(2026, 10, 24), {}))
    s = server()
    report = go(tmp_path, s)
    assert not report.collision
    assert s.counts == {}


def test_catalog_refreshed_only_when_writing(tmp_path: Path) -> None:
    s = server(bad=(2,))
    go(tmp_path, s)
    assert s.products_requests == 0
    s2 = server()
    go(tmp_path, s2)
    assert s2.products_requests == 2
    s3 = server()
    go(tmp_path, s3)
    assert s3.products_requests == 0


def test_gap_resets_quality_baseline(tmp_path: Path) -> None:
    paths = Paths(tmp_path)
    old = date(2026, 9, 13)
    Warehouse(paths).write_prices(old, prices_frame(old, {1: [row(1, 1.0)]}))
    report = go(tmp_path, server(market=100.0))
    assert report.written[0].rows_quarantined == 0
    assert Warehouse(paths).load_day(date(2026, 10, 24)).height == 2


def test_probe_archive() -> None:
    class Boom(httpx.SyncByteStream):
        def __iter__(self):
            raise AssertionError("body was read")

    def handler_for(code: int):
        return lambda request: httpx.Response(code, stream=Boom())

    day = date(2026, 10, 24)
    for code, want in ((403, False), (200, True)):
        c = httpx.Client(transport=httpx.MockTransport(handler_for(code)))
        assert probe_archive(c, day) is want

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    assert probe_archive(httpx.Client(transport=httpx.MockTransport(down)), day) is False


def test_second_read_naming_stored_day_does_not_fail_the_run(tmp_path: Path) -> None:
    go(tmp_path, server())  # writes 10-24
    part = tmp_path / "data/warehouse/prices/date=2026-10-24/data.parquet"
    before = part.read_bytes()
    # read 1: new day (unstored); reads 2+: the stored day, same timestamp.
    s = server()
    s.updates = [UPDATE_25, UPDATE]
    report = go(tmp_path, s)
    assert report.written == ()
    assert part.read_bytes() == before
    assert stored(tmp_path) == [date(2026, 10, 24)]


def test_held_record_with_missing_file_is_reported_partial(tmp_path: Path) -> None:
    paths = Paths(tmp_path)
    capture_update(server(bad=(10,), gids=TEN).client(), paths, list(TEN), sleep=lambda _: None)
    d = tmp_path / "data/raw/live/2026-10-24"
    m = json.loads((d / "manifest.json").read_text())
    m["groups"]["10"] = {"status": "held", "http": 200, "rows": 1}
    (d / "manifest.json").write_text(json.dumps(m))
    report = go(tmp_path, server(UPDATE_25, gids=TEN))
    assert report.partial == {date(2026, 10, 24): (10,)}


# --- final-review fix wave -------------------------------------------------

UPDATE_26 = "2026-10-26T20:05:44+0000"
D24 = date(2026, 10, 24)
D25 = date(2026, 10, 25)


def many(update: str, gids: range, bad: tuple[int, ...] = ()) -> Server:
    s = Server([update])
    s.group_ids = list(gids)
    for gid in gids:
        s.script(gid, status(500) if gid in bad else ok(body([row(gid, 100.0)])))
    return s


def capture_old(tmp_path: Path, s: Server) -> None:
    capture_update(s.client(), Paths(tmp_path), s.group_ids, sleep=lambda _: None)


def manifest_of(tmp_path: Path, day: date) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (tmp_path / "data/raw/live" / day.isoformat() / "manifest.json").read_text()
    )
    return data


def test_superseded_day_with_every_group_failed_is_abandoned(tmp_path: Path) -> None:
    capture_old(tmp_path, many(UPDATE, range(1, 3), bad=(1, 2)))
    report = go(tmp_path, many(UPDATE_25, range(1, 3)))
    assert report.abandoned == (D24,)
    assert [w.day for w in report.written] == [D25]
    assert report.partial == {}
    assert stored(tmp_path) == [D25]
    rec = manifest_of(tmp_path, D24)["abandoned"]
    assert rec["held"] == 0 and rec["total"] == 2
    assert "reason" in rec and datetime.fromisoformat(rec["at"]).tzinfo is not None
    assert (tmp_path / "data/raw/live/2026-10-24/manifest.json").exists()
    assert pending_days(Paths(tmp_path)) == []
    # Not retried on a later run.
    later = go(tmp_path, many(UPDATE_26, range(1, 3)))
    assert later.abandoned == ()
    assert stored(tmp_path) == [D25, date(2026, 10, 26)]


def test_superseded_day_at_exactly_the_floor_is_written(tmp_path: Path) -> None:
    capture_old(tmp_path, many(UPDATE, range(1, 11), bad=(10,)))
    report = go(tmp_path, many(UPDATE_25, range(1, 11)))
    assert report.abandoned == ()
    assert [w.day for w in report.written] == [D24, D25]
    assert report.partial == {D24: (10,)}


def test_superseded_day_below_the_floor_is_abandoned(tmp_path: Path) -> None:
    capture_old(tmp_path, many(UPDATE, range(1, 11), bad=(9, 10)))
    report = go(tmp_path, many(UPDATE_25, range(1, 11)))
    assert report.abandoned == (D24,)
    assert stored(tmp_path) == [D25]
    assert manifest_of(tmp_path, D24)["abandoned"]["held"] == 8


def test_superseded_day_with_zero_rows_is_abandoned(tmp_path: Path) -> None:
    s = Server([UPDATE])
    s.group_ids = [1, 2]
    s.script(1, ok(body([])))
    s.script(2, ok(body([])))
    capture_old(tmp_path, s)
    report = go(tmp_path, many(UPDATE_25, range(1, 3)))
    assert report.abandoned == (D24,)
    assert stored(tmp_path) == [D25]


def test_finalize_error_keeps_the_report_of_days_already_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pkmn_quant.data.live as live

    capture_old(tmp_path, many(UPDATE, range(1, 11), bad=(10,)))
    real = live.finalize_day

    def flaky(paths: Paths, day: date):
        if day == D25:
            raise OSError("disk full")
        return real(paths, day)

    monkeypatch.setattr(live, "finalize_day", flaky)
    report = go(tmp_path, many(UPDATE_25, range(1, 11)))
    assert [w.day for w in report.written] == [D24]
    assert report.partial == {D24: (10,)}
    assert set(report.finalize_errors) == {D25}
    assert "disk full" in report.finalize_errors[D25]
    assert stored(tmp_path) == [D24]
    assert pending_days(Paths(tmp_path)) == [D25]


def test_older_stopgap_day_is_not_partial_for_a_group_added_later(tmp_path: Path) -> None:
    d = tmp_path / "data/raw/live/2026-10-24"
    d.mkdir(parents=True)
    groups = {}
    for gid in (1, 2):
        (d / f"{gid}.json.gz").write_bytes(gzip.compress(body([row(gid, 100.0)])))
        groups[str(gid)] = {"status": "held", "http": 200, "rows": 1}
    manifest = {"last_updated": "2026-10-24T20:05:44+00:00", "groups": groups}
    (d / "manifest.json").write_text(json.dumps(manifest))
    report = go(tmp_path, many(UPDATE_25, range(1, 4)))  # group 3 is new
    assert [w.day for w in report.written] == [D24, D25]
    assert report.partial == {}


def test_untried_groups_of_a_superseded_day_count_as_missing(tmp_path: Path) -> None:
    # 1..180 held, 181..190 fail (the breaker trips), 191..200 never tried.
    capture_old(tmp_path, many(UPDATE, range(1, 201), bad=tuple(range(181, 191))))
    assert manifest_of(tmp_path, D24)["tracked"] == list(range(1, 201))
    report = go(tmp_path, many(UPDATE_25, range(1, 201)))
    assert report.abandoned == ()
    assert report.partial[D24] == tuple(range(181, 201))
