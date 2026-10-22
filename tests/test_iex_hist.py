import shutil
from collections import namedtuple
from pathlib import Path

import httpx
import pytest

from scripts.iex_hist import HistFile, download, ensure_space, list_files, select

GIB = 1024**3


def _hf(feed: str, size: int = 10) -> HistFile:
    return HistFile("20241223", feed, "1.0", "IEXTP1", size, f"https://x.test/{feed}")


def test_list_files_parses_listing() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "link": "https://x.test/deep",
                    "date": "20241223",
                    "feed": "DEEP",
                    "version": "1.0",
                    "protocol": "IEXTP1",
                    "size": "1530000000",
                }
            ],
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    files = list_files(client, "20241223")
    assert files == [
        HistFile("20241223", "DEEP", "1.0", "IEXTP1", 1530000000, "https://x.test/deep")
    ]
    assert seen[0].url.params["date"] == "20241223"
    assert seen[0].url.host == "iextrading.com"


def test_select_orders_and_reports_missing() -> None:
    files = [_hf("TOPS"), _hf("DEEP"), _hf("DPLS")]
    assert [f.feed for f in select(files, ("DPLS", "DEEP"))] == ["DPLS", "DEEP"]
    with pytest.raises(LookupError, match="DPLC"):
        select(files, ("DPLC",))


def test_ensure_space_refuses_below_reserve(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    usage = namedtuple("usage", "total used free")

    def fake(free: int):  # type: ignore[no-untyped-def]
        return lambda _p: usage(100 * GIB, 0, free)

    monkeypatch.setattr(shutil, "disk_usage", fake(3 * GIB))
    with pytest.raises(OSError):
        ensure_space(tmp_path, int(1.5 * GIB))
    monkeypatch.setattr(shutil, "disk_usage", fake(4 * GIB))
    ensure_space(tmp_path, int(1.5 * GIB))


def test_download_atomic_and_skips_complete(tmp_path: Path) -> None:
    body = b"x" * 1000
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert not (tmp_path / "20241223_IEXTP1_DEEP1.0.pcap.gz").exists()
        assert (tmp_path / "20241223_IEXTP1_DEEP1.0.pcap.gz.part").exists() or calls == 1
        return httpx.Response(200, content=body)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    f = _hf("DEEP", size=len(body))
    path = download(client, f, tmp_path)
    assert path == tmp_path / "20241223_IEXTP1_DEEP1.0.pcap.gz"
    assert path.read_bytes() == body
    assert not list(tmp_path.glob("*.part"))
    assert calls == 1

    def boom(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not request a complete file")

    again = httpx.Client(transport=httpx.MockTransport(boom))
    assert download(again, f, tmp_path) == path
