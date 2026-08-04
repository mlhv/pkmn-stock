from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

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


def test_tracked_groups_has_no_date_bound() -> None:
    """Since full-set coverage, prices cover every group, matching the
    catalog — including groups published in the future. There is no lower
    OR upper bound: `published_on` is a mutating "last touched" timestamp
    for promo/catch-all groups, not a reliable publication date, so no bound
    against it is safe (see `tracked_groups`'s docstring)."""
    groups = [
        Group(group_id=1, name="Base Set", abbreviation="BS", published_on=date(1999, 1, 9)),
        Group(group_id=2, name="Recent", abbreviation="R", published_on=date(2025, 2, 20)),
        Group(group_id=3, name="Unreleased", abbreviation="U", published_on=date(2027, 1, 1)),
    ]
    tracked = tracked_groups(groups)
    assert [g.group_id for g in tracked] == [1, 2, 3]


def test_tracked_groups_includes_promo_groups_stamped_today() -> None:
    """Regression test for the bug that failed Task 4's real-data rebuild.

    tcgcsv's promo/catch-all groups (POP Series, Nintendo Promos, EX Trainer
    Kits, Burger King Promos, Miscellaneous Cards & Products, and others)
    report `published_on` as whatever day TCGplayer last re-filed a product
    into them, not a release date. During a historical backfill that reads
    as "published in the future" relative to the backfill's own `today`, so
    the old `published_on <= today` bound silently dropped all 19 such
    groups for the ENTIRE 907-day rebuild even though their price files were
    present in the archive every day. This test mimics exactly that: a group
    whose `published_on` is later than a caller's `today` must still be
    tracked.
    """
    groups = [
        Group(
            group_id=24541,
            name="Miscellaneous Cards & Products",
            abbreviation="MISC",
            published_on=date(2026, 8, 3),
        ),
    ]
    tracked = tracked_groups(groups)
    assert [g.group_id for g in tracked] == [24541]


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
    report = ingest_range(Paths(root=root), d1, d2, client=client)

    assert [s.day for s in report.days] == [d1, d2]
    assert report.days[0].rows_clean == 1
    # 10.0 -> 500.0 is a 50x jump: quarantined by the price_jump gate.
    assert report.days[1].rows_clean == 0
    assert report.days[1].rows_quarantined == 1
    assert report.catalog_failures == ()

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
    assert len(first.days) == 1
    assert second.days == []
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
    report = ingest_range(Paths(root=root), d2, d2, client=client)
    assert report.days[0].rows_clean == 1
    assert report.days[0].rows_quarantined == 0


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
    report = ingest_range(Paths(root=root), d_early, d_early, client=client)
    assert report.days[0].rows_clean == 1
    assert report.days[0].rows_quarantined == 0


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
    refresh = refresh_products(client, warehouse, groups)

    assert refresh.rows == 1, f"expected the duplicate to be collapsed, got {refresh.rows} rows"
    assert warehouse.load_products()["product_id"].to_list() == [666906]


# ---------------------------------------------------------------------------
# Catalog durability: a partial fetch must never destroy a good catalog.
# ---------------------------------------------------------------------------

BIG_GROUP, SMALL_GROUP = GROUP_ID, 3170


def _bulk_products(group_id: int, first_id: int, n: int) -> dict[str, Any]:
    """A products payload with n rows, so a one-group loss is a small fraction."""
    return {
        "success": True,
        "results": [
            {
                "productId": first_id + i,
                "name": f"P{first_id + i}",
                "groupId": group_id,
                "presaleInfo": {"releasedOn": "2025-02-20T00:00:00"},
                "extendedData": [],
            }
            for i in range(n)
        ],
    }


def _two_groups() -> list[Group]:
    return [
        Group(group_id=BIG_GROUP, name="A", abbreviation="A", published_on=date(2025, 2, 20)),
        Group(group_id=SMALL_GROUP, name="B", abbreviation="B", published_on=date(2020, 1, 1)),
    ]


