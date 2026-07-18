"""Differential tests: NativeBacktest (C++) vs Backtest (Python reference).

Every assertion is EXACT (==) — bit-for-bit parity is the acceptance bar
(spec 2026-07-14). A tolerance here would hide real divergence.
"""

from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA
from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.backtest import Backtest, Result
from pkmn_quant.engine.costs import CostModel
from pkmn_quant.engine.native import NativeBacktest, NativeStrategySpec
from pkmn_quant.strategies.buy_and_hold import BuyAndHold

START = date(2025, 1, 1)

# (product_id, sub_type) -> base price. product 4 has two sub_types (an
# insertion-order tie on product_id); product 6 sits below min_price.
BASES: dict[tuple[int, str], float] = {
    (1, "Normal"): 80.0,
    (2, "Normal"): 40.0,
    (3, "Normal"): 25.0,
    (4, "Normal"): 12.0,
    (4, "Foil"): 18.0,
    (5, "Normal"): 6.0,
    (6, "Normal"): 1.5,
}

PRODUCTS = pl.DataFrame(
    {
        "product_id": [1, 2, 3, 4, 5, 6],
        "group_id": [1, 1, 1, 1, 1, 1],
        "name": ["Box A", "Box B", "Card C", "Card D", "Card E", "Penny F"],
        "rarity": [None, None, "Rare", "Rare", "Holo", "Common"],
        "kind": ["sealed", "sealed", "single", "single", "single", "single"],
        "released_on": [
            date(2024, 11, 1),
            date(2024, 6, 1),
            date(2024, 11, 1),
            date(2024, 11, 1),
            date(2024, 11, 1),
            date(2024, 11, 1),
        ],
    }
)


def _path(i: int) -> float:
    """Deterministic ramp-crash-recover cycle: guarantees dips, drawdowns,
    momentum reversals, and take-profit recoveries within 25 days."""
    i = i % 25
    if i < 10:
        return 1.0 + 0.05 * i  # ramp to 1.45
    if i < 15:
        return 0.8 - 0.05 * (i - 10)  # crash to 0.60
    return 0.62 + 0.03 * (i - 15)  # recovery


def seed_rich(root: Path, n_days: int = 40) -> None:
    w = Warehouse(Paths(root=root))
    for i in range(n_days):
        day = START + timedelta(days=i)
        if i % 9 == 4:  # market-wide gap day
            continue
        rows = []
        for (pid, st), base in BASES.items():
            if (pid * 3 + i) % 11 == 0:  # per-asset missing prints
                continue
            market = round(base * _path(i + pid), 2)
            rows.append(
                {
                    "date": day,
                    "product_id": pid,
                    "sub_type": st,
                    "low": round(market * 0.9, 2),
                    "mid": round(market * 1.15, 2),
                    "high": round(market * 3.0, 2),
                    "market": market,
                }
            )
        w.write_prices(day, pl.DataFrame(rows, schema=PRICE_SCHEMA))
    w.write_products(PRODUCTS)


# A priced asset with NO row in the products catalog (Plan 10 finding: the
# real warehouse has exactly this for 1,845 of 6,493 priced product_ids --
# upstream tcgcsv catalog drift, not stale local data). NativeBacktest.run()
# used to do an unconditional prod_info[product_id] lookup and crash with
# KeyError the instant it saw one; it now tags such assets kind "other" (-1),
# matching the Python engine's implicit behavior (an uncataloged asset never
# joins into any products-filtered universe, so it's simply invisible there,
# but cost-aware-reversion has no kind filter at all and still trades it).
UNCATALOGED_PID = 99
UNCATALOGED_BASE = 50.0


