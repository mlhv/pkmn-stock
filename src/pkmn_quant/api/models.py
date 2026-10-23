"""Pydantic response models — the API's typed contract (and OpenAPI schema)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class ApiModel(BaseModel):
    """Base for every API response model: `extra="forbid"` so a field the
    model dropped (or a fixture/artifact with an unexpected extra key) fails
    loudly at validation time instead of silently vanishing.

    Verified safe against real on-disk artifacts (data/results/wf-*/
    walkforward.json, data/results/evaluate-*/evaluate.json,
    stitched_equity.parquet) before adoption: every splatted sub-dict
    (fold rows, the rigor CI block, evaluate CI blocks, equity points,
    strategy catalog entries) carries exactly the declared fields, no more —
    see the Plan report for the field-by-field comparison.
    """

    model_config = ConfigDict(extra="forbid")


class RunSummary(ApiModel):
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


class EquityPoint(ApiModel):
    date: str
    equity: float


class FoldRow(ApiModel):
    is_start: str
    is_end: str
    oos_start: str
    oos_end: str
    params: dict[str, Any]
    is_summary: dict[str, float]
    oos_summary: dict[str, float]


class RigorCI(ApiModel):
    point: float
    lo: float
    hi: float
    level: float
    n_boot: int
    mean_block: float
    seed: int


class WalkForwardResponse(ApiModel):
    run_id: str
    strategy: str
    summary: dict[str, float]
    folds: list[FoldRow]
    rigor: RigorCI | None
    equity_curve: list[EquityPoint]


class ConfidenceInterval(ApiModel):
    point: float
    lo: float
    hi: float
    level: float


class StrategyStat(ApiModel):
    strategy: str
    total_return: float
    ci: ConfidenceInterval
    sharpe: float
    dsr: float | None


class EvaluateResponse(ApiModel):
    run_id: str
    reality_check_p: float
    benchmark: str
    n_days: int
    start: str
    end: str
    params: dict[str, Any]
    strategies: list[StrategyStat]


class ParamSpecInfo(ApiModel):
    name: str
    kind: str  # "int" | "float"
    default: float
    low: float
    high: float
    log: bool


class ProductHit(ApiModel):
    product_id: int
    name: str
    kind: str
    sub_types: list[str]
    last_priced_on: str


class StrategyInfo(ApiModel):
    name: str
    thesis: str
    params: list[ParamSpecInfo]


class HoldingInput(ApiModel):
    product_id: int
    sub_type: str
    quantity: int
    avg_cost: float
    opened_on: str  # ISO date


class BacktestRequest(ApiModel):
    strategy: str
    start: str
    end: str
    params: dict[str, float | int | str] = {}
    cash: float = 10_000.0
    impact: bool = True
    execution: Literal["book", "formula"] = "book"
    kind: str = "sealed"
    holdings: list[HoldingInput] = []


class JobStatus(ApiModel):
    job_id: str
    status: str
    request: dict[str, Any]
    submitted_at: str
    started_at: str | None
    finished_at: str | None
    run_id: str | None
    benchmark_run_id: str | None
    error: str | None


class FillRow(ApiModel):
    day: str
    product_id: int
    sub_type: str
    quantity: int
    price: float
    fees: float
    impact: float
    seeded: bool


class FillsPage(ApiModel):
    run_id: str
    offset: int
    limit: int
    n_fills: int
    fills: list[FillRow]


class BenchmarkCurve(ApiModel):
    run_id: str
    strategy: str
    equity_curve: list[EquityPoint]


class BacktestDetail(ApiModel):
    run_id: str
    strategy: str
    config: dict[str, Any]
    summary: dict[str, float]
    equity_curve: list[EquityPoint]
    benchmark: BenchmarkCurve | None
    n_fills: int
    seeded_exits: list[str]
