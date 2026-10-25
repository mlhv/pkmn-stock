"""Live capture of tcgcsv's per-group /prices endpoints (no network)."""

from __future__ import annotations

import gzip
import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from pkmn_quant.config import Paths
from pkmn_quant.data.live import (
    LiveIngestError,
    capture_update,
    fetch_last_updated,
    update_day,
)

UPDATE = "2026-10-24T20:05:44+0000"
OK_BODY = b'{"success": true, "errors": [], "results": [{"productId": 1}]}'
EMPTY_BODY = b'{"success": true, "results": []}'
OLD_LM = "Thu, 31 Oct 2024 12:00:00 GMT"

Reply = Callable[[], httpx.Response]


def ok(body: bytes = OK_BODY, lm: str = OLD_LM) -> Reply:
    return lambda: httpx.Response(200, content=body, headers={"Last-Modified": lm, "ETag": '"e1"'})


def status(code: int) -> Reply:
    return lambda: httpx.Response(code)


class Server:
    """Scripted tcgcsv: per-gid reply lists (last one repeats) and counters."""

    def __init__(self, updates: list[str] | None = None) -> None:
        self.updates = list(updates or [UPDATE])
        self.replies: dict[int, list[Reply]] = {}
        self.counts: dict[int, int] = {}
        self.lu_reads = 0

    def script(self, gid: int, *replies: Reply) -> None:
        self.replies[gid] = list(replies)

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/last-updated.txt":
            text = self.updates[min(self.lu_reads, len(self.updates) - 1)]
            self.lu_reads += 1
            return httpx.Response(200, text=text + "\n")
        gid = int(path.split("/")[-2])
        n = self.counts.get(gid, 0)
        self.counts[gid] = n + 1
        queue = self.replies[gid]
        return queue[min(n, len(queue) - 1)]()

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


def run(server: Server, tmp_path: Path, gids: list[int], sleeps: list[float] | None = None):
    rec = sleeps if sleeps is not None else []
    return capture_update(server.client(), Paths(tmp_path), gids, sleep=rec.append)


def day_dir(tmp_path: Path, day: str = "2026-10-24") -> Path:
    return tmp_path / "data" / "raw" / "live" / day


def test_update_day_uses_utc_date() -> None:
    ts = datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
    assert update_day(ts) == date(2026, 10, 25)
    local = ts.astimezone(ZoneInfo("America/New_York"))
    assert local.day == 24
    assert update_day(local) == date(2026, 10, 25)


def test_capture_writes_raw_and_manifest(tmp_path: Path) -> None:
    s = Server()
    s.script(10, ok())
    s.script(11, ok())
    cap = run(s, tmp_path, [10, 11])
    for gid in (10, 11):
        assert gzip.decompress((day_dir(tmp_path) / f"{gid}.json.gz").read_bytes()) == OK_BODY
    m = json.loads((day_dir(tmp_path) / "manifest.json").read_text())
    assert m["last_updated"] == "2026-10-24T20:05:44+00:00"
    rec = m["groups"]["10"]
    assert rec["status"] == "held"
    assert rec["rows"] == 1
    assert rec["http"] == 200
    assert rec["last_modified"] == OLD_LM
    assert rec["etag"] == '"e1"'
    assert cap.held == (10, 11)
    assert cap.complete
    assert cap.day == date(2026, 10, 24)
    assert not cap.moved_on
    assert not cap.collision


def test_empty_results_is_held_and_404_is_absent(tmp_path: Path) -> None:
    s = Server()
    s.script(1, ok(EMPTY_BODY))
    s.script(2, status(404))
    cap = run(s, tmp_path, [1, 2])
    m = json.loads((day_dir(tmp_path) / "manifest.json").read_text())
    assert m["groups"]["1"]["status"] == "held"
    assert m["groups"]["1"]["rows"] == 0
    assert m["groups"]["2"]["status"] == "absent"
    assert not (day_dir(tmp_path) / "2.json.gz").exists()
    assert cap.held == (1,)
    assert cap.absent == (2,)
    assert cap.complete


def test_failures_retry_then_stay_failed(tmp_path: Path) -> None:
    s = Server()
    s.script(1, status(503))
    s.script(2, ok(b"<html>nope</html>"))
    sleeps: list[float] = []
    cap = run(s, tmp_path, [1], sleeps)
    assert s.counts[1] == 3
    assert sleeps == [2.0, 4.0]
    cap2 = run(s, tmp_path, [2])
    assert cap.failed == (1,)
    assert cap2.failed == (2,)
    assert not cap.complete
    assert not list(day_dir(tmp_path).glob("*.json.gz*"))
    m = json.loads((day_dir(tmp_path) / "manifest.json").read_text())
    assert m["groups"]["1"]["status"] == "failed"
    assert "error" in m["groups"]["2"]