def seed_rich_with_uncataloged(root: Path, n_days: int = 40) -> None:
    """seed_rich() plus one extra priced-but-uncataloged asset.

    Same deep-dip/recover price shape as the cataloged assets (via _path),
    so it exercises cost-aware-reversion's entry logic the same way; never
    written to products.parquet.
    """
    seed_rich(root, n_days)
    w = Warehouse(Paths(root=root))
    for i in range(n_days):
        day = START + timedelta(days=i)
        if i % 9 == 4:  # matches seed_rich's market-wide gap day
            continue
        existing = w.load_day(day)
        market = round(UNCATALOGED_BASE * _path(i + UNCATALOGED_PID), 2)
        extra = pl.DataFrame(
            [
                {
                    "date": day,
                    "product_id": UNCATALOGED_PID,
                    "sub_type": "Normal",
                    "low": round(market * 0.9, 2),
                    "mid": round(market * 1.15, 2),
                    "high": round(market * 3.0, 2),
                    "market": market,
                }
            ],
            schema=PRICE_SCHEMA,
        )
        w.write_prices(day, pl.concat([existing, extra]))
    # PRODUCTS deliberately excludes UNCATALOGED_PID; already written by
    # seed_rich(), not rewritten here.


def assert_results_equal(py: Result, cpp: Result) -> None:
    assert py.equity_curve["date"].to_list() == cpp.equity_curve["date"].to_list()
    assert py.equity_curve["equity"].to_list() == cpp.equity_curve["equity"].to_list()
    assert len(py.fills) == len(cpp.fills)
    for a, b in zip(py.fills, cpp.fills, strict=True):
        assert (a.day, a.asset, a.quantity) == (b.day, b.asset, b.quantity)
        assert a.price == b.price
        assert a.fees == b.fees
        assert a.impact == b.impact
    assert py.summary == cpp.summary
    assert py.strategy_name == cpp.strategy_name


@pytest.mark.parametrize("impact", [False, True])
def test_buy_and_hold_parity(tmp_path: Path, impact: bool) -> None:
    seed_rich(tmp_path)
    wh = Warehouse(Paths(root=tmp_path))
    cm = CostModel(impact_enabled=impact)
    end = START + timedelta(days=39)
    py = Backtest(
        warehouse=wh,
        strategy=BuyAndHold(kind="sealed"),
        cost_model=cm,
        start=START,
        end=end,
        initial_cash=1000.0,
    ).run()
    cpp = NativeBacktest(
        warehouse=wh,
        strategy=NativeStrategySpec("buy-and-hold", {}, kind="sealed"),
        cost_model=cm,
        start=START,
        end=end,
        initial_cash=1000.0,
    ).run()
    assert len(py.fills) > 0  # the test must not pass vacuously
    assert_results_equal(py, cpp)


def test_buy_and_hold_parity_single_universe(tmp_path: Path) -> None:
    """Exercises the marks insertion-order tie: product 4 Normal vs Foil."""
    seed_rich(tmp_path)
    wh = Warehouse(Paths(root=tmp_path))
    cm = CostModel()
    end = START + timedelta(days=39)
    py = Backtest(
        warehouse=wh,
        strategy=BuyAndHold(kind="single"),
        cost_model=cm,
        start=START,
        end=end,
        initial_cash=1000.0,
    ).run()
    cpp = NativeBacktest(
        warehouse=wh,
        strategy=NativeStrategySpec("buy-and-hold", {}, kind="single"),
        cost_model=cm,
        start=START,
        end=end,
        initial_cash=1000.0,
    ).run()
    assert len(py.fills) > 0
    assert_results_equal(py, cpp)


def test_warmup_days_parity(tmp_path: Path) -> None:
    seed_rich(tmp_path)
    wh = Warehouse(Paths(root=tmp_path))
    cm = CostModel(impact_enabled=True)
    start = START + timedelta(days=15)
    end = START + timedelta(days=39)
    py = Backtest(
        warehouse=wh,
        strategy=BuyAndHold(kind="sealed"),
        cost_model=cm,
        start=start,
        end=end,
        initial_cash=1000.0,
        warmup_days=10,
    ).run()
    cpp = NativeBacktest(
        warehouse=wh,
        strategy=NativeStrategySpec("buy-and-hold", {}, kind="sealed"),
        cost_model=cm,
        start=start,
        end=end,
        initial_cash=1000.0,
        warmup_days=10,
    ).run()
    assert_results_equal(py, cpp)


