"""NativeBacktest book execution (Plan B Task 2).

Fills against a ladder of modelled resting orders (synthetic, built from the
day's tcgcsv quote), priced in whole cents. Pins the cent golden on a
controlled 1-asset warehouse and checks the formula default is untouched.
"""

import math
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


def _llround(x: float) -> int:
    """std::llround for non-negative x: nearest, halves away from zero."""
    return math.floor(x + 0.5)


def test_book_sell_impact_beyond_depth_extends_ramp() -> None:
    """A hypothetical 5-unit exit on a $60 product with bid depth 3 (tier
    < $200): units 0..2 match the modelled bids (synthetic, built from the
    day's tcgcsv quote), units 3..4 are priced on the same ramp extended past
    the last level. Unit k bids 60 - 10*(k+0.5)/3, rounded to whole cents."""
    from pkmn_quant.engine.native import book_sell_impact

    cents = sum(_llround((60.0 - 10.0 * (k + 0.5) / 3.0) * 100.0) for k in range(5))
    assert cents == 25833  # 5833 + 5500 + 5167 + 4833 + 4500
    hand = 5 * 60.0 - cents / 100.0
    costs = CostModel(impact_enabled=True)
    got = book_sell_impact(costs, 60.0, 50.0, 5)
    assert got == pytest.approx(hand, abs=1e-9)
    assert got == pytest.approx(costs.sell_impact(60.0, 50.0, 5), abs=0.03)


def test_book_sell_impact_no_low_is_zero() -> None:
    from pkmn_quant.engine.native import book_sell_impact

    assert book_sell_impact(CostModel(impact_enabled=True), 60.0, None, 5) == 0.0


def test_bindings_reject_mismatched_tier_arrays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """tier_thresholds and tier_qtys must have equal length at both binding
    sites; a mismatch is a clean ValueError, never an out-of-bounds read."""
    import numpy as np

    from pkmn_quant import _engine
    from pkmn_quant.engine import native

    with pytest.raises(ValueError, match="tier_thresholds and tier_qtys differ"):
        _engine.book_sell_impact(
            market=60.0,
            low=50.0,
            qty=5,
            tier_thresholds=np.array([5.0, 50.0, 200.0]),
            tier_qtys=np.array([20, 8], dtype=np.int64),
            fallback_max_qty=1,
        )

    real = _engine.run_backtest

    def truncated(**kw: object) -> object:
        kw["tier_qtys"] = kw["tier_qtys"][:-1]  # type: ignore[index]
        return real(**kw)  # type: ignore[arg-type]

    monkeypatch.setattr(native._engine, "run_backtest", truncated)
    with pytest.raises(ValueError, match="tier_thresholds and tier_qtys differ"):
        _run(tmp_path)


def test_ladder_equivalence_counts_and_agrees() -> None:
    """The Task 7 gate's binding on hand-built rows: a $1 card (depth 20 each
    side, 210 (used, qty) pairs per side), an inverted $60 box whose $65 floor
    lands in the 3-unit tier on the buy side and $60 in it on the sell side,
    a NaN market (skipped) and a $300 fallback-tier row with no mid (1 pair a
    side). Exact ladder vs formula agree far inside 1e-6."""
    import numpy as np

    from pkmn_quant import _engine

    out = _engine.ladder_equivalence(
        row_market=np.array([1.0, 60.0, np.nan, 300.0]),
        row_mid=np.array([1.5, 70.0, 1.0, np.nan]),
        row_low=np.array([0.8, 65.0, 1.0, 250.0]),
        tier_thresholds=np.array([5.0, 50.0, 200.0]),
        tier_qtys=np.array([20, 8, 3], dtype=np.int64),
        fallback_max_qty=1,
    )
    assert out["rows"] == 3
    assert out["checks"] == 210 * 2 + 6 * 2 + 1 * 2
    assert isinstance(out["max_abs_err"], float)
    assert out["max_abs_err"] <= 1e-6
    assert out["worst_side"] in ("buy", "sell")