def test_resume_skips_held_and_absent(tmp_path: Path) -> None:
    s = Server()
    s.script(1, ok())
    s.script(2, status(404))
    s.script(3, status(500), status(500), status(500), ok())
    first = run(s, tmp_path, [1, 2, 3])
    assert first.failed == (3,)
    second = run(s, tmp_path, [1, 2, 3])
    assert (s.counts[1], s.counts[2]) == (1, 1)
    assert s.counts[3] == 4
    assert second.held == (1, 3)
    assert second.absent == (2,)
    assert second.complete


def test_resume_treats_tmp_as_not_held(tmp_path: Path) -> None:
    d = day_dir(tmp_path)
    d.mkdir(parents=True)
    (d / "7.json.gz.tmp").write_bytes(b"partial")
    s = Server()
    s.script(7, ok())
    cap = run(s, tmp_path, [7])
    assert s.counts[7] == 1
    assert cap.held == (7,)
    assert gzip.decompress((d / "7.json.gz").read_bytes()) == OK_BODY


def test_held_entry_with_missing_file_is_refetched(tmp_path: Path) -> None:
    s = Server()
    s.script(7, ok())
    run(s, tmp_path, [7])
    (day_dir(tmp_path) / "7.json.gz").unlink()
    run(s, tmp_path, [7])
    assert s.counts[7] == 2
    assert (day_dir(tmp_path) / "7.json.gz").exists()


def test_reads_stopgap_manifest(tmp_path: Path) -> None:
    d = day_dir(tmp_path)
    d.mkdir(parents=True)
    groups = {}
    for gid in (5, 6):
        (d / f"{gid}.json.gz").write_bytes(gzip.compress(OK_BODY))
        groups[str(gid)] = {
            "status": "held",
            "http": 200,
            "last_modified": OLD_LM,
            "etag": '"x"',
            "fetched_at": "2026-10-25T16:33:00+00:00",
            "rows": 1,
        }
    groups["9"] = {"status": "absent", "http": 404, "fetched_at": "2026-10-25T16:33:01+00:00"}
    manifest = {
        "last_updated": "2026-10-24T20:05:44+00:00",
        "last_updated_after": "2026-10-24T20:05:44+00:00",
        "groups": groups,
    }
    (d / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    s = Server()
    cap = run(s, tmp_path, [5, 6, 9])
    assert s.counts == {}
    assert cap.held == (5, 6)
    assert cap.absent == (9,)
    assert cap.complete


def test_file_newer_than_update_is_set_aside(tmp_path: Path) -> None:
    s = Server()
    s.script(1, ok(lm="Sun, 25 Oct 2026 20:01:00 GMT"))
    s.script(2, ok(lm=OLD_LM))
    cap = run(s, tmp_path, [1, 2])
    assert cap.failed == (1,)
    assert cap.held == (2,)
    m = json.loads((day_dir(tmp_path) / "manifest.json").read_text())
    assert "newer than update" in m["groups"]["1"]["error"]
    assert not (day_dir(tmp_path) / "1.json.gz").exists()


def test_second_update_same_day_does_not_mix(tmp_path: Path) -> None:
    d = tmp_path / "data" / "raw" / "live" / "2026-10-25"
    d.mkdir(parents=True)
    (d / "3.json.gz").write_bytes(gzip.compress(OK_BODY))
    manifest = {
        "last_updated": "2026-10-25T00:30:00+00:00",
        "groups": {"3": {"status": "held", "http": 200, "rows": 1}},
    }
    mpath = d / "manifest.json"
    mpath.write_text(json.dumps(manifest))
    before = mpath.read_bytes()
    s = Server(["2026-10-25T20:05:00+0000"])
    s.script(3, ok())
    s.script(4, ok())
    cap = run(s, tmp_path, [3, 4])
    assert cap.collision
    assert s.counts == {}
    assert cap.held == (3,)
    assert mpath.read_bytes() == before


def test_moved_on_detected(tmp_path: Path) -> None:
    s = Server([UPDATE, "2026-10-25T02:00:00+0000"])
    s.script(1, ok())
    cap = run(s, tmp_path, [1])
    assert cap.moved_on
    m = json.loads((day_dir(tmp_path) / "manifest.json").read_text())
    assert m["last_updated_after"] == "2026-10-25T02:00:00+00:00"


def test_bad_last_updated_raises_and_writes_nothing(tmp_path: Path) -> None:
    s = Server(["<html>oops</html>"])
    with pytest.raises(LiveIngestError):
        run(s, tmp_path, [1])
    assert not list(tmp_path.rglob("*"))


def test_fetch_last_updated_http_error() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(LiveIngestError):
        fetch_last_updated(client)