def test_unknown_strategy_raises_value_error(tmp_path: Path) -> None:
    seed_rich(tmp_path, n_days=3)
    wh = Warehouse(Paths(root=tmp_path))
    with pytest.raises(ValueError):
        NativeBacktest(
            warehouse=wh,
            strategy=NativeStrategySpec("nope", {}),
            cost_model=CostModel(),
            start=START,
            end=START + timedelta(days=2),
            initial_cash=100.0,
        ).run()


@pytest.mark.parametrize("impact", [False, True])
def test_sealed_accumulation_parity(tmp_path: Path, impact: bool) -> None:
    from pkmn_quant.strategies.sealed_accumulation import SealedAccumulation

    seed_rich(tmp_path)
    wh = Warehouse(Paths(root=tmp_path))
    cm = CostModel(impact_enabled=impact)
    end = START + timedelta(days=39)
    params = {
        "min_age_days": 30,
        "max_age_days": 400,
        "min_drawdown": 0.15,
        "take_profit": 1.1,
        "max_positions": 5,
        "budget_frac": 0.4,
    }
    py = Backtest(
        warehouse=wh,
        strategy=SealedAccumulation(
            min_age_days=30,
            max_age_days=400,
            min_drawdown=0.15,
            take_profit=1.1,
            max_positions=5,
            budget_frac=0.4,
        ),
        cost_model=cm,
        start=START,
        end=end,
        initial_cash=1000.0,
    ).run()
    cpp = NativeBacktest(
        warehouse=wh,
        strategy=NativeStrategySpec(
            "sealed-accumulation", {k: float(v) for k, v in params.items()}
        ),
        cost_model=cm,
        start=START,
        end=end,
        initial_cash=1000.0,
    ).run()
    assert len(py.fills) > 0
    assert_results_equal(py, cpp)


@pytest.mark.parametrize("impact", [False, True])
def test_dip_buyer_parity(tmp_path: Path, impact: bool) -> None:
    from pkmn_quant.strategies.dip_buyer import DipBuyer

    seed_rich(tmp_path)
    wh = Warehouse(Paths(root=tmp_path))
    cm = CostModel(impact_enabled=impact)
    end = START + timedelta(days=39)
    py = Backtest(
        warehouse=wh,
        strategy=DipBuyer(
            dip_window_days=5,
            dip_threshold=0.10,
            hold_days=7,
            take_profit=1.05,
            max_positions=5,
            budget_frac=0.4,
            min_price=3.0,
        ),
        cost_model=cm,
        start=START,
        end=end,
        initial_cash=1000.0,
    ).run()
    cpp = NativeBacktest(
        warehouse=wh,
        strategy=NativeStrategySpec(
            "dip-buyer",
            {
                "dip_window_days": 5.0,
                "dip_threshold": 0.10,
                "hold_days": 7.0,
                "take_profit": 1.05,
                "max_positions": 5.0,
                "budget_frac": 0.4,
                "min_price": 3.0,
            },
        ),
        cost_model=cm,
        start=START,
        end=end,
        initial_cash=1000.0,
    ).run()
    assert len(py.fills) > 2  # entries AND exits must occur
    assert any(f.quantity < 0 for f in py.fills)
    assert_results_equal(py, cpp)


