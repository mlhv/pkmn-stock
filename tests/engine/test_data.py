from datetime import date
from pathlib import Path

import polars as pl
import pytest

from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA
from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.data import MarketData
from pkmn_quant.engine.portfolio import Asset
from tests.helpers import price_row

D1, D2, D3 = date(2025, 6, 1), date(2025, 6, 2), date(2025, 6, 3)
A1 = Asset(product_id=1, sub_type="Normal")
A2 = Asset(product_id=2, sub_type="Normal")

row = price_row


@pytest.fixture
def market(tmp_path: Path) -> MarketData:
    w = Warehouse(Paths(root=tmp_path))
    w.write_prices(D1, pl.DataFrame([row(D1, 1, 10.0), row(D1, 2, 5.0)], schema=PRICE_SCHEMA))
    # product 2 does not trade on D2
    w.write_prices(D2, pl.DataFrame([row(D2, 1, 11.0)], schema=PRICE_SCHEMA))
    w.write_prices(D3, pl.DataFrame([row(D3, 1, 12.0), row(D3, 2, 6.0)], schema=PRICE_SCHEMA))
    return MarketData.from_warehouse(w, start=D1, end=D3)


def test_trading_days(market: MarketData) -> None:
    assert market.days == [D1, D2, D3]


def test_prices_on_day(market: MarketData) -> None:
    assert market.prices_on(D1) == {A1: 10.0, A2: 5.0}
    assert market.prices_on(D2) == {A1: 11.0}


def test_marks_carry_forward_missing_assets(market: MarketData) -> None:
    assert market.marks_on(D2) == {A1: 11.0, A2: 5.0}  # A2 carried from D1
    assert market.marks_on(D3) == {A1: 12.0, A2: 6.0}


def test_history_excludes_future(market: MarketData) -> None:
    h = market.history_until(D2)
    assert h["date"].max() == D2
    assert h.height == 3  # 2 rows on D1 + 1 on D2


def test_range_filtering(tmp_path: Path) -> None:
    w = Warehouse(Paths(root=tmp_path))
    for d in (D1, D2, D3):
        w.write_prices(d, pl.DataFrame([row(d, 1, 10.0)], schema=PRICE_SCHEMA))
    md = MarketData.from_warehouse(w, start=D2, end=D3)
    assert md.days == [D2, D3]
    assert md.history_until(D3)["date"].min() == D2


# ---------------------------------------------------------------------------
# Warm-up tests
# ---------------------------------------------------------------------------
# Fixture: 7 days of data (D_W0 through D_W6).  We treat D_W5..D_W6 as the
# "trading window" and D_W0..D_W4 as the warm-up.

_D_W = [date(2025, 7, d) for d in range(1, 8)]  # 2025-07-01 .. 2025-07-07
_WARMUP_START = _D_W[5]  # 2025-07-06 -- start of the trading window
_WARMUP_END = _D_W[6]  # 2025-07-07 -- end of the trading window
_WARMUP_DAYS = 5  # load D_W0 through D_W4 as warm-up


@pytest.fixture
def warmup_warehouse(tmp_path: Path) -> Warehouse:
    w = Warehouse(Paths(root=tmp_path))
    for i, d in enumerate(_D_W):
        # product 1 prints every day; product 2 only on days 0..4 (warm-up only)
        rows = [row(d, 1, float(10 + i))]
        if i < 5:
            rows.append(row(d, 2, float(50 + i)))
        w.write_prices(d, pl.DataFrame(rows, schema=PRICE_SCHEMA))
    return w


def test_warmup_days_not_in_days_list(warmup_warehouse: Warehouse) -> None:
    """days must only contain [start, end]; warm-up rows must not appear."""
    md = MarketData.from_warehouse(
        warmup_warehouse, start=_WARMUP_START, end=_WARMUP_END, warmup_days=_WARMUP_DAYS
    )
    assert md.days == [_WARMUP_START, _WARMUP_END]


def test_warmup_rows_visible_in_history(warmup_warehouse: Warehouse) -> None:
    """history_until(start) must include rows from the warm-up period."""
    md = MarketData.from_warehouse(
        warmup_warehouse, start=_WARMUP_START, end=_WARMUP_END, warmup_days=_WARMUP_DAYS
    )
    h = md.history_until(_WARMUP_START)
    dates_in_history = sorted(h["date"].unique().to_list())
    # All 5 warm-up days plus start itself must be present
    assert _D_W[0] in dates_in_history, "earliest warm-up day must be in history"
    assert _WARMUP_START in dates_in_history


def test_warmup_marks_carry_forward_from_warmup(warmup_warehouse: Warehouse) -> None:
    """marks_on(start) must carry forward a product that last printed in warm-up."""
    md = MarketData.from_warehouse(
        warmup_warehouse, start=_WARMUP_START, end=_WARMUP_END, warmup_days=_WARMUP_DAYS
    )
    # product 2 printed last on _D_W[4] (price 54.0); it doesn't print in
    # [_WARMUP_START, _WARMUP_END], so marks_on should carry it forward.
    marks = md.marks_on(_WARMUP_START)
    A2_warmup = Asset(product_id=2, sub_type="Normal")
    assert A2_warmup in marks, "product 2 mark must be carried forward from warm-up"
    assert marks[A2_warmup] == pytest.approx(54.0)


def test_warmup_zero_behaves_identically(warmup_warehouse: Warehouse) -> None:
    """warmup_days=0 (default) must give the same result as no warmup_days arg."""
    md_default = MarketData.from_warehouse(warmup_warehouse, start=_WARMUP_START, end=_WARMUP_END)
    md_explicit = MarketData.from_warehouse(
        warmup_warehouse, start=_WARMUP_START, end=_WARMUP_END, warmup_days=0
    )
    assert md_default.days == md_explicit.days
    h_default = md_default.history_until(_WARMUP_END).height
    h_explicit = md_explicit.history_until(_WARMUP_END).height
    assert h_default == h_explicit
