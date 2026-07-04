import optuna
import pytest

from pkmn_quant.research.search import SearchSpec, optimize_params


def quadratic_eval(params: dict[str, float | int]) -> float:
    # Max at x=3: deterministic stand-in for "run a backtest, return the metric".
    x = float(params["x"])
    return -((x - 3.0) ** 2)


def space(trial: optuna.Trial) -> dict[str, float | int]:
    return {"x": trial.suggest_float("x", 0.0, 10.0)}


def test_optimize_finds_maximum_deterministically() -> None:
    spec = SearchSpec(space=space, n_trials=40, seed=7)
    best_a = optimize_params(spec, quadratic_eval)
    best_b = optimize_params(spec, quadratic_eval)
    assert best_a == best_b  # seeded -> reproducible
    assert abs(float(best_a["x"]) - 3.0) < 1.0


def test_zero_trials_raises() -> None:
    with pytest.raises(ValueError):
        optimize_params(SearchSpec(space=space, n_trials=0, seed=1), quadratic_eval)
