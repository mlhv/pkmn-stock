from datetime import date
from pathlib import Path
from typing import Any

import httpx

from pkmn_quant.config import Paths
from pkmn_quant.data.ingest import ingest_range, tracked_groups
from pkmn_quant.data.tcgcsv import Group
from pkmn_quant.data.warehouse import Warehouse
from tests.test_tcgcsv import make_archive

GROUP_ID = 24541

GROUPS_JSON = {
    "success": True,
    "results": [
        {
            "groupId": GROUP_ID,
            "name": "ME: Ascended Heroes",
            "abbreviation": "MEG",
            "publishedOn": "2025-02-20T00:00:00",
            "categoryId": 3,
        },
        {
            "groupId": 3170,
            "name": "Old Set",
            "abbreviation": "OLD",
            "publishedOn": "2020-01-01T00:00:00",
            "categoryId": 3,
        },
    ],
}

PRODUCTS_JSON = {
    "success": True,
    "results": [
        {
            "productId": 666906,
            "name": "Collection - Erika",
            "groupId": GROUP_ID,
            "presaleInfo": {"releasedOn": "2025-02-20T00:00:00"},
            "extendedData": [],
        }
    ],
}


def price_row(market: float) -> dict[str, Any]:
    return {
        "productId": 666906,
        "lowPrice": 1.0,
        "midPrice": 2.0,
        "highPrice": 3.0,
        "marketPrice": market,
        "directLowPrice": None,
        "subTypeName": "Normal",
    }


def test_tracked_groups_filters_old_sets() -> None:
    groups = [
        Group(group_id=1, name="new", abbreviation="N", published_on=date(2025, 1, 1)),
        Group(group_id=2, name="old", abbreviation="O", published_on=date(2020, 1, 1)),
        Group(group_id=3, name="future", abbreviation="F", published_on=date(2030, 1, 1)),
    ]
    tracked = tracked_groups(groups, today=date(2025, 6, 1))
    assert [g.group_id for g in tracked] == [1]


def test_ingest_range_end_to_end(tmp_path: Path) -> None:
    d1, d2 = date(2025, 6, 1), date(2025, 6, 2)
    archives = {
        d1: make_archive(tmp_path / "fixtures1", d1, GROUP_ID, [price_row(10.0)]),
        d2: make_archive(tmp_path / "fixtures2", d2, GROUP_ID, [price_row(500.0)]),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/groups"):
            return httpx.Response(200, json=GROUPS_JSON)
        if path.endswith("/products"):
            return httpx.Response(200, json=PRODUCTS_JSON)
        for day, archive in archives.items():
            if path.endswith(f"prices-{day.isoformat()}.ppmd.7z"):
                return httpx.Response(200, content=archive.read_bytes())
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    root = tmp_path / "proj"
    stats = ingest_range(Paths(root=root), d1, d2, client=client)

    assert [s.day for s in stats] == [d1, d2]
    assert stats[0].rows_clean == 1
    # 10.0 -> 500.0 is a 50x jump: quarantined by the price_jump gate.
    assert stats[1].rows_clean == 0
    assert stats[1].rows_quarantined == 1

    warehouse = Warehouse(Paths(root=root))
    assert warehouse.stored_days() == [d1, d2]
    assert warehouse.load_products()["kind"].to_list() == ["sealed"]


def test_ingest_range_skips_already_stored(tmp_path: Path) -> None:
    d1 = date(2025, 6, 1)
    archive = make_archive(tmp_path / "fixtures", d1, GROUP_ID, [price_row(10.0)])
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/groups"):
            return httpx.Response(200, json=GROUPS_JSON)
        if request.url.path.endswith("/products"):
            return httpx.Response(200, json=PRODUCTS_JSON)
        return httpx.Response(200, content=archive.read_bytes())

    client = httpx.Client(transport=httpx.MockTransport(handler))
    root = tmp_path / "proj"
    first = ingest_range(Paths(root=root), d1, d1, client=client)
    calls_after_first = len(calls)
    second = ingest_range(Paths(root=root), d1, d1, client=client)
    assert len(first) == 1
    assert second == []
    assert not any("/products" in c for c in calls[calls_after_first:])


def test_previous_resets_after_long_gap(tmp_path: Path) -> None:
    d1, d2 = date(2025, 6, 1), date(2025, 7, 1)  # 30-day gap > GAP_RESET_DAYS
    archives = {
        d1: make_archive(tmp_path / "f1", d1, GROUP_ID, [price_row(10.0)]),
        d2: make_archive(tmp_path / "f2", d2, GROUP_ID, [price_row(500.0)]),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/groups"):
            return httpx.Response(200, json=GROUPS_JSON)
        if path.endswith("/products"):
            return httpx.Response(200, json=PRODUCTS_JSON)
        for day, archive in archives.items():
            if path.endswith(f"prices-{day.isoformat()}.ppmd.7z"):
                return httpx.Response(200, content=archive.read_bytes())
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    root = tmp_path / "proj"
    ingest_range(Paths(root=root), d1, d1, client=client)
    # Resuming a month later: the 50x move must NOT be quarantined as a jump,
    # because the baseline resets after GAP_RESET_DAYS.
    stats = ingest_range(Paths(root=root), d2, d2, client=client)
    assert stats[0].rows_clean == 1
    assert stats[0].rows_quarantined == 0


def test_cli_help() -> None:
    from typer.testing import CliRunner

    from pkmn_quant.cli import app

    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "ingest" in result.output
