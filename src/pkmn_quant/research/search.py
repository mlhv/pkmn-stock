"""Seeded optuna search over a strategy's parameter space."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import optuna

Params = dict[str, float | int]


@dataclass(frozen=True)
class SearchSpec:
    space: Callable[[optuna.Trial], Params]
    n_trials: int
    seed: int


def optimize_params(spec: SearchSpec, evaluate: Callable[[Params], float]) -> Params:
    """Maximize evaluate(params) over the space; deterministic under the seed."""
    if spec.n_trials <= 0:
        raise ValueError("n_trials must be positive")
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(
        direction="maximize", sampler=optuna.samplers.TPESampler(seed=spec.seed)
    )

    def objective(trial: optuna.Trial) -> float:
        return evaluate(spec.space(trial))

    study.optimize(objective, n_trials=spec.n_trials)
    return dict(study.best_params)
