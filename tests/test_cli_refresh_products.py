"""`pkmn refresh-products` — repair the catalog without a price ingest."""

from datetime import date
from pathlib import Path
from typing import Any

import httpx
import polars as pl
from typer.testing import CliRunner

from pkmn_quant.cli import app
from pkmn_quant.config import Paths
from pkmn_quant.data.warehouse import Warehouse

runner = CliRunner()

GROUPS_JSON: dict[str, Any] = {
    "success": True,
    "results": [
        {
            "groupId": 24541,
            "name": "ME: Ascended Heroes",
            "abbreviation": "MEG",
            "publishedOn": "2025-02-20T00:00:00",
            "categoryId": 3,
        },
        {
            "groupId": 2374,
            "name": "Miscellaneous Cards & Products",
            "abbreviation": "MISC",
            "publishedOn": "2010-01-01T00:00:00",
            "categoryId": 3,
        },
    ],
}


def _products(group_id: int, product_id: int, name: str) -> dict[str, Any]:
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


def _client() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/groups"):
            return httpx.Response(200, json=GROUPS_JSON)
        if path.endswith("/24541/products"):
            return httpx.Response(200, json=_products(24541, 666906, "Collection - Erika"))
        if path.endswith("/2374/products"):
            return httpx.Response(200, json=_products(2374, 83542, "Ancient Mew"))
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_refresh_products_writes_all_groups(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("pkmn_quant.cli.tcgcsv.make_client", _client)
    result = runner.invoke(app, ["refresh-products", "--root", str(tmp_path)])

    assert result.exit_code == 0, result.output
    ids = set(Warehouse(Paths(root=tmp_path)).load_products()["product_id"].to_list())
    assert ids == {666906, 83542}
    assert "wrote 2 products" in result.output  # reports how many products were written


def test_refresh_products_merges_into_an_existing_catalog(tmp_path: Path, monkeypatch) -> None:
    """The command repairs a stale catalog by MERGING into it, not replacing it.

    Replacing meant one failed group deleted its products (see
    tests/test_ingest.py::test_refresh_products_keeps_rows_of_a_group_that_
    failed_this_run). The deliberate cost of merging is that a row we no
    longer fetch — like the 111 below — survives; stale metadata for a
    product the price feed no longer lists is harmless, losing a row is not.
    """
    wh = Warehouse(Paths(root=tmp_path))
    wh.write_products(
        pl.DataFrame(
            {
                "product_id": [111],
                "group_id": [1],
                "name": ["Stale"],
                "rarity": [None],
                "kind": ["other"],
                "released_on": [date(2020, 1, 1)],
            }
        )
    )
    monkeypatch.setattr("pkmn_quant.cli.tcgcsv.make_client", _client)
    result = runner.invoke(app, ["refresh-products", "--root", str(tmp_path)])

    assert result.exit_code == 0, result.output
    ids = set(wh.load_products()["product_id"].to_list())
    assert {666906, 83542} <= ids, "the freshly fetched products were lost"
    assert 111 in ids, "merge must keep rows no group returned this run"


def test_refresh_products_reports_failed_groups(tmp_path: Path, monkeypatch) -> None:
    """An unattended degradation must be visible, not just a stderr warning
    buried mid-run: the command reports how many groups failed."""

    def flaky_client() -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.endswith("/groups"):
                return httpx.Response(200, json=GROUPS_JSON)
            if path.endswith("/24541/products"):
                return httpx.Response(200, json=_products(24541, 666906, "Collection - Erika"))
            if path.endswith("/2374/products"):
                raise httpx.ConnectError("[Errno 54] Connection reset by peer")
            return httpx.Response(404)

        return httpx.Client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr("pkmn_quant.cli.tcgcsv.make_client", flaky_client)
    result = runner.invoke(app, ["refresh-products", "--root", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "wrote 1 products" in result.output
    assert "1 of 2 groups failed to fetch" in result.output
    assert "2374" in result.output
