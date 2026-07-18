"""from_frame(load_prices(), ...) must be indistinguishable from from_warehouse(...)."""

from datetime import date
from pathlib import Path

import polars as pl

from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA
from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.data import MarketData
from tests.helpers import price_row

D = [date(2025, 6, 1), date(2025, 6, 2), date(2025, 6, 4)]  # gap on 06-03


def seed(root: Path) -> Warehouse:
    w = Warehouse(Paths(root=root))
    for i, day in enumerate(D):
        rows = [price_row(day, 1, 10.0 + i), price_row(day, 2, 50.0 - i)]
        w.write_prices(day, pl.DataFrame(rows, schema=PRICE_SCHEMA))
    return w


def test_from_frame_matches_from_warehouse(tmp_path: Path) -> None:
    w = seed(tmp_path)
    a = MarketData.from_warehouse(w, D[0], D[-1], warmup_days=0)
    b = MarketData.from_frame(w.load_prices(), D[0], D[-1], warmup_days=0)
    assert a.days == b.days
    assert a.frame.equals(b.frame)
    assert a.mark_events() == b.mark_events()
    for day in a.days:
        assert a.prices_on(day) == b.prices_on(day)
        assert a.marks_on(day) == b.marks_on(day)


def test_from_frame_applies_warmup_filter(tmp_path: Path) -> None:
    w = seed(tmp_path)
    a = MarketData.from_warehouse(w, D[1], D[-1], warmup_days=1)
    b = MarketData.from_frame(w.load_prices(), D[1], D[-1], warmup_days=1)
    assert a.days == b.days  # trading days exclude the warm-up day
    assert a.frame.equals(b.frame)  # frame includes it
    assert a.mark_events() == b.mark_events()
