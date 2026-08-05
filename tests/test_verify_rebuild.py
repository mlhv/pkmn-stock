"""The swap gate: every product already in the live warehouse must reproduce
exactly in the shadow. New products are additive and ignored."""

from datetime import date
from pathlib import Path

import polars as pl

from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA, PRODUCT_SCHEMA
from pkmn_quant.data.warehouse import Warehouse
from scripts.verify_rebuild import verify

D = date(2025, 6, 1)


def _row(pid: int, market: float) -> dict[str, object]:
    return {
        "date": D,
        "product_id": pid,
        "sub_type": "Normal",
        "low": 1.0,
        "mid": 2.0,
        "high": 3.0,
        "market": market,
    }


def _catalog(pids: list[int]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "product_id": pid,
                "group_id": 1,
                "name": f"p{pid}",
                "rarity": None,
                "kind": "single",
                "released_on": D,
            }
            for pid in pids
        ],
        schema=PRODUCT_SCHEMA,
    )


def _write(root: Path, name: str, rows: list[dict[str, object]], day: date = D) -> Warehouse:
    wh = Warehouse(Paths(root=root, warehouse_name=name))
    wh.write_prices(day, pl.DataFrame(rows, schema=PRICE_SCHEMA))
    return wh


def test_clean_when_shadow_is_a_superset(tmp_path: Path) -> None:
    _write(tmp_path, "warehouse", [_row(1, 10.0)])
    _write(tmp_path, "warehouse-full", [_row(1, 10.0), _row(2, 99.0)])
    assert verify(tmp_path, "warehouse-full") == []


def test_detects_a_changed_price(tmp_path: Path) -> None:
    _write(tmp_path, "warehouse", [_row(1, 10.0)])
    _write(tmp_path, "warehouse-full", [_row(1, 10.5), _row(2, 99.0)])
    problems = verify(tmp_path, "warehouse-full")
    assert problems and any("2025-06-01" in p for p in problems)


def test_detects_a_missing_product(tmp_path: Path) -> None:
    _write(tmp_path, "warehouse", [_row(1, 10.0), _row(3, 5.0)])
    _write(tmp_path, "warehouse-full", [_row(1, 10.0)])
    assert verify(tmp_path, "warehouse-full") != []


def test_detects_a_missing_day(tmp_path: Path) -> None:
    _write(tmp_path, "warehouse", [_row(1, 10.0)])
    _write(tmp_path, "warehouse", [_row(1, 11.0)], day=date(2025, 6, 2))
    _write(tmp_path, "warehouse-full", [_row(1, 10.0)])
    problems = verify(tmp_path, "warehouse-full")
    assert problems and any("2025-06-02" in p for p in problems)


def test_detects_a_flipped_quarantine_decision(tmp_path: Path) -> None:
    """A quarantine decision that changed for an existing product means the
    gate chain diverged, even if the clean rows happen to match."""
    live = _write(tmp_path, "warehouse", [_row(1, 10.0)])
    _write(tmp_path, "warehouse-full", [_row(1, 10.0)])
    q = pl.DataFrame([{**_row(9, 0.0), "reason": "nonpositive_market"}])
    live.write_quarantine(D, q)
    # shadow writes no quarantine for product 9 -> divergence
    assert verify(tmp_path, "warehouse-full") != []


def test_detects_a_catalog_product_dropped_by_the_shadow(tmp_path: Path) -> None:
    """A per-group fetch failure during a rebuild ships a catalog SMALLER than
    the live one, which no price comparison can see: the prices of a product
    that lost its catalog row still reproduce exactly. Unlike the live
    catalog, a shadow's is a single fresh fetch with nothing to merge against
    and no CATALOG_FLOOR to refuse it, so this must fail the gate."""
    live = _write(tmp_path, "warehouse", [_row(1, 10.0)])
    shadow = _write(tmp_path, "warehouse-full", [_row(1, 10.0), _row(2, 99.0)])
    live.write_products(_catalog([1, 2]))
    shadow.write_products(_catalog([1]))  # the group holding product 2 failed to fetch
    problems = verify(tmp_path, "warehouse-full")
    # "(2)" is the id list, not the count — pin the id so a message that
    # merely counts a loss without naming it cannot satisfy this.
    assert problems and any(p.startswith("catalog:") and p.endswith("(2)") for p in problems)


def test_detects_a_shadow_with_no_catalog_at_all(tmp_path: Path) -> None:
    live = _write(tmp_path, "warehouse", [_row(1, 10.0)])
    _write(tmp_path, "warehouse-full", [_row(1, 10.0)])
    live.write_products(_catalog([1]))
    assert any("catalog" in p for p in verify(tmp_path, "warehouse-full"))


def test_clean_when_the_shadow_catalog_is_a_superset(tmp_path: Path) -> None:
    live = _write(tmp_path, "warehouse", [_row(1, 10.0)])
    shadow = _write(tmp_path, "warehouse-full", [_row(1, 10.0), _row(2, 99.0)])
    live.write_products(_catalog([1]))
    shadow.write_products(_catalog([1, 2]))  # wider by design — additive, not a divergence
    assert verify(tmp_path, "warehouse-full") == []