@pytest.mark.parametrize("impact", [False, True])
def test_momentum_parity(tmp_path: Path, impact: bool) -> None:
    from pkmn_quant.strategies.momentum import CrossSectionalMomentum

    seed_rich(tmp_path)
    wh = Warehouse(Paths(root=tmp_path))
    cm = CostModel(impact_enabled=impact)
    end = START + timedelta(days=39)
    py = Backtest(
        warehouse=wh,
        strategy=CrossSectionalMomentum(lookback_days=10, top_n=3, rebalance_days=5, min_price=3.0),
        cost_model=cm,
        start=START,
        end=end,
        initial_cash=1000.0,
    ).run()
    cpp = NativeBacktest(
        warehouse=wh,
        strategy=NativeStrategySpec(
            "xs-momentum",
            {"lookback_days": 10.0, "top_n": 3.0, "rebalance_days": 5.0, "min_price": 3.0},
        ),
        cost_model=cm,
        start=START,
        end=end,
        initial_cash=1000.0,
    ).run()
    assert len(py.fills) > 2
    assert_results_equal(py, cpp)


@pytest.mark.parametrize("impact", [False, True])
def test_cost_aware_reversion_parity(tmp_path: Path, impact: bool) -> None:
    from pkmn_quant.strategies.cost_aware_reversion import CostAwareReversion

    seed_rich(tmp_path)
    wh = Warehouse(Paths(root=tmp_path))
    cm = CostModel(impact_enabled=impact)
    end = START + timedelta(days=39)
    py = Backtest(
        warehouse=wh,
        strategy=CostAwareReversion(
            dip_window_days=10,
            dip_threshold=0.15,
            min_edge=0.02,
            take_profit=1.05,
            max_hold_days=20,
            max_positions=5,
            budget_frac=0.4,
            min_price=3.0,
        ),
        cost_model=cm,
        start=START,
        end=end,
        initial_cash=1000.0,
    ).run()
    cpp = NativeBacktest(
        warehouse=wh,
        strategy=NativeStrategySpec(
            "cost-aware-reversion",
            {
                "dip_window_days": 10.0,
                "dip_threshold": 0.15,
                "min_edge": 0.02,
                "take_profit": 1.05,
                "max_hold_days": 20.0,
                "max_positions": 5.0,
                "budget_frac": 0.4,
                "min_price": 3.0,
            },
        ),
        cost_model=cm,
        start=START,
        end=end,
        initial_cash=1000.0,
    ).run()
    assert len(py.fills) > 0
    assert_results_equal(py, cpp)


def test_bridge_runs_python_strategy_bit_for_bit(tmp_path: Path) -> None:
    """The bridge path: a Python Strategy instance on the C++ engine.

    Passing DipBuyer (unmodified) as ``strategy=`` to NativeBacktest exercises
    the per-bar callback bridge (Task 6): C++ sends (day, positions, cash)
    each bar, the Python wrapper rebuilds Context from its own MarketData,
    and the untouched Python strategy runs.
    """
    from pkmn_quant.strategies.dip_buyer import DipBuyer

    seed_rich(tmp_path)
    wh = Warehouse(Paths(root=tmp_path))
    cm = CostModel(impact_enabled=True)
    end = START + timedelta(days=39)

    def make() -> DipBuyer:
        return DipBuyer(
            dip_window_days=5,
            dip_threshold=0.10,
            hold_days=7,
            take_profit=1.05,
            max_positions=5,
            budget_frac=0.4,
            min_price=3.0,
        )

    py = Backtest(
        warehouse=wh,
        strategy=make(),
        cost_model=cm,
        start=START,
        end=end,
        initial_cash=1000.0,
    ).run()
    cpp = NativeBacktest(
        warehouse=wh,
        strategy=make(),
        cost_model=cm,
        start=START,
        end=end,
        initial_cash=1000.0,
    ).run()
    assert len(py.fills) > 0
    assert_results_equal(py, cpp)


