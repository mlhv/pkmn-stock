"""Finalizing captured live days into the warehouse (no network)."""

from __future__ import annotations

import json
from datetime import date
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


def server(update: str = UPDATE, market: float = 100.0, bad: tuple[int, ...] = ()) -> Server:
    s = Server([update])
    s.group_ids = [1, 2]
    for gid in (1, 2):
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


def test_older_pending_day_is_finalized_partial_with_missing_recorded(tmp_path: Path) -> None:
    old = server(bad=(2,))
    old.group_ids = [1, 2]
    capture_update(old.client(), Paths(tmp_path), [1, 2], sleep=lambda _: None)
    report = go(tmp_path, server(UPDATE_25, market=1500.0))
    assert [w.day for w in report.written] == [date(2026, 10, 24), date(2026, 10, 25)]
    assert report.partial == {date(2026, 10, 24): (2,)}
    wh = Warehouse(Paths(tmp_path))
    assert wh.load_day(date(2026, 10, 24)).height == 1
    # 10-24 is the quality baseline for 10-25: product 1 went 100 -> 1500.
    assert report.written[1].rows_quarantined == 1


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
    capture_update(server(bad=(2,)).client(), paths, [1, 2], sleep=lambda _: None)
    d = tmp_path / "data/raw/live/2026-10-24"
    m = json.loads((d / "manifest.json").read_text())
    m["groups"]["2"] = {"status": "held", "http": 200, "rows": 1}
    (d / "manifest.json").write_text(json.dumps(m))
    report = go(tmp_path, server(UPDATE_25))
    assert report.partial == {date(2026, 10, 24): (2,)}
