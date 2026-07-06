from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA
from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.live.signals import SignalsError, generate_signals
from pkmn_quant.research.artifacts import write_walkforward_json
from pkmn_quant.research.folds import Fold
from pkmn_quant.research.walkforward import FoldResult, WalkForwardResult
from tests.helpers import price_row

START = date(2025, 1, 1)
LATEST = START + timedelta(days=120)


@pytest.fixture
def warehouse(tmp_path: Path) -> Warehouse:
    """A sealed product that peaked at 200 then fell to 100 (50% drawdown),
    aged 121 days at LATEST: qualifies for sealed-accumulation entry."""
    w = Warehouse(Paths(root=tmp_path))
    for i in range(121):
        d = START + timedelta(days=i)
        price = 200.0 if i < 30 else 100.0
        w.write_prices(d, pl.DataFrame([price_row(d, 1, price)], schema=PRICE_SCHEMA))
    w.write_products(
        pl.DataFrame(
            {
                "product_id": [1],
                "group_id": [1],
                "name": ["Crashed Box"],
                "rarity": [None],
                "kind": ["sealed"],
                "released_on": [START],
            }
        )
    )
    return w


PARAMS: dict[str, float | int] = {"min_drawdown": 0.25, "take_profit": 1.5, "min_age_days": 60}


def seed_wf_artifact(results_dir: Path, params: dict[str, float | int] = PARAMS) -> None:
    run_dir = results_dir / "wf-sealed-accumulation-2025-01-01-2025-04-01"
    run_dir.mkdir(parents=True)
    fold = Fold(date(2025, 1, 1), date(2025, 2, 1), date(2025, 2, 2), date(2025, 3, 1))
    fr = FoldResult(
        fold=fold,
        params=params,
        is_summary={"total_return": 0.05},
        oos_summary={"total_return": 0.01},
        oos_curve=pl.DataFrame({"date": [date(2025, 2, 2)], "equity": [1000.0]}),
    )
    result = WalkForwardResult(
        folds=[fr],
        stitched_curve=pl.DataFrame({"date": [date(2025, 2, 2)], "equity": [1000.0]}),
        summary={"stitched_total_return": 0.01, "overfitting_gap": 0.04},
    )
    write_walkforward_json(run_dir, result, strategy_name="sealed-accumulation")


def test_generates_buy_recommendation(warehouse: Warehouse, tmp_path: Path) -> None:
    results_dir = tmp_path / "data" / "results"
    seed_wf_artifact(results_dir)
    report = generate_signals(
        warehouse=warehouse,
        strategy_name="sealed-accumulation",
        cash=1000.0,
        results_dir=results_dir,
    )
    assert report.as_of == LATEST
    assert report.strategy == "sealed-accumulation"
    assert report.params == {"min_drawdown": 0.25, "take_profit": 1.5, "min_age_days": 60}
    assert report.wf_summary["overfitting_gap"] == 0.04
    [rec] = report.recommendations
    assert rec.action == "BUY"
    assert rec.product_id == 1
    assert rec.name == "Crashed Box"
    assert rec.market_price == 100.0
    assert rec.quantity == 1  # floor(1000 * 0.10 budget_frac / 100)
    assert rec.notional == 100.0


def test_no_artifact_raises_clean_error(warehouse: Warehouse, tmp_path: Path) -> None:
    with pytest.raises(SignalsError, match="pkmn walkforward"):
        generate_signals(
            warehouse=warehouse,
            strategy_name="sealed-accumulation",
            cash=1000.0,
            results_dir=tmp_path / "data" / "results",
        )


def test_unknown_strategy_raises(warehouse: Warehouse, tmp_path: Path) -> None:
    with pytest.raises(SignalsError, match="unknown strategy"):
        generate_signals(
            warehouse=warehouse,
            strategy_name="nope",
            cash=1000.0,
            results_dir=tmp_path / "data" / "results",
        )


def test_corrupt_artifact_raises_clean_error(warehouse: Warehouse, tmp_path: Path) -> None:
    results_dir = tmp_path / "data" / "results"
    run_dir = results_dir / "wf-sealed-accumulation-2025-01-01-2025-04-01"
    run_dir.mkdir(parents=True)
    (run_dir / "walkforward.json").write_text("{not json")
    with pytest.raises(SignalsError, match="corrupt"):
        generate_signals(
            warehouse=warehouse,
            strategy_name="sealed-accumulation",
            cash=1000.0,
            results_dir=results_dir,
        )