def test_walkforward_cpp_matches_python(tmp_path: Path) -> None:
    """Whole-walkforward differential: fixed params (trivial optimizer), both engines."""
    from pkmn_quant.research.walkforward import run_walkforward
    from pkmn_quant.strategies.dip_buyer import DipBuyer

    seed_rich(tmp_path, n_days=60)
    wh = Warehouse(Paths(root=tmp_path))
    cm = CostModel(impact_enabled=True)
    fixed: dict[str, float | int] = {
        "dip_window_days": 5,
        "dip_threshold": 0.10,
        "hold_days": 7,
        "take_profit": 1.05,
    }

    def factory(p: dict[str, float | int]) -> DipBuyer:
        return DipBuyer(
            dip_window_days=int(p["dip_window_days"]),
            dip_threshold=float(p["dip_threshold"]),
            hold_days=int(p["hold_days"]),
            take_profit=float(p["take_profit"]),
        )

    def optimizer(fold: object, evaluate: object) -> dict[str, float | int]:
        return dict(fixed)

    py = run_walkforward(
        warehouse=wh,
        strategy_factory=factory,
        optimizer=optimizer,
        cost_model=cm,
        start=START,
        end=START + timedelta(days=59),
        is_days=20,
        oos_days=10,
        initial_cash=1000.0,
        warmup_days=10,
    )
    cpp = run_walkforward(
        warehouse=wh,
        strategy_factory=factory,
        optimizer=optimizer,
        cost_model=cm,
        start=START,
        end=START + timedelta(days=59),
        is_days=20,
        oos_days=10,
        initial_cash=1000.0,
        warmup_days=10,
        engine="cpp",
        strategy_name="dip-buyer",
    )
    assert py.stitched_curve["equity"].to_list() == cpp.stitched_curve["equity"].to_list()
    assert py.summary == cpp.summary


def test_bridge_ml_ranker_parity(tmp_path: Path) -> None:
    """ml-ranker (sklearn, random_state=0) runs unmodified via the bridge.

    Proves the bridge with a stateful, sklearn-backed strategy: MLRanker
    trains a HistGradientBoostingRegressor inside on_bar and must produce
    identical predictions (hence identical fills) on both engines. On this
    small synthetic fixture ml-ranker legitimately trades zero times (too
    few training rows clear min_train_rows before the model ever fires);
    that's acceptable here because the bridge mechanics are what this test
    proves, and test_bridge_runs_python_strategy_bit_for_bit (dip-buyer,
    above) already exercises a bridge run with real fills.
    """
    from pkmn_quant.strategies.ml_ranker import MLRanker

    seed_rich(tmp_path, n_days=60)
    wh = Warehouse(Paths(root=tmp_path))
    cm = CostModel(impact_enabled=True)
    end = START + timedelta(days=59)

    def make() -> MLRanker:
        return MLRanker(
            horizon_days=5,
            rebalance_days=7,
            top_n=2,
            train_days=30,
            max_iter=50,
            learning_rate=0.1,
            min_samples_leaf=5,
        )

    py = Backtest(
        warehouse=wh,
        strategy=make(),
        cost_model=cm,
        start=START + timedelta(days=20),
        end=end,
        initial_cash=1000.0,
        warmup_days=20,
    ).run()
    cpp = NativeBacktest(
        warehouse=wh,
        strategy=make(),
        cost_model=cm,
        start=START + timedelta(days=20),
        end=end,
        initial_cash=1000.0,
        warmup_days=20,
    ).run()
    assert_results_equal(py, cpp)


def test_buy_and_hold_parity_with_uncataloged_asset(tmp_path: Path) -> None:
    """Regression for the Plan 10 real-data finding: a priced asset absent
    from products.parquet must not crash the C++ bridge. BuyAndHold's kind
    filter excludes the uncataloged asset in Python (no catalog row -> it
    never joins into the sealed universe); this proves the C++ side
    reproduces "excluded, not crashed" bit-for-bit.
    """
    seed_rich_with_uncataloged(tmp_path)
    wh = Warehouse(Paths(root=tmp_path))
    cm = CostModel(impact_enabled=True)
    end = START + timedelta(days=39)
    py = Backtest(
        warehouse=wh,
        strategy=BuyAndHold(kind="sealed"),
        cost_model=cm,
        start=START,
        end=end,
        initial_cash=1000.0,
    ).run()
    cpp = NativeBacktest(
        warehouse=wh,
        strategy=NativeStrategySpec("buy-and-hold", {}, kind="sealed"),
        cost_model=cm,
        start=START,
        end=end,
        initial_cash=1000.0,
    ).run()
    assert len(py.fills) > 0  # the test must not pass vacuously
    assert_results_equal(py, cpp)
    assert not any(f.asset.product_id == UNCATALOGED_PID for f in py.fills)
    assert not any(f.asset.product_id == UNCATALOGED_PID for f in cpp.fills)


