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
    # The already-stored day's price archive must not be re-downloaded. (The
    # catalog, unlike price archives, IS re-fetched on every ingest_range call
    # — see test_ingest_refreshes_catalog_even_when_products_exist.)
    archive_suffix = f"prices-{d1.isoformat()}.ppmd.7z"
    assert not any(c.endswith(archive_suffix) for c in calls[calls_after_first:])


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


def test_backfill_earlier_day_does_not_use_future_baseline(tmp_path: Path) -> None:
    d_early, d_late = date(2025, 6, 1), date(2025, 6, 5)
    archives = {
        d_early: make_archive(tmp_path / "fe", d_early, GROUP_ID, [price_row(500.0)]),
        d_late: make_archive(tmp_path / "fl", d_late, GROUP_ID, [price_row(10.0)]),
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
    ingest_range(Paths(root=root), d_late, d_late, client=client)
    # Backfilling June 1 after June 5 exists: the June 5 frame (a FUTURE day)
    # must not serve as the jump baseline, so the 50x difference is clean.
    stats = ingest_range(Paths(root=root), d_early, d_early, client=client)
    assert stats[0].rows_clean == 1
    assert stats[0].rows_quarantined == 0


def _per_group_products(group_id: int, product_id: int, name: str) -> dict[str, Any]:
    return {
        "success": True,
        "results": [
            {
                "productId": product_id,
                "name": name,
                "groupId": group_id,
                "presaleInfo": {"releasedOn": "2025-02-20T00:00:00"},
                "extendedData": [],
            }
        ],
    }


def test_refresh_products_catalogs_untracked_groups(tmp_path: Path) -> None:
    """The catalog must cover EVERY group, not just the price-tracked ones.

    TCGplayer re-files products between groups over time, so a product whose
    prices were archived under a tracked set now lists under an untracked
    promo group. Asking only tracked groups leaves it nameless and therefore
    kind "other", invisible to every kind-filtered strategy.
    """
    d1 = date(2025, 6, 1)
    archives = {d1: make_archive(tmp_path / "fx", d1, GROUP_ID, [price_row(10.0)])}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/groups"):
            return httpx.Response(200, json=GROUPS_JSON)
        if path.endswith(f"/{GROUP_ID}/products"):
            return httpx.Response(
                200, json=_per_group_products(GROUP_ID, 666906, "Collection - Erika")
            )
        if path.endswith("/3170/products"):
            return httpx.Response(200, json=_per_group_products(3170, 83542, "Ancient Mew"))
        for day, archive in archives.items():
            if path.endswith(f"prices-{day.isoformat()}.ppmd.7z"):
                return httpx.Response(200, content=archive.read_bytes())
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    root = tmp_path / "proj"
    ingest_range(Paths(root=root), d1, d1, client=client)

    products = Warehouse(Paths(root=root)).load_products()
    ids = set(products["product_id"].to_list())
    assert 666906 in ids  # tracked group, as before
    assert 83542 in ids  # UNTRACKED group 3170 — the whole point


def test_ingest_refreshes_catalog_even_when_products_exist(tmp_path: Path) -> None:
    """A second ingest must re-fetch the catalog. Previously refresh_products
    ran only when products.parquet was missing, so a product re-filed into a
    new group stayed nameless forever."""
    d1, d2 = date(2025, 6, 1), date(2025, 6, 2)
    archives = {
        d1: make_archive(tmp_path / "fx1", d1, GROUP_ID, [price_row(10.0)]),
        d2: make_archive(tmp_path / "fx2", d2, GROUP_ID, [price_row(10.5)]),
    }
    seen: list[str] = []
    extra = {"on": False}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/groups"):
            return httpx.Response(200, json=GROUPS_JSON)
        if path.endswith("/products"):
            seen.append(path)
            if path.endswith("/3170/products") and extra["on"]:
                return httpx.Response(200, json=_per_group_products(3170, 999, "Late Promo"))
            gid = GROUP_ID if f"/{GROUP_ID}/" in path else 3170
            pid = 666906 if gid == GROUP_ID else 83542
            return httpx.Response(200, json=_per_group_products(gid, pid, "X"))
        for day, archive in archives.items():
            if path.endswith(f"prices-{day.isoformat()}.ppmd.7z"):
                return httpx.Response(200, content=archive.read_bytes())
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    root = tmp_path / "proj"
    ingest_range(Paths(root=root), d1, d1, client=client)
    first_calls = len(seen)
    assert first_calls > 0

    extra["on"] = True
    ingest_range(Paths(root=root), d2, d2, client=client)
    assert len(seen) > first_calls, "second ingest did not re-fetch the catalog"

    ids = set(Warehouse(Paths(root=root)).load_products()["product_id"].to_list())
    assert 999 in ids, "a newly re-filed product was not picked up"


def test_refresh_products_dedupes_by_product_id(tmp_path: Path) -> None:
    """Fetching every group means a product listed under two groups would
    otherwise appear twice, and a duplicate product row fans out every join
    against the price frame."""
    from pkmn_quant.data.ingest import refresh_products

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/products"):
            return httpx.Response(200, json=_per_group_products(GROUP_ID, 666906, "Dup"))
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    warehouse = Warehouse(Paths(root=tmp_path))
    groups = [
        Group(group_id=GROUP_ID, name="A", abbreviation="A", published_on=date(2025, 2, 20)),
        Group(group_id=3170, name="B", abbreviation="B", published_on=date(2020, 1, 1)),
    ]
    rows = refresh_products(client, warehouse, groups)

    assert rows == 1, f"expected the duplicate to be collapsed, got {rows} rows"
    assert warehouse.load_products()["product_id"].to_list() == [666906]