def test_incompatible_params_raises_clean_error(warehouse: Warehouse, tmp_path: Path) -> None:
    """A parseable artifact whose params no longer fit the strategy factory
    (e.g. a search-space key was renamed) must not leak a raw KeyError."""
    results_dir = tmp_path / "data" / "results"
    seed_wf_artifact(results_dir, params={"min_drawdown": 0.25})  # missing keys
    with pytest.raises(SignalsError, match="incompatible"):
        generate_signals(
            warehouse=warehouse,
            strategy_name="sealed-accumulation",
            cash=1000.0,
            results_dir=results_dir,
        )


def test_portfolio_mode_emits_sell_at_take_profit(warehouse: Warehouse, tmp_path: Path) -> None:
    """Bought at 60, mark is 100, take_profit 1.5 -> 100 >= 90 fires the exit."""
    from pkmn_quant.engine.portfolio import Asset as EAsset
    from pkmn_quant.engine.portfolio import Portfolio, Position

    results_dir = tmp_path / "data" / "results"
    seed_wf_artifact(results_dir)
    pf = Portfolio(cash=500.0)
    pf.positions[EAsset(1, "Normal")] = Position(quantity=2, avg_cost=60.0)
    report = generate_signals(
        warehouse=warehouse,
        strategy_name="sealed-accumulation",
        results_dir=results_dir,
        portfolio=pf,
    )
    sells = [r for r in report.recommendations if r.action == "SELL"]
    [sell] = sells
    assert sell.product_id == 1 and sell.quantity == 2
    assert sell.avg_cost == 60.0
    assert sell.gain_pct == pytest.approx(100.0 / 60.0 - 1.0)
    assert report.portfolio_snapshot is not None
    assert report.portfolio_snapshot.cash == 500.0
    assert report.portfolio_snapshot.equity == pytest.approx(500.0 + 200.0)
    # BUY recommendations (if any) must not carry avg_cost (that belongs to SELLs only)
    buys = [r for r in report.recommendations if r.action == "BUY"]
    assert all(r.avg_cost is None for r in buys)


def test_portfolio_mode_no_sell_below_take_profit(warehouse: Warehouse, tmp_path: Path) -> None:
    """avg_cost=90, mark=100, take_profit=1.5 -> target is 135; no exit fired."""
    from pkmn_quant.engine.portfolio import Asset as EAsset
    from pkmn_quant.engine.portfolio import Portfolio, Position

    results_dir = tmp_path / "data" / "results"
    seed_wf_artifact(results_dir)
    pf = Portfolio(cash=300.0)
    pf.positions[EAsset(1, "Normal")] = Position(quantity=1, avg_cost=90.0)
    report = generate_signals(
        warehouse=warehouse,
        strategy_name="sealed-accumulation",
        results_dir=results_dir,
        portfolio=pf,
    )
    sells = [r for r in report.recommendations if r.action == "SELL"]
    assert sells == []
    assert report.portfolio_snapshot is not None
    # equity = cash + mark * qty = 300 + 100 * 1
    assert report.portfolio_snapshot.equity == pytest.approx(300.0 + 100.0)


def test_portfolio_mode_no_mark_raises_signals_error(warehouse: Warehouse, tmp_path: Path) -> None:
    """A held asset with sub_type not in the warehouse has no mark; generate_signals
    must raise SignalsError (not LedgerError) with a message mentioning warmup."""
    from pkmn_quant.engine.portfolio import Asset as EAsset
    from pkmn_quant.engine.portfolio import Portfolio, Position

    results_dir = tmp_path / "data" / "results"
    seed_wf_artifact(results_dir)
    pf = Portfolio(cash=500.0)
    # Asset(1, "Weird") has no price rows in the warehouse → mark is missing
    pf.positions[EAsset(1, "Weird")] = Position(quantity=1, avg_cost=80.0)
    with pytest.raises(SignalsError, match="warmup"):
        generate_signals(
            warehouse=warehouse,
            strategy_name="sealed-accumulation",
            results_dir=results_dir,
            portfolio=pf,
        )


def test_portfolio_mode_rejects_entry_state_strategies(
    warehouse: Warehouse, tmp_path: Path
) -> None:
    from pkmn_quant.engine.portfolio import Portfolio

    with pytest.raises(SignalsError, match="dip-buyer"):
        generate_signals(
            warehouse=warehouse,
            strategy_name="dip-buyer",
            results_dir=tmp_path / "data" / "results",
            portfolio=Portfolio(cash=100.0),
        )


def test_cash_and_portfolio_are_mutually_exclusive(warehouse: Warehouse, tmp_path: Path) -> None:
    from pkmn_quant.engine.portfolio import Portfolio

    with pytest.raises(SignalsError, match="either"):
        generate_signals(
            warehouse=warehouse,
            strategy_name="sealed-accumulation",
            results_dir=tmp_path / "data" / "results",
            cash=1000.0,
            portfolio=Portfolio(cash=100.0),
        )
