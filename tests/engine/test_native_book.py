"""NativeBacktest book execution (Plan B Task 2).

Fills against a ladder of modelled resting orders (synthetic, built from the
day's tcgcsv quote), priced in whole cents. Pins the cent golden on a
controlled 1-asset warehouse and checks the formula default is untouched.
"""

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA
from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.backtest import Result
from pkmn_quant.engine.costs import CostModel
from pkmn_quant.engine.native import NativeBacktest, NativeStrategySpec
from tests.helpers import price_row

D1, D2, D3 = date(2025, 6, 1), date(2025, 6, 2), date(2025, 6, 3)


def _seed(root: Path) -> Warehouse:
    """One sealed box: market 10/12/15, mid 11/13/16, every low 1.0."""
    w = Warehouse(Paths(root=root))
    for day, market, mid in ((D1, 10.0, 11.0), (D2, 12.0, 13.0), (D3, 15.0, 16.0)):
        r = price_row(day, 1, market)
        r["mid"] = mid
        w.write_prices(day, pl.DataFrame([r], schema=PRICE_SCHEMA))
    w.write_products(
        pl.DataFrame(
            {
                "product_id": [1],
                "group_id": [1],
                "name": ["Box"],
                "rarity": [None],
                "kind": ["sealed"],
                "released_on": [D1],
            }
        )
    )
    return w


def _run(tmp_path: Path, **kw: str) -> Result:
    bt = NativeBacktest(
        warehouse=_seed(tmp_path),
        strategy=NativeStrategySpec("buy-and-hold", {}),
        cost_model=CostModel(impact_enabled=True),
        start=D1,
        end=D3,
        initial_cash=100.0,
        **kw,  # type: ignore[arg-type]
    )
    return bt.run()


def test_book_cent_golden_through_native(tmp_path: Path) -> None:
    # D2 ask ladder on 12/13: 12.06, 12.19, ..., 12.94 (cumsum 87.06 at 7,
    # 100.00 at 8). 7 units + 1.00 shipping = 88.06 <= 100; 8 = 101 > 100.
    # Cash 100 - 84 - 1 - 3.06 = 11.94; equity 11.94 + 7*12, then + 7*15.
    res = _run(tmp_path, execution="book")
    assert res.equity_curve["equity"].to_list() == pytest.approx([100.0, 95.94, 116.94], abs=1e-9)
    assert len(res.fills) == 1
    assert res.fills[0].quantity == 7
    assert res.fills[0].impact == pytest.approx(3.06, abs=1e-9)
    assert res.cost_model["execution"] == "book"


def test_formula_default_unchanged(tmp_path: Path) -> None:
    # Formula: spread 1, cap 8; impact(7) = 1*7*7/16 = 3.0625.
    res = _run(tmp_path)
    assert res.equity_curve["equity"].to_list() == pytest.approx([100.0, 95.9375, 116.9375])
    assert len(res.fills) == 1
    assert res.fills[0].impact == pytest.approx(3.0625)
    assert "execution" not in res.cost_model
    assert res.cost_model == CostModel(impact_enabled=True).as_dict()


def test_unknown_execution_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="execution"):
        _run(tmp_path, execution="fast")


def test_binding_rejects_unknown_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Bypass the Python-side check so the C++ binding's own validation fires.
    from pkmn_quant.engine import native

    monkeypatch.setattr(native, "EXECUTION_MODES", frozenset({"fast"}))
    with pytest.raises(ValueError, match="unknown execution 'fast'"):
        _run(tmp_path, execution="fast")
