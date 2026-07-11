import optuna

from pkmn_quant.research.registry import REGISTRY


def test_registry_has_all_tunable_strategies() -> None:
    assert set(REGISTRY) == {
        "sealed-accumulation",
        "dip-buyer",
        "xs-momentum",
        "cost-aware-reversion",
        "ml-ranker",
    }


def test_factories_build_with_sampled_params() -> None:
    for name, entry in REGISTRY.items():
        study = optuna.create_study(sampler=optuna.samplers.RandomSampler(seed=1))
        trial = study.ask()
        params = entry.space(trial)
        strategy = entry.factory(params)
        assert strategy.name == name


def test_reversion_registered_and_buildable() -> None:
    entry = REGISTRY["cost-aware-reversion"]
    study = optuna.create_study(sampler=optuna.samplers.RandomSampler(seed=1))
    trial = study.ask()
    params = entry.space(trial)
    assert set(params) == {
        "dip_window_days",
        "dip_threshold",
        "min_edge",
        "take_profit",
        "max_hold_days",
    }
    assert 30 <= int(params["max_hold_days"]) <= 180
    strategy = entry.factory(params)
    assert strategy.name == "cost-aware-reversion"


def test_ml_ranker_registered_and_buildable() -> None:
    entry = REGISTRY["ml-ranker"]
    study = optuna.create_study(sampler=optuna.samplers.RandomSampler(seed=1))
    trial = study.ask()
    params = entry.space(trial)
    assert set(params) == {
        "horizon_days",
        "rebalance_days",
        "top_n",
        "train_days",
        "max_iter",
        "learning_rate",
        "min_samples_leaf",
    }
    assert 14 <= int(params["horizon_days"]) <= 60
    strategy = entry.factory(params)
    assert strategy.name == "ml-ranker"
