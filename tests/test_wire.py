"""Pokemon quote wire: warehouse encoder, replay digests, identical books.

The required proof (`test_wire_and_backtester_build_identical_books`) is
that the replay path (warehouse -> cents on the wire -> decoder -> doubles
`cents / 100.0` -> ladder builder) and the backtester path (PreparedMarket
doubles -> ladder builder) build the same modelled resting orders
(synthetic, built from the day's tcgcsv quote) for every asset-day.

Null market: the warehouse schema allows a null `market`, but the quality
gates (`data/quality.py`, reason "null_market") quarantine every such row
before it is written, so no stored row has one and the identical-books case
for it is skipped. What the wire does with one is pinned separately
(`test_null_market_is_encoded_and_not_laddered`).
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest
from typer.testing import CliRunner

from pkmn_quant import _engine
from pkmn_quant.cli import app
from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA
from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.costs import CostModel
from pkmn_quant.engine.prepared import PreparedMarket
from pkmn_quant.wire import NULL_CENTS, encode_window, to_cents

D0 = date(2026, 6, 1)
DAYS = [D0 + timedelta(days=i) for i in range(5)]
_EPOCH = date(1970, 1, 1)


def _tiers() -> tuple[np.ndarray, np.ndarray, int]:
    cm = CostModel(impact_enabled=True)
    t = np.array([a for a, _ in cm.liquidity_tiers], dtype=np.float64)
    q = np.array([b for _, b in cm.liquidity_tiers], dtype=np.int64)
    return t, q, cm.fallback_max_qty


def _row(
    d: date,
    pid: int,
    st: str,
    market: float | None,
    low: float | None,
    mid: float | None,
    high: float | None,
) -> dict[str, object]:
    return {
        "date": d,
        "product_id": pid,
        "sub_type": st,
        "low": low,
        "mid": mid,
        "high": high,
        "market": market,
    }


def _write(root: Path, rows: list[dict[str, object]]) -> Warehouse:
    w = Warehouse(Paths(root=root))
    df = pl.DataFrame(rows, schema=PRICE_SCHEMA)
    for d in sorted(set(df["date"].to_list())):
        w.write_prices(d, df.filter(pl.col("date") == d))
    pids = sorted(set(df["product_id"].to_list()))
    w.write_products(
        pl.DataFrame(
            {
                "product_id": pids,
                "group_id": [1] * len(pids),
                "name": [f"P{p}" for p in pids],
                "rarity": [None] * len(pids),
                "kind": ["sealed"] * len(pids),
                "released_on": [D0] * len(pids),
            },
            schema={
                "product_id": pl.Int64,
                "group_id": pl.Int64,
                "name": pl.Utf8,
                "rarity": pl.Utf8,
                "kind": pl.Utf8,
                "released_on": pl.Date,
            },
        )
    )
    return w


def _seed_window(root: Path) -> Warehouse:
    """5 days x 4 products, 5 instruments (product 1 has two sub_types).

    Tiers (defaults <5: 20, <50: 8, <200: 3, else 1): product 1 Normal ~$3,
    product 1 Holofoil ~$30, product 2 ~$120, product 3 ~$250 (fallback).
    Product 4 is a penny card. Daily drifts are rounded to cents: 3.17 + 0.05
    is 3.2199999999999998, which the encoder rightly refuses. Edge cases: a
    null low (1/Normal day 1), a null mid (1/Holofoil day 2), a crossed
    quote, mid < market (2, day 3), and a floor case, market < low (3, day 4).
    """
    rows: list[dict[str, object]] = []
    for i, d in enumerate(DAYS):
        rows.append(_row(d, 1, "Normal", round(3.17 + i * 0.05, 2), 2.99, 3.49, 4.10))
        rows.append(_row(d, 1, "Holofoil", round(29.95 + i, 2), 27.50, 33.25, 41.00))
        rows.append(_row(d, 2, "Normal", round(119.99 + i * 2, 2), 110.00, 131.40, 150.00))
        rows.append(_row(d, 3, "Normal", 250.00 + i * 10, 240.00, 275.00, 300.00))
        rows.append(_row(d, 4, "Normal", round(0.02 + i * 0.01, 2), 0.01, 0.05, 0.25))
    rows[1 * 5 + 0]["low"] = None  # day 1, 1/Normal
    rows[2 * 5 + 1]["mid"] = None  # day 2, 1/Holofoil
    rows[3 * 5 + 2]["mid"] = 100.00  # day 3, product 2: crossed (mid < market 125.99)
    rows[4 * 5 + 3]["low"] = 300.00  # day 4, product 3: floor (market 290 < low)
    return _write(root, rows)


def test_to_cents_maps_null_to_sentinel() -> None:
    frame = pl.DataFrame([_row(D0, 1, "Normal", 1.0, None, 0.29, 42949672.94)], schema=PRICE_SCHEMA)
    assert to_cents(frame["low"], what="low", frame=frame).tolist() == [NULL_CENTS]
    assert to_cents(frame["mid"], what="mid", frame=frame).tolist() == [29]
    assert to_cents(frame["high"], what="high", frame=frame).tolist() == [4294967294]
    assert to_cents(frame["high"], what="high", frame=frame).dtype == np.uint32


def test_encode_rejects_non_cent_price(tmp_path: Path) -> None:
    w = _write(tmp_path, [_row(D0, 1, "Normal", 12.345, 12.0, 13.0, 14.0)])
    out = tmp_path / "x.pkw"
    with pytest.raises(ValueError, match=r"product_id=1 .*market"):
        encode_window(w, D0, D0, out)
    assert not out.exists()


def test_encode_rejects_sentinel_overflow(tmp_path: Path) -> None:
    w = _write(tmp_path, [_row(D0, 1, "Normal", 12.0, 11.0, 13.0, 42949672.95)])
    out = tmp_path / "x.pkw"
    with pytest.raises(ValueError, match=r"product_id=1 sub_type=Normal date=2026-06-01 .*high"):
        encode_window(w, D0, D0, out)
    assert not out.exists()


def test_encode_rejects_negative_price(tmp_path: Path) -> None:
    w = _write(tmp_path, [_row(D0, 1, "Normal", 12.0, -1.0, 13.0, 14.0)])
    with pytest.raises(ValueError, match=r"product_id=1 .*low"):
        encode_window(w, D0, D0, tmp_path / "x.pkw")


def test_encode_wire_raises_when_output_cannot_be_opened(tmp_path: Path) -> None:
    w = _write(tmp_path, [_row(D0, 1, "Normal", 12.0, 11.0, 13.0, 14.0)])
    with pytest.raises(OSError):
        encode_window(w, D0, D0, tmp_path / "no-such-dir" / "x.pkw")


def _join(path: Path, w: Warehouse, start: date, end: date) -> tuple[int, int, dict[str, int]]:
    """(rows, mismatches, decode stats); asserts both sides hold the same keys."""
    t, q, fb = _tiers()
    day, pid, st, dig, stats = _engine.wire_ladder_digests(str(path), t, q, fb)
    wire = {(int(d), int(p), s): int(g) for d, p, s, g in zip(day, pid, st, dig, strict=True)}
    assert len(wire) == len(day), "duplicate (day, product_id, sub_type) on the wire side"

    pm = PreparedMarket.prepare(w, start, end)
    assert not np.isnan(pm.row_market).any(), "null market reached PreparedMarket"
    bt_dig = _engine.ladder_digests(
        pm.row_day, pm.row_asset, pm.row_market, pm.row_mid, pm.row_low, t, q, fb
    )
    bt: dict[tuple[int, int, str], int] = {}
    for d, a, g in zip(pm.row_day.tolist(), pm.row_asset.tolist(), bt_dig.tolist(), strict=True):
        asset = pm.asset_list[a]
        bt[(d, asset.product_id, asset.sub_type)] = int(g)
    assert len(bt) == len(pm.row_day)

    assert wire.keys() == bt.keys(), (
        f"join incomplete: {len(wire.keys() - bt.keys())} wire-only, "
        f"{len(bt.keys() - wire.keys())} backtester-only"
    )
    mismatches = sum(1 for k, g in wire.items() if bt[k] != g)
    return len(wire), mismatches, stats


def test_wire_and_backtester_build_identical_books(tmp_path: Path) -> None:
    w = _seed_window(tmp_path)
    out = tmp_path / "w.pkw"
    n_msgs = encode_window(w, DAYS[0], DAYS[-1], out)
    assert n_msgs == 5 + 2 * 5 + 25
    rows, mismatches, stats = _join(out, w, DAYS[0], DAYS[-1])
    assert rows == 25
    assert mismatches == 0
    assert stats["messages"] == n_msgs
    assert {k: v for k, v in stats.items() if k not in ("packets", "messages")} == {
        "unknown": 0,
        "short_messages": 0,
        "short_packets": 0,
        "gaps": 0,
        "gap_messages": 0,
        "duplicates": 0,
        "bad_magic": 0,
    }


def test_digests_distinguish_quotes() -> None:
    """Guard against a digest that ignores its input (a constant would pass
    the identity test trivially)."""
    t, q, fb = _tiers()
    nan = float("nan")
    market = np.array([3.17, 3.18, 3.17, 3.17, 250.0], dtype=np.float64)
    mid = np.array([3.49, 3.49, 3.50, nan, 275.0], dtype=np.float64)
    low = np.array([2.99, 2.99, 2.99, 2.99, 240.0], dtype=np.float64)
    zeros = np.zeros(5, dtype=np.int32)
    dig = _engine.ladder_digests(zeros, zeros, market, mid, low, t, q, fb)
    assert dig.dtype == np.uint64
    assert len(set(dig.tolist())) == 5


def test_null_market_is_encoded_and_not_laddered(tmp_path: Path) -> None:
    """Bypasses the quality gates (which never store a null market): the
    encoder writes the sentinel and the replay skips the quote."""
    w = _write(
        tmp_path,
        [
            _row(D0, 1, "Normal", None, 1.0, 2.0, 3.0),
            _row(D0, 2, "Normal", 5.0, 4.0, 6.0, 7.0),
        ],
    )
    out = tmp_path / "n.pkw"
    assert encode_window(w, D0, D0, out) == 2 + 2 + 2
    t, q, fb = _tiers()
    day, pid, st, dig, stats = _engine.wire_ladder_digests(str(out), t, q, fb)
    assert len(dig) == 1
    assert pid.tolist() == [2]
    assert day.tolist() == [(D0 - _EPOCH).days]
    assert st == ["Normal"]
    assert stats["messages"] == 6


def test_wire_cli_encode(tmp_path: Path) -> None:
    _seed_window(tmp_path)
    out = tmp_path / "data" / "wire" / "w.pkw"
    result = CliRunner().invoke(
        app,
        [
            "wire",
            "encode",
            "--start",
            DAYS[0].isoformat(),
            "--end",
            DAYS[-1].isoformat(),
            "--out",
            str(out),
            "--root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert out.exists()
    instruments, days, quotes = 5, 5, 25
    assert f"messages: {instruments + 2 * days + quotes}" in result.output
    assert f"bytes: {out.stat().st_size}" in result.output
    assert "seconds:" in result.output


def check_real_window(path: str, start: str, end: str, root: str = ".") -> None:
    """Step 5 helper (not collected): the identity check on a real window.

    `path` is a file written by `pkmn wire encode` over [start, end] from
    the warehouse under `root`.
    """
    w = Warehouse(Paths(root=Path(root)))
    rows, mismatches, stats = _join(
        Path(path), w, date.fromisoformat(start), date.fromisoformat(end)
    )
    assert mismatches == 0, f"{mismatches} mismatches of {rows}"
    print(f"rows: {rows}")
    print(f"decode stats: {stats}")
    print("0 mismatches")
