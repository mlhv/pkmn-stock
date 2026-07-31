"""The catalog is a SUPERSET of the priced universe.

Since the catalog-completeness plan, products.parquet covers all 217 tcgcsv
groups while prices cover only the tracked sets, so thousands of catalog rows
have no price data. Strategies build candidates from ctx.products; a candidate
with no mark must never become an order, and both engines must agree.

This file is a regression net, not a bug fix: at write time every assertion
here is expected to pass, because every strategy intersects its candidates
against ctx.history (price rows) before ordering. It is checked in anyway
because the shape (catalog as a strict superset, not the Plan-10 subset gap)
is new to this codebase and worth pinning permanently -- a future strategy
that built candidates straight from ctx.products without the price join
would fabricate a fill price, and this is the test that would catch it.

The fixture deliberately makes the decoys real trades, not idle bystanders:
prices for products 1 and 2 dip and recover hard enough that every
parametrized strategy fills at least once on its legitimate asset in this
same run. That is the difference between "the guard held" and "nothing ever
happened so the guard was never exercised" -- see the vacuous-pass note on
each test below.
"""

from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA
from pkmn_quant.data.warehouse import Warehouse

START = date(2025, 1, 1)
DAYS = 40

# Legitimate, priced product ids. Anything else appearing in a fill is a
# fabricated price on a product with no mark.
PRICED_IDS = {1, 2}


def _price_path(i: int) -> float:
    """Ramp, hard crash, partial recovery -- as a fraction of a base price.

    Peaks at i=4 (1.20x), bottoms at i=11 (0.36x, a 70% drawdown from peak),
    then climbs past 1x again well before the window ends at i=39. Steep
    enough that every strategy's entry threshold (sealed-accumulation's 25%
    drawdown, dip-buyer's 30% drop over its window, xs-momentum's ranked
    return) trips on a REAL priced asset, not just in theory.
    """
    if i < 5:
        return 1.0 + 0.05 * i
    if i < 12:
        return 1.20 - 0.12 * (i - 5)
    return 0.36 + 0.05 * (i - 12)


def _seed(root: Path) -> None:
    """Two priced products plus five catalog-only decoys that never print a
    price:

    - product 1 (kind "sealed", base 100): the sealed-accumulation asset.
    - product 2 (kind "single", base 50): the dip-buyer / xs-momentum asset.
    - 900, 901, 902 (kind "sealed"): released well before the window starts,
      so they are age-eligible for sealed-accumulation's default
      [min_age_days=60, max_age_days=365] window on every day of this run --
      genuinely reachable candidates, not excluded by the age/kind filter.
    - 903, 904 (kind "single"): sealed-accumulation's kind filter ("sealed")
      already excludes these, but dip-buyer and xs-momentum both build their
      candidate universe from
      ``ctx.products.filter(pl.col("kind") == "single")``, so these decoys
      DO enter that id set -- they are excluded only because the subsequent
      join against ctx.history (priced rows) finds nothing for them. That
      join is exactly the guard this file pins for those two strategies.

    None of the five decoys ever appears in `write_prices` -- that is the
    whole point.
    """
    wh = Warehouse(Paths(root=root))
    for i in range(DAYS):
        day = START + timedelta(days=i)
        shape = _price_path(i)
        p1 = round(100.0 * shape, 2)
        p2 = round(50.0 * shape, 2)
        rows = [
            {
                "date": day,
                "product_id": 1,
                "sub_type": "Normal",
                "low": round(p1 * 0.9, 2),
                "mid": round(p1 * 1.15, 2),
                "high": round(p1 * 3.0, 2),
                "market": p1,
            },
            {
                "date": day,
                "product_id": 2,
                "sub_type": "Normal",
                "low": round(p2 * 0.9, 2),
                "mid": round(p2 * 1.15, 2),
                "high": round(p2 * 3.0, 2),
                "market": p2,
            },
        ]
        wh.write_prices(day, pl.DataFrame(rows, schema=PRICE_SCHEMA))
    wh.write_products(
        pl.DataFrame(
            {
                "product_id": [1, 2, 900, 901, 902, 903, 904],
                "group_id": [1, 2, 2374, 2289, 1423, 2201, 2202],
                "name": [
                    "Alpha Booster Box",
                    "Beta Single Card",
                    "Ancient Mew",
                    "Blister Exclusive",
                    "Nintendo Promo",
                    "Uncataloged Rare Single",
                    "Uncataloged Holo Single",
                ],
                "rarity": [None, "Rare", "Promo", "Promo", "Promo", "Rare", "Holo"],
                "kind": ["sealed", "single", "sealed", "sealed", "sealed", "single", "single"],
                "released_on": [date(2024, 6, 1)] * 7,
            }
        )
    )


