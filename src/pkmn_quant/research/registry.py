"""Tunable strategies: factory + optuna search space, keyed by CLI name."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

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
class ParamSpec:
    name: str
    kind: Literal["int", "float"]
    default: float | int
    low: float | int
    high: float | int
    log: bool = False


def _space_from_params(params: tuple[ParamSpec, ...]) -> Callable[[optuna.Trial], Params]:
    """Derive a flat optuna space from declarative specs. Suggestion order is
    the tuple order — parity-relevant, the seeded sampler is order-sensitive."""

    def space(trial: optuna.Trial) -> Params:
        out: Params = {}
        for p in params:
            if p.kind == "int":
                out[p.name] = trial.suggest_int(p.name, int(p.low), int(p.high), log=p.log)
            else:
                out[p.name] = trial.suggest_float(p.name, float(p.low), float(p.high), log=p.log)
        return out

    return space


@dataclass(frozen=True)
class RegistryEntry:
    factory: Callable[[Params], Strategy]
    params: tuple[ParamSpec, ...]

    @property
    def space(self) -> Callable[[optuna.Trial], Params]:
        return _space_from_params(self.params)


def _sealed_factory(p: Params) -> Strategy:
    return SealedAccumulation(
        min_drawdown=float(p["min_drawdown"]),
        take_profit=float(p["take_profit"]),
        min_age_days=int(p["min_age_days"]),
    )


def _dip_factory(p: Params) -> Strategy:
    return DipBuyer(
        dip_threshold=float(p["dip_threshold"]),
        hold_days=int(p["hold_days"]),
        take_profit=float(p["take_profit"]),
    )


def _momentum_factory(p: Params) -> Strategy:
    return CrossSectionalMomentum(
        lookback_days=int(p["lookback_days"]),
        top_n=int(p["top_n"]),
        rebalance_days=int(p["rebalance_days"]),
    )


def _reversion_factory(p: Params) -> Strategy:
    return CostAwareReversion(
        dip_window_days=int(p["dip_window_days"]),
        dip_threshold=float(p["dip_threshold"]),
        min_edge=float(p["min_edge"]),
        take_profit=float(p["take_profit"]),
        max_hold_days=int(p["max_hold_days"]),
    )


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
    "sealed-accumulation": RegistryEntry(
        factory=_sealed_factory,
        params=(
            ParamSpec("min_drawdown", "float", 0.25, 0.10, 0.50),
            ParamSpec("take_profit", "float", 1.5, 1.2, 2.5),
            ParamSpec("min_age_days", "int", 60, 30, 180),
        ),
    ),
    "dip-buyer": RegistryEntry(
        factory=_dip_factory,
        params=(
            ParamSpec("dip_threshold", "float", 0.30, 0.10, 0.50),
            ParamSpec("hold_days", "int", 30, 7, 90),
            ParamSpec("take_profit", "float", 1.25, 1.05, 1.6),
        ),
    ),
    "xs-momentum": RegistryEntry(
        factory=_momentum_factory,
        params=(
            ParamSpec("lookback_days", "int", 60, 14, 120),
            ParamSpec("top_n", "int", 10, 5, 25),
            ParamSpec("rebalance_days", "int", 30, 7, 60),
        ),
    ),
    "cost-aware-reversion": RegistryEntry(
        factory=_reversion_factory,
        params=(
            ParamSpec("dip_window_days", "int", 30, 14, 90),
            ParamSpec("dip_threshold", "float", 0.25, 0.15, 0.50),
            ParamSpec("min_edge", "float", 0.05, 0.02, 0.15),
            ParamSpec("take_profit", "float", 1.25, 1.1, 1.6),
            ParamSpec("max_hold_days", "int", 120, 30, 180),
        ),
    ),
    "ml-ranker": RegistryEntry(
        factory=_ml_ranker_factory,
        params=(
            ParamSpec("horizon_days", "int", 30, 14, 60),
            ParamSpec("rebalance_days", "int", 30, 21, 90),
            ParamSpec("top_n", "int", 8, 3, 15),
            ParamSpec("train_days", "int", 365, 120, 540),
            ParamSpec("max_iter", "int", 100, 50, 300, log=True),
            ParamSpec("learning_rate", "float", 0.1, 0.03, 0.3, log=True),
            ParamSpec("min_samples_leaf", "int", 20, 10, 50),
        ),
    ),
    # max_iter / learning_rate are deliberately absent: in-loop purged
    # validation owns them (research/purged.py DEFAULT_GRID).
    "ml-ranker-v2": RegistryEntry(
        factory=_ml_ranker_v2_factory,
        params=(
            ParamSpec("horizon_days", "int", 30, 14, 60),
            ParamSpec("rebalance_days", "int", 30, 21, 90),
            ParamSpec("top_n", "int", 8, 3, 15),
            ParamSpec("train_days", "int", 365, 120, 540),
            ParamSpec("min_price", "float", 3.0, 1.0, 10.0),
            ParamSpec("min_samples_leaf", "int", 20, 10, 50),
        ),
    ),
}
