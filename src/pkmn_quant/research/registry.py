"""Tunable strategies: factory + optuna search space, keyed by CLI name."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import optuna

from pkmn_quant.engine.strategy import Strategy
from pkmn_quant.strategies.cost_aware_reversion import CostAwareReversion
from pkmn_quant.strategies.dip_buyer import DipBuyer
from pkmn_quant.strategies.ml_ranker import MLRanker
from pkmn_quant.strategies.ml_ranker_v2 import MLRankerV2
from pkmn_quant.strategies.momentum import CrossSectionalMomentum
from pkmn_quant.strategies.sealed_accumulation import SealedAccumulation

Params = dict[str, float | int]


@dataclass(frozen=True)
class RegistryEntry:
    factory: Callable[[Params], Strategy]
    space: Callable[[optuna.Trial], Params]


def _sealed_space(trial: optuna.Trial) -> Params:
    return {
        "min_drawdown": trial.suggest_float("min_drawdown", 0.10, 0.50),
        "take_profit": trial.suggest_float("take_profit", 1.2, 2.5),
        "min_age_days": trial.suggest_int("min_age_days", 30, 180),
    }


def _sealed_factory(p: Params) -> Strategy:
    return SealedAccumulation(
        min_drawdown=float(p["min_drawdown"]),
        take_profit=float(p["take_profit"]),
        min_age_days=int(p["min_age_days"]),
    )


def _dip_space(trial: optuna.Trial) -> Params:
    return {
        "dip_threshold": trial.suggest_float("dip_threshold", 0.10, 0.50),
        "hold_days": trial.suggest_int("hold_days", 7, 90),
        "take_profit": trial.suggest_float("take_profit", 1.05, 1.6),
    }


def _dip_factory(p: Params) -> Strategy:
    return DipBuyer(
        dip_threshold=float(p["dip_threshold"]),
        hold_days=int(p["hold_days"]),
        take_profit=float(p["take_profit"]),
    )


def _momentum_space(trial: optuna.Trial) -> Params:
    return {
        "lookback_days": trial.suggest_int("lookback_days", 14, 120),
        "top_n": trial.suggest_int("top_n", 5, 25),
        "rebalance_days": trial.suggest_int("rebalance_days", 7, 60),
    }


def _momentum_factory(p: Params) -> Strategy:
    return CrossSectionalMomentum(
        lookback_days=int(p["lookback_days"]),
        top_n=int(p["top_n"]),
        rebalance_days=int(p["rebalance_days"]),
    )


def _reversion_space(trial: optuna.Trial) -> Params:
    return {
        "dip_window_days": trial.suggest_int("dip_window_days", 14, 90),
        "dip_threshold": trial.suggest_float("dip_threshold", 0.15, 0.50),
        "min_edge": trial.suggest_float("min_edge", 0.02, 0.15),
        "take_profit": trial.suggest_float("take_profit", 1.1, 1.6),
        "max_hold_days": trial.suggest_int("max_hold_days", 30, 180),
    }


def _reversion_factory(p: Params) -> Strategy:
    return CostAwareReversion(
        dip_window_days=int(p["dip_window_days"]),
        dip_threshold=float(p["dip_threshold"]),
        min_edge=float(p["min_edge"]),
        take_profit=float(p["take_profit"]),
        max_hold_days=int(p["max_hold_days"]),
    )


def _ml_ranker_space(trial: optuna.Trial) -> Params:
    return {
        "horizon_days": trial.suggest_int("horizon_days", 14, 60),
        "rebalance_days": trial.suggest_int("rebalance_days", 21, 90),
        "top_n": trial.suggest_int("top_n", 3, 15),
        "train_days": trial.suggest_int("train_days", 120, 540),
        "max_iter": trial.suggest_int("max_iter", 50, 300, log=True),
        "learning_rate": trial.suggest_float("learning_rate", 0.03, 0.3, log=True),
        "min_samples_leaf": trial.suggest_int("min_samples_leaf", 10, 50),
    }


def _ml_ranker_factory(p: Params) -> Strategy:
    return MLRanker(
        horizon_days=int(p["horizon_days"]),
        rebalance_days=int(p["rebalance_days"]),
        top_n=int(p["top_n"]),
        train_days=int(p["train_days"]),
        max_iter=int(p["max_iter"]),
        learning_rate=float(p["learning_rate"]),
        min_samples_leaf=int(p["min_samples_leaf"]),
    )


def _ml_ranker_v2_space(trial: optuna.Trial) -> Params:
    # max_iter / learning_rate are deliberately absent: in-loop purged
    # validation owns them (research/purged.py DEFAULT_GRID).
    return {
        "horizon_days": trial.suggest_int("horizon_days", 14, 60),
        "rebalance_days": trial.suggest_int("rebalance_days", 21, 90),
        "top_n": trial.suggest_int("top_n", 3, 15),
        "train_days": trial.suggest_int("train_days", 120, 540),
        "min_price": trial.suggest_float("min_price", 1.0, 10.0),
        "min_samples_leaf": trial.suggest_int("min_samples_leaf", 10, 50),
    }


def _ml_ranker_v2_factory(p: Params) -> Strategy:
    return MLRankerV2(
        horizon_days=int(p["horizon_days"]),
        rebalance_days=int(p["rebalance_days"]),
        top_n=int(p["top_n"]),
        train_days=int(p["train_days"]),
        min_price=float(p["min_price"]),
        min_samples_leaf=int(p["min_samples_leaf"]),
    )


REGISTRY: dict[str, RegistryEntry] = {
    "sealed-accumulation": RegistryEntry(factory=_sealed_factory, space=_sealed_space),
    "dip-buyer": RegistryEntry(factory=_dip_factory, space=_dip_space),
    "xs-momentum": RegistryEntry(factory=_momentum_factory, space=_momentum_space),
    "cost-aware-reversion": RegistryEntry(factory=_reversion_factory, space=_reversion_space),
    "ml-ranker": RegistryEntry(factory=_ml_ranker_factory, space=_ml_ranker_space),
    "ml-ranker-v2": RegistryEntry(factory=_ml_ranker_v2_factory, space=_ml_ranker_v2_space),
}
