"""The swap gate: every product already in the live warehouse must reproduce
exactly in the shadow. New products are additive and ignored."""

from datetime import date
from pathlib import Path

import polars as pl

from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA
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