# xs-momentum's default lookback_days is 60, longer than this 40-day fixture
# -- window_start = today - 60 always precedes START, so ctx.history would
# have zero rows to rank REGARDLESS of any product's price or catalog
# status. Left at default, the test would pass for the wrong reason (no
# candidate is ever considered, decoy or legitimate) rather than the right
# one (the decoy specifically is excluded by the price join). Shrinking it
# to 14 -- within ParamSpec's [14, 120] range -- gives the strategy real
# history to rank inside the window, so the guard is actually exercised.
_STRATEGY_PARAMS: dict[str, dict[str, float | int]] = {
    "sealed-accumulation": {},
    "dip-buyer": {},
    "xs-momentum": {"lookback_days": 14},
}


@pytest.mark.parametrize("strategy_name", ["sealed-accumulation", "dip-buyer", "xs-momentum"])
def test_no_fill_on_a_catalog_only_product(tmp_path: Path, strategy_name: str) -> None:
    """A product in the catalog but absent from the price history must never
    be filled -- it has no mark, so any fill price would be fabricated.

    Not vacuous: each strategy fills at least once on its legitimate priced
    asset (1 or 2) in this same run (asserted below), so the absence of any
    decoy fill reflects the guard holding, not the strategy sitting idle.
    """
    from pkmn_quant.research.backtest_run import run_single_backtest

    _seed(tmp_path)
    out = run_single_backtest(
        root=tmp_path,
        strategy_name=strategy_name,
        params=_STRATEGY_PARAMS[strategy_name],
        cash=10_000.0,
        holdings=[],
        start=START,
        end=START + timedelta(days=DAYS - 1),
    )
    fills = pl.read_parquet(out.artifact_dir / "fills.parquet")
    traded = set(fills["product_id"].to_list())
    assert traded, f"{strategy_name} made no fills at all; this test proves nothing idle"
    assert traded <= PRICED_IDS, (
        f"{strategy_name} traded catalog-only products: {traded - PRICED_IDS}"
    )


def test_both_engines_agree_with_a_superset_catalog(tmp_path: Path) -> None:
    """Bit-for-bit parity must survive a catalog that is a strict superset of
    the priced universe. Plan 10's differential test (test_native_parity.py)
    covered the opposite gap (catalog rows MISSING for priced ids, fixed by
    tagging them kind "other"); this covers the new shape -- catalog rows
    with no price rows at all -- and confirms neither engine crashes or
    silently disagrees about which of the seven catalog rows are tradeable.
    """
    from pkmn_quant.engine.backtest import Backtest
    from pkmn_quant.engine.costs import CostModel
    from pkmn_quant.engine.native import NativeBacktest, NativeStrategySpec
    from pkmn_quant.strategies.sealed_accumulation import SealedAccumulation

    _seed(tmp_path)
    wh = Warehouse(Paths(root=tmp_path))
    cm = CostModel()
    end = START + timedelta(days=DAYS - 1)
    py = Backtest(
        warehouse=wh,
        strategy=SealedAccumulation(),
        cost_model=cm,
        start=START,
        end=end,
        initial_cash=10_000.0,
    ).run()
    native = NativeBacktest(
        warehouse=wh,
        strategy=NativeStrategySpec(name="sealed-accumulation", params={}),
        cost_model=cm,
        start=START,
        end=end,
        initial_cash=10_000.0,
    ).run()

    assert len(py.fills) > 0  # the test must not pass vacuously
    assert py.equity_curve["equity"].to_list() == native.equity_curve["equity"].to_list()
    assert len(py.fills) == len(native.fills)
    for a, b in zip(py.fills, native.fills, strict=True):
        assert (a.day, a.asset, a.quantity, a.price) == (b.day, b.asset, b.quantity, b.price)
    decoy_ids = {900, 901, 902, 903, 904}
    assert not any(f.asset.product_id in decoy_ids for f in py.fills)
    assert not any(f.asset.product_id in decoy_ids for f in native.fills)
