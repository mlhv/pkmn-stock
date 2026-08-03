"""The shadow rebuild: full-set prices into an alternate warehouse, reading
the SAME cached archives, never touching the live warehouse."""

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
import polars as pl

from pkmn_quant.config import Paths
from pkmn_quant.data.warehouse import Warehouse
from scripts.rebuild_warehouse import rebuild
from tests.test_tcgcsv import make_archive

D1 = date(2025, 6, 1)

GROUPS_JSON: dict[str, Any] = {
    "success": True,
    "results": [
        {
            "groupId": 100,
            "name": "Recent",
            "abbreviation": "R",
            "publishedOn": "2025-02-20T00:00:00",
            "categoryId": 3,
        },
        {
            "groupId": 200,
            "name": "Ancient",
            "abbreviation": "A",
            "publishedOn": "1999-01-09T00:00:00",
            "categoryId": 3,
        },
    ],
}


def _products(group_id: int, product_id: int) -> dict[str, Any]:
    return {
        "success": True,
        "results": [
            {
                "productId": product_id,
                "name": f"P{product_id}",
                "groupId": group_id,
                "presaleInfo": {"releasedOn": "2025-02-20T00:00:00"},
                "extendedData": [],
            }
        ],
    }


def _price(product_id: int, market: float) -> dict[str, Any]:
    return {
        "productId": product_id,
        "lowPrice": 1.0,
        "midPrice": 2.0,
        "highPrice": 3.0,
        "marketPrice": market,
        "directLowPrice": None,
        "subTypeName": "Normal",
    }


def _client(tmp_path: Path, days: int = 3) -> httpx.Client:
    archives = {}
    for i in range(days):
        d = D1 + timedelta(days=i)
        archives[d] = make_archive(
            tmp_path / f"fx{i}",
            d,
            100,
            [_price(1001, 10.0 + i)],
            extra_groups={200: [_price(2001, 50.0 + i)]},
        )

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/groups"):
            return httpx.Response(200, json=GROUPS_JSON)
        if path.endswith("/100/products"):
            return httpx.Response(200, json=_products(100, 1001))
        if path.endswith("/200/products"):
            return httpx.Response(200, json=_products(200, 2001))
        for d, a in archives.items():
            if path.endswith(f"prices-{d.isoformat()}.ppmd.7z"):
                return httpx.Response(200, content=a.read_bytes())
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _jump_client(tmp_path: Path) -> httpx.Client:
    """Archives where product 1001's day-over-day move only violates the
    price-jump gate (JUMP_FACTOR = 10.0, `data/quality.py`) between D1 and
    D1+1: 10.0 -> 200.0 is a 20x move, quarantined ONLY when D1's CLEAN
    output (market=10.0) is honored as D1+1's baseline. A regression that
    reset `previous` to None on the first resumed day would have nothing to
    compare against and pass this row through clean instead — that
    difference is what `test_rebuild_is_resumable` checks for.
    """
    schedule = {0: 10.0, 1: 200.0, 2: 210.0}
    archives = {}
    for i, market in schedule.items():
        d = D1 + timedelta(days=i)
        archives[d] = make_archive(tmp_path / f"jx{i}", d, 100, [_price(1001, market)])

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/groups"):
            return httpx.Response(200, json=GROUPS_JSON)
        if path.endswith("/100/products"):
            return httpx.Response(200, json=_products(100, 1001))
        if path.endswith("/200/products"):
            return httpx.Response(200, json=_products(200, 2001))
        for d, a in archives.items():
            if path.endswith(f"prices-{d.isoformat()}.ppmd.7z"):
                return httpx.Response(200, content=a.read_bytes())
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_rebuild_writes_the_shadow_and_includes_ancient_sets(tmp_path: Path) -> None:
    client = _client(tmp_path)
    rebuild(
        root=tmp_path,
        warehouse_name="warehouse-full",
        start=D1,
        end=D1 + timedelta(days=2),
        client=client,
    )

    shadow = Warehouse(Paths(root=tmp_path, warehouse_name="warehouse-full"))
    assert shadow.stored_days() == [D1 + timedelta(days=i) for i in range(3)]
    ids = set(shadow.load_prices()["product_id"].to_list())
    assert ids == {1001, 2001}, "the 1999 set's product must be ingested"
    assert set(shadow.load_products()["product_id"].to_list()) == {1001, 2001}


def test_rebuild_never_touches_the_live_warehouse(tmp_path: Path) -> None:
    live = Warehouse(Paths(root=tmp_path))
    live.write_products(
        pl.DataFrame(
            {
                "product_id": [7],
                "group_id": [1],
                "name": ["Live"],
                "rarity": [None],
                "kind": ["sealed"],
                "released_on": [date(2024, 1, 1)],
            }
        )
    )

    rebuild(
        root=tmp_path,
        warehouse_name="warehouse-full",
        start=D1,
        end=D1 + timedelta(days=2),
        client=_client(tmp_path),
    )

    assert live.load_products()["product_id"].to_list() == [7]
    assert live.stored_days() == []


def test_rebuild_is_resumable(tmp_path: Path) -> None:
    """A crashed rebuild must resume, and the resumed days must use the prior
    day's CLEAN output as their gate baseline — the chain cannot restart."""
    rebuild(
        root=tmp_path,
        warehouse_name="warehouse-full",
        start=D1,
        end=D1,
        client=_jump_client(tmp_path),
    )
    shadow = Warehouse(Paths(root=tmp_path, warehouse_name="warehouse-full"))
    assert shadow.stored_days() == [D1]

    stats = rebuild(
        root=tmp_path,
        warehouse_name="warehouse-full",
        start=D1,
        end=D1 + timedelta(days=2),
        client=_jump_client(tmp_path),
    )
    assert shadow.stored_days() == [D1 + timedelta(days=i) for i in range(3)]
    assert [s.day for s in stats] == [D1 + timedelta(days=1), D1 + timedelta(days=2)], (
        "the already-stored day must be skipped, not re-ingested"
    )

    # The 20x jump (10.0 -> 200.0) between D1 and D1+1 is only a violation of
    # the price-jump gate if D1's CLEAN output is honored as the baseline for
    # the first resumed day — prove the resumed run actually did that instead
    # of restarting the chain from an unseeded `previous`.
    day1_stats = stats[0]
    assert day1_stats.day == D1 + timedelta(days=1)
    assert (day1_stats.rows_clean, day1_stats.rows_quarantined) == (0, 1), (
        "D1+1's 20x jump over D1's clean baseline must be quarantined, not passed clean — "
        "a `previous=None` restart would have nothing to compare against and let it through"
    )
    prices = shadow.load_prices()
    assert prices.filter(pl.col("date") == D1 + timedelta(days=1)).height == 0, (
        "the jumped row must not have entered the clean warehouse"
    )
