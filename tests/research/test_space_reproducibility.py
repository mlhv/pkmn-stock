"""Bit-for-bit guard: the ParamSpec-derived optuna space must reproduce the
original hand-written spaces exactly, so past walk-forward runs remain
reproducible. The _OLD_* functions below are verbatim copies of the
pre-refactor registry._*_space bodies — the frozen oracle."""

import optuna
import pytest

from pkmn_quant.research.registry import REGISTRY, Params

optuna.logging.set_verbosity(optuna.logging.WARNING)


def _OLD_sealed(trial: optuna.Trial) -> Params:
    return {
        "min_drawdown": trial.suggest_float("min_drawdown", 0.10, 0.50),
        "take_profit": trial.suggest_float("take_profit", 1.2, 2.5),
        "min_age_days": trial.suggest_int("min_age_days", 30, 180),
    }


def _OLD_dip(trial: optuna.Trial) -> Params:
    return {
        "dip_threshold": trial.suggest_float("dip_threshold", 0.10, 0.50),
        "hold_days": trial.suggest_int("hold_days", 7, 90),
        "take_profit": trial.suggest_float("take_profit", 1.05, 1.6),
    }


def _OLD_momentum(trial: optuna.Trial) -> Params:
    return {
        "lookback_days": trial.suggest_int("lookback_days", 14, 120),
        "top_n": trial.suggest_int("top_n", 5, 25),
        "rebalance_days": trial.suggest_int("rebalance_days", 7, 60),
    }


def _OLD_reversion(trial: optuna.Trial) -> Params:
    return {
        "dip_window_days": trial.suggest_int("dip_window_days", 14, 90),
        "dip_threshold": trial.suggest_float("dip_threshold", 0.15, 0.50),
        "min_edge": trial.suggest_float("min_edge", 0.02, 0.15),
        "take_profit": trial.suggest_float("take_profit", 1.1, 1.6),
        "max_hold_days": trial.suggest_int("max_hold_days", 30, 180),
    }


def _OLD_ml_ranker(trial: optuna.Trial) -> Params:
    return {
        "horizon_days": trial.suggest_int("horizon_days", 14, 60),
        "rebalance_days": trial.suggest_int("rebalance_days", 21, 90),
        "top_n": trial.suggest_int("top_n", 3, 15),
        "train_days": trial.suggest_int("train_days", 120, 540),
        "max_iter": trial.suggest_int("max_iter", 50, 300, log=True),
        "learning_rate": trial.suggest_float("learning_rate", 0.03, 0.3, log=True),
        "min_samples_leaf": trial.suggest_int("min_samples_leaf", 10, 50),
    }


def _OLD_ml_ranker_v2(trial: optuna.Trial) -> Params:
    return {
        "horizon_days": trial.suggest_int("horizon_days", 14, 60),
        "rebalance_days": trial.suggest_int("rebalance_days", 21, 90),
        "top_n": trial.suggest_int("top_n", 3, 15),
        "train_days": trial.suggest_int("train_days", 120, 540),
        "min_price": trial.suggest_float("min_price", 1.0, 10.0),
        "min_samples_leaf": trial.suggest_int("min_samples_leaf", 10, 50),
    }


_OLD = {
    "sealed-accumulation": _OLD_sealed,
    "dip-buyer": _OLD_dip,
    "xs-momentum": _OLD_momentum,
    "cost-aware-reversion": _OLD_reversion,
    "ml-ranker": _OLD_ml_ranker,
    "ml-ranker-v2": _OLD_ml_ranker_v2,
}


def _trajectory(space, n=20, seed=7):
    """The exact sequence of param dicts a seeded TPESampler suggests."""
    study = optuna.create_study(sampler=optuna.samplers.TPESampler(seed=seed))
    out = []
    for _ in range(n):
        trial = study.ask()
        out.append(space(trial))
        study.tell(trial, 0.0)  # constant objective: isolates the space
    return out


@pytest.mark.parametrize("name", sorted(_OLD))
def test_derived_space_matches_old_bit_for_bit(name: str) -> None:
    old = _trajectory(_OLD[name])
    new = _trajectory(REGISTRY[name].space)
    assert new == old