def test_refresh_products_keeps_rows_of_a_group_that_failed_this_run(tmp_path: Path) -> None:
    """A transient failure for one group must NOT delete that group's products.

    Observed in production 2026-07-30 (data/results/daily.log): one
    "[Errno 54] Connection reset by peer" on group 2534 during the unattended
    09:00 run silently removed that group from the catalog, and the run still
    reported success.
    """
    from pkmn_quant.data.ingest import refresh_products

    fail = {"on": False}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith(f"/{BIG_GROUP}/products"):
            return httpx.Response(200, json=_bulk_products(BIG_GROUP, 1000, 40))
        if path.endswith(f"/{SMALL_GROUP}/products"):
            if fail["on"]:
                raise httpx.ConnectError("[Errno 54] Connection reset by peer")
            return httpx.Response(200, json=_bulk_products(SMALL_GROUP, 90000, 2))
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    warehouse = Warehouse(Paths(root=tmp_path))
    groups = _two_groups()
    first = refresh_products(client, warehouse, groups)
    assert first.rows == 42 and first.failed_groups == ()

    fail["on"] = True
    second = refresh_products(client, warehouse, groups)

    ids = set(warehouse.load_products()["product_id"].to_list())
    assert {90000, 90001} <= ids, "products of a group that failed THIS run were dropped"
    assert second.rows == 42
    assert second.failed_groups == (SMALL_GROUP,), "the failure was not reported to the caller"


def test_refresh_products_fresh_row_wins_over_the_stored_one(tmp_path: Path) -> None:
    """Merging must not freeze the catalog: a genuine upstream update wins."""
    from pkmn_quant.data.ingest import refresh_products

    name = {"value": "Old Name"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(f"/{BIG_GROUP}/products"):
            return httpx.Response(200, json=_per_group_products(BIG_GROUP, 666906, name["value"]))
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    warehouse = Warehouse(Paths(root=tmp_path))
    groups = [Group(group_id=BIG_GROUP, name="A", abbreviation="A", published_on=date(2025, 2, 20))]
    refresh_products(client, warehouse, groups)

    name["value"] = "New Name"
    refresh_products(client, warehouse, groups)

    products = warehouse.load_products()
    assert products.height == 1
    assert products["name"].to_list() == ["New Name"], "the stale stored row won over the fresh one"


def test_refresh_products_refuses_to_write_an_undersized_catalog(tmp_path: Path) -> None:
    """A mass fetch failure must raise, not quietly shrink the catalog."""
    from pkmn_quant.data.ingest import refresh_products

    fail = {"on": False}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith(f"/{BIG_GROUP}/products"):
            if fail["on"]:
                raise httpx.ConnectError("network down")
            return httpx.Response(200, json=_bulk_products(BIG_GROUP, 1000, 40))
        if path.endswith(f"/{SMALL_GROUP}/products"):
            return httpx.Response(200, json=_bulk_products(SMALL_GROUP, 90000, 2))
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    warehouse = Warehouse(Paths(root=tmp_path))
    groups = _two_groups()
    refresh_products(client, warehouse, groups)
    before = warehouse.load_products()

    fail["on"] = True
    with pytest.raises(RuntimeError, match="catalog"):
        refresh_products(client, warehouse, groups)

    after = warehouse.load_products()
    assert after.height == before.height == 42, "the existing catalog was overwritten anyway"


def test_refresh_products_survives_malformed_json(tmp_path: Path) -> None:
    """A 200 carrying non-JSON raises json.JSONDecodeError (a ValueError) out of
    tcgcsv.fetch_products; before the fix that aborted the whole ingest."""
    from pkmn_quant.data.ingest import refresh_products

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith(f"/{BIG_GROUP}/products"):
            return httpx.Response(200, content=b"<html>502 Bad Gateway</html>")
        if path.endswith(f"/{SMALL_GROUP}/products"):
            return httpx.Response(200, json=_per_group_products(SMALL_GROUP, 83542, "Ancient Mew"))
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    warehouse = Warehouse(Paths(root=tmp_path))
    refresh = refresh_products(client, warehouse, _two_groups())

    assert refresh.failed_groups == (BIG_GROUP,)
    assert warehouse.load_products()["product_id"].to_list() == [83542]


def test_refresh_products_survives_a_payload_missing_results(tmp_path: Path) -> None:
    """A JSON body with no "results" key raises KeyError out of fetch_products;
    before the fix that aborted the whole ingest."""
    from pkmn_quant.data.ingest import refresh_products

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith(f"/{BIG_GROUP}/products"):
            return httpx.Response(200, json={"success": False, "errors": ["rate limited"]})
        if path.endswith(f"/{SMALL_GROUP}/products"):
            return httpx.Response(200, json=_per_group_products(SMALL_GROUP, 83542, "Ancient Mew"))
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    warehouse = Warehouse(Paths(root=tmp_path))
    refresh = refresh_products(client, warehouse, _two_groups())

    assert refresh.failed_groups == (BIG_GROUP,)
    assert warehouse.load_products()["product_id"].to_list() == [83542]
