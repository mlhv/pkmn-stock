"""Pydantic response models — the API's typed contract (and OpenAPI schema)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class RunSummary(BaseModel):
    run_id: str
    recorded_at: str
    command: str
    strategy: str
    git_sha: str | None
    git_dirty: bool
    results: dict[str, float]


class RunDetail(RunSummary):
    config_hash: str
    config: dict[str, Any]
    data_fingerprint: dict[str, Any]
    runtime: dict[str, Any] | None = None


class EquityPoint(BaseModel):
    date: str
    equity: float


class FoldRow(BaseModel):
    is_start: str
    is_end: str
    oos_start: str
    oos_end: str
    params: dict[str, Any]
    is_summary: dict[str, float]
    oos_summary: dict[str, float]


class RigorCI(BaseModel):
    point: float
    lo: float
    hi: float
    level: float
    n_boot: int
    mean_block: float
    seed: int


class WalkForwardResponse(BaseModel):
    run_id: str
    strategy: str
    summary: dict[str, float]
    folds: list[FoldRow]
    rigor: RigorCI | None
    equity_curve: list[EquityPoint]


class ConfidenceInterval(BaseModel):
    point: float
    lo: float
    hi: float
    level: float


class StrategyStat(BaseModel):
    strategy: str
    total_return: float
    ci: ConfidenceInterval
    sharpe: float
    dsr: float | None


class EvaluateResponse(BaseModel):
    run_id: str
    reality_check_p: float
    benchmark: str
    n_days: int
    start: str
    end: str
    params: dict[str, Any]
    strategies: list[StrategyStat]


class StrategyInfo(BaseModel):
    name: str
    thesis: str