def test_cost_aware_reversion_parity_with_uncataloged_asset(tmp_path: Path) -> None:
    """cost-aware-reversion has no kind filter at all (cost_aware_reversion.py
    scans every asset in ctx.history, no `if kind ==` guard) -- unlike
    buy-and-hold above, the uncataloged asset IS a tradeable candidate here.
    Confirms native.py's kind "other" (-1) tagging keeps it IN the universe
    (not silently dropped, which would be a quieter but worse divergence
    than the KeyError it replaced) and that both engines fill it identically.
    """
    from pkmn_quant.strategies.cost_aware_reversion import CostAwareReversion

    seed_rich_with_uncataloged(tmp_path)
    wh = Warehouse(Paths(root=tmp_path))
    cm = CostModel(impact_enabled=True)
    end = START + timedelta(days=39)
    py = Backtest(
        warehouse=wh,
        strategy=CostAwareReversion(
            dip_window_days=10,
            dip_threshold=0.15,
            min_edge=0.02,
            take_profit=1.05,
            max_hold_days=20,
            max_positions=5,
            budget_frac=0.4,
            min_price=3.0,
        ),
        cost_model=cm,
        start=START,
        end=end,
        initial_cash=1000.0,
    ).run()
    cpp = NativeBacktest(
        warehouse=wh,
        strategy=NativeStrategySpec(
            "cost-aware-reversion",
            {
                "dip_window_days": 10.0,
                "dip_threshold": 0.15,
                "min_edge": 0.02,
                "take_profit": 1.05,
                "max_hold_days": 20.0,
                "max_positions": 5.0,
                "budget_frac": 0.4,
                "min_price": 3.0,
            },
        ),
        cost_model=cm,
        start=START,
        end=end,
        initial_cash=1000.0,
    ).run()
    assert len(py.fills) > 0
    assert_results_equal(py, cpp)
    assert any(f.asset.product_id == UNCATALOGED_PID for f in py.fills)
    assert any(f.asset.product_id == UNCATALOGED_PID for f in cpp.fills)


def test_unknown_param_raises_value_error(tmp_path: Path) -> None:
    """A renamed/added tunable the C++ factory branch doesn't consume must
    fail loudly, not silently run on defaults (final-review hardening)."""
    seed_rich(tmp_path, n_days=3)
    wh = Warehouse(Paths(root=tmp_path))
    with pytest.raises(ValueError, match="bogus_param"):
        NativeBacktest(
            warehouse=wh,
            strategy=NativeStrategySpec("dip-buyer", {"bogus_param": 1.0}),
            cost_model=CostModel(),
            start=START,
            end=START + timedelta(days=2),
            initial_cash=100.0,
        ).run()


def test_unknown_kind_raises_value_error(tmp_path: Path) -> None:
    """An unvalidated --kind must not silently divergence-match the C++
    uncataloged bucket (final-review hardening)."""
    seed_rich(tmp_path, n_days=3)
    wh = Warehouse(Paths(root=tmp_path))
    with pytest.raises(ValueError, match="kind"):
        NativeBacktest(
            warehouse=wh,
            strategy=NativeStrategySpec("buy-and-hold", {}, kind="bogus"),
            cost_model=CostModel(),
            start=START,
            end=START + timedelta(days=2),
            initial_cash=100.0,
        ).run()
