"""NativeBacktest initial-holdings seeding (Plan 2a).

Parity is NOT the oracle here — the Python engine has no seeding. These
tests pin exact absolute numbers on a controlled 1-asset warehouse and
exercise the full Python->C++ marshaling (asset-id map, date->day, arrays).
"""

from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA
from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.costs import CostModel
from pkmn_quant.engine.native import NativeBacktest, NativeStrategySpec, SeededHolding
from pkmn_quant.engine.portfolio import Asset
from pkmn_quant.engine.strategy import Context, Strategy

START = date(2025, 1, 1)
PRODUCTS = pl.DataFrame(
    {
        "product_id": [1],
        "group_id": [1],
        "name": ["Box A"],
        "rarity": [None],
        "kind": ["sealed"],
        "released_on": [date(2024, 11, 1)],
    }
)


class NoTrade(Strategy):
    name = "no-trade"

    def on_bar(self, ctx: Context) -> list:
        return []


def _seed_one_asset(root: Path) -> None:
    """One sealed product, three days, market 10/12/15 (marks track market)."""
    w = Warehouse(Paths(root=root))
    for i, price in enumerate((10.0, 12.0, 15.0)):
        day = START + timedelta(days=i)
        w.write_prices(
            day,
            pl.DataFrame(
                [
                    {
                        "date": day,
                        "product_id": 1,
                        "sub_type": "Normal",
                        "low": round(price * 0.9, 2),
                        "mid": round(price * 1.15, 2),
                        "high": round(price * 3.0, 2),
                        "market": price,
                    }
                ],
                schema=PRICE_SCHEMA,
            ),
        )
    w.write_products(PRODUCTS)


def _run(root: Path, holdings: list[SeededHolding]) -> list[float]:
    wh = Warehouse(Paths(root=root))
    res = NativeBacktest(
        warehouse=wh,
        strategy=NoTrade(),
        cost_model=CostModel(),
        start=START,
        end=START + timedelta(days=2),
        initial_cash=50.0,
        initial_holdings=holdings,
    ).run()
    return res.equity_curve["equity"].to_list()


def test_seeded_holdings_value_through_the_curve(tmp_path: Path) -> None:
    _seed_one_asset(tmp_path)
    asset = Asset(product_id=1, sub_type="Normal")
    equity = _run(
        tmp_path,
        [SeededHolding(asset=asset, quantity=4, avg_cost=9.0, opened_on=date(2024, 12, 1))],
    )
    # No trading; equity = 50 + 4*mark. 50+40, 50+48, 50+60.
    assert equity == [90.0, 98.0, 110.0]


def test_no_holdings_is_flat_cash(tmp_path: Path) -> None:
    _seed_one_asset(tmp_path)
    assert _run(tmp_path, []) == [50.0, 50.0, 50.0]


def test_holding_outside_universe_raises_value_error(tmp_path: Path) -> None:
    _seed_one_asset(tmp_path)
    ghost = Asset(product_id=999, sub_type="Normal")
    with pytest.raises(ValueError, match="universe"):
        _run(tmp_path, [SeededHolding(asset=ghost, quantity=1, avg_cost=5.0, opened_on=START)])


def test_native_strategy_path_seeds_day_zero_equity(tmp_path: Path) -> None:
    """Seeding also works on the native (non-bridge) strategy path.

    NoTrade above runs via the Python callback bridge (GIL held). Here the
    strategy is a NativeStrategySpec, so the C++ factory builds and runs a
    real native buy-and-hold strategy with no Python callback in the loop —
    seeding must still be visible. On day 0 no T+1 fill has happened yet, so
    equity[0] is a clean seed-isolating assertion: initial_cash + qty*market.
    """
    _seed_one_asset(tmp_path)
    wh = Warehouse(Paths(root=tmp_path))
    asset = Asset(product_id=1, sub_type="Normal")
    res = NativeBacktest(
        warehouse=wh,
        strategy=NativeStrategySpec("buy-and-hold", {}, kind="sealed"),
        cost_model=CostModel(),
        start=START,
        end=START + timedelta(days=2),
        initial_cash=50.0,
        initial_holdings=[
            SeededHolding(asset=asset, quantity=4, avg_cost=9.0, opened_on=date(2024, 12, 1))
        ],
    ).run()
    # day 0: 50 cash + 4 * market(10.0) = 90.0; no fill has landed yet.
    assert res.equity_curve["equity"].to_list()[0] == 90.0


def test_duplicate_seed_asset_raises_value_error_from_cpp(tmp_path: Path) -> None:
    """A duplicate in-universe asset passes the Python universe check but
    must be rejected by C++ Portfolio::seed's duplicate detection, proving
    that a std::invalid_argument raised inside the C++ core crosses the
    nanobind boundary as a Python ValueError (not just a Python-side
    ValueError raised before the C++ call, as in the universe-check test
    above: the Python adapter only validates universe membership, not
    duplicates or quantity)."""
    _seed_one_asset(tmp_path)
    asset = Asset(product_id=1, sub_type="Normal")
    with pytest.raises(ValueError):
        _run(
            tmp_path,
            [
                SeededHolding(asset=asset, quantity=1, avg_cost=5.0, opened_on=START),
                SeededHolding(asset=asset, quantity=2, avg_cost=6.0, opened_on=START),
            ],
        )
