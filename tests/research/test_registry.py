import optuna

from pkmn_quant.research.registry import REGISTRY


def test_registry_has_all_tunable_strategies() -> None:
    assert set(REGISTRY) == {"sealed-accumulation", "dip-buyer", "xs-momentum"}


def test_factories_build_with_sampled_params() -> None:
    for name, entry in REGISTRY.items():
        study = optuna.create_study(sampler=optuna.samplers.RandomSampler(seed=1))
        trial = study.ask()
        params = entry.space(trial)
        strategy = entry.factory(params)
        assert strategy.name == name
