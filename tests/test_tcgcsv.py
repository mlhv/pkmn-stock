import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import py7zr

from pkmn_quant.data import tcgcsv

GROUPS_PAYLOAD: dict[str, Any] = {
    "totalItems": 2,
    "success": True,
    "results": [
        {
            "groupId": 24541,
            "name": "ME: Ascended Heroes",
            "abbreviation": "MEG",
            "publishedOn": "2026-02-20T00:00:00",
            "categoryId": 3,
        },
        {
            "groupId": 3170,
            "name": "SV: Scarlet & Violet 151",
            "abbreviation": "MEW",
            "publishedOn": "2023-09-22T00:00:00",
            "categoryId": 3,
        },
    ],
}

PRICE_ROWS: list[dict[str, Any]] = [
    {
        "productId": 666906,
        "lowPrice": 23.95,
        "midPrice": 32.81,
        "highPrice": 55.0,
        "marketPrice": 32.98,
        "directLowPrice": None,
        "subTypeName": "Normal",
    }
]


def test_parse_groups() -> None:
    groups = tcgcsv.parse_groups(GROUPS_PAYLOAD)
    assert len(groups) == 2
    assert groups[0].group_id == 24541
    assert groups[0].name == "ME: Ascended Heroes"
    assert groups[0].published_on == date(2026, 2, 20)


def test_archive_url() -> None:
    assert (
        tcgcsv.archive_url(date(2025, 6, 1))
        == "https://tcgcsv.com/archive/tcgplayer/prices-2025-06-01.ppmd.7z"
    )


def make_archive(tmp_path: Path, day: date, group_id: int, rows: list[dict[str, Any]]) -> Path:
    """Build a miniature tcgcsv daily archive: <date>/3/<groupId>/prices inside a 7z."""
    src = tmp_path / "archive-src" / day.isoformat() / "3" / str(group_id)
    src.mkdir(parents=True)
    (src / "prices").write_text(json.dumps({"success": True, "results": rows}))
    archive = tmp_path / f"prices-{day.isoformat()}.ppmd.7z"
    with py7zr.SevenZipFile(archive, "w") as z:
        z.writeall(tmp_path / "archive-src" / day.isoformat(), arcname=day.isoformat())
    return archive


def test_extract_group_prices(tmp_path: Path) -> None:
    day = date(2025, 6, 1)
    archive = make_archive(tmp_path, day, 24541, PRICE_ROWS)
    out = tcgcsv.extract_group_prices(archive, day, {24541, 99999})
    assert out == {24541: PRICE_ROWS}


def test_extract_returns_empty_for_unknown_groups(tmp_path: Path) -> None:
    day = date(2025, 6, 1)
    archive = make_archive(tmp_path, day, 24541, PRICE_ROWS)
    assert tcgcsv.extract_group_prices(archive, day, {12345}) == {}


def test_download_archive_caches(tmp_path: Path) -> None:
    day = date(2025, 6, 1)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=b"archive-bytes")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    first = tcgcsv.download_archive(client, day, tmp_path)
    second = tcgcsv.download_archive(client, day, tmp_path)
    assert first == second
    assert first.read_bytes() == b"archive-bytes"
    assert calls == 1


def test_make_client_sets_user_agent() -> None:
    # tcgcsv.com returns 401 for httpx's default User-Agent.
    with tcgcsv.make_client() as client:
        assert client.headers["User-Agent"].startswith("pkmn-quant/")
