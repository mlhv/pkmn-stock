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
