"""ml-ranker-v2: determinism, degeneracy, engine parity, wiring."""

from datetime import timedelta

from pkmn_quant.config import Paths
from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.backtest import Backtest
from pkmn_quant.engine.costs import CostModel
from pkmn_quant.strategies.ml_ranker_v2 import MLRankerV2
from tests.test_native_parity import START, assert_results_equal, seed_rich


def _strategy() -> MLRankerV2:
    # min_train_rows lowered so the small fixture actually trains a model
    return MLRankerV2(
        horizon_days=7,
        rebalance_days=7,
        top_n=3,
        train_days=30,
        min_price=1.0,
        min_train_rows=20,
    )


def _run(tmp_path, strategy):
    return Backtest(
        warehouse=Warehouse(Paths(root=tmp_path)),
        strategy=strategy,
        cost_model=CostModel(impact_enabled=True),
        start=START,
        end=START + timedelta(days=39),
        initial_cash=1000.0,
    ).run()


def test_deterministic_and_trades(tmp_path) -> None:
    seed_rich(tmp_path)
    a = _run(tmp_path, _strategy())
    b = _run(tmp_path, _strategy())
    assert len(a.fills) > 0  # non-vacuous: the model trains and trades
    assert_results_equal(a, b)


def test_holds_when_history_too_thin(tmp_path) -> None:
    seed_rich(tmp_path, n_days=5)
    result = _run(tmp_path, MLRankerV2())  # default min_train_rows=200
    assert len(result.fills) == 0


def test_bridge_parity_bit_for_bit(tmp_path) -> None:
    """v2 through the C++ callback bridge == pure Python engine, exactly."""
    from pkmn_quant.engine.native import NativeBacktest

    seed_rich(tmp_path)
    py = _run(tmp_path, _strategy())
    native = NativeBacktest(
        warehouse=Warehouse(Paths(root=tmp_path)),
        strategy=_strategy(),
        cost_model=CostModel(impact_enabled=True),
        start=START,
        end=START + timedelta(days=39),
        initial_cash=1000.0,
    ).run()
    assert_results_equal(py, native)


def test_registry_and_portfolio_safe_wiring() -> None:
    from pkmn_quant.research.registry import REGISTRY

    entry = REGISTRY["ml-ranker-v2"]
    import optuna

    study = optuna.create_study(sampler=optuna.samplers.TPESampler(seed=1))
    params = entry.space(study.ask())
    strategy = entry.factory(params)
    assert strategy.name == "ml-ranker-v2"
    # max_iter / learning_rate are owned by in-loop purged selection:
    assert "max_iter" not in params and "learning_rate" not in params
    # portfolio-safe registration (grep-verified constant):
    from pkmn_quant.live.signals import PORTFOLIO_SAFE_STRATEGIES

    assert "ml-ranker-v2" in PORTFOLIO_SAFE_STRATEGIES
