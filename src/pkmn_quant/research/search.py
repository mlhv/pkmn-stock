"""Seeded optuna search over a strategy's parameter space."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import optuna

optuna.logging.set_verbosity(optuna.logging.WARNING)

Params = dict[str, float | int]


@dataclass(frozen=True)
class SearchSpec:
    """Search space + budget. The space callable must suggest only float or
    int parameters (flat, one suggest per key) — categorical or derived params
    would violate the Params contract via study.best_params.
    """

    space: Callable[[optuna.Trial], Params]
    n_trials: int
    seed: int


def optimize_params(spec: SearchSpec, evaluate: Callable[[Params], float]) -> Params:
    """Maximize evaluate(params) over the space; deterministic under the seed.

    Exceptions raised by evaluate propagate immediately and abort the study.
    """
    if spec.n_trials <= 0:
        raise ValueError("n_trials must be positive")
    study = optuna.create_study(
        direction="maximize", sampler=optuna.samplers.TPESampler(seed=spec.seed)
    )

    def objective(trial: optuna.Trial) -> float:
        return evaluate(spec.space(trial))

    study.optimize(objective, n_trials=spec.n_trials)
    return dict(study.best_params)
