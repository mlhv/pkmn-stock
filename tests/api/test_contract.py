"""The web/ fixtures ARE the API contract: every one must satisfy its
Pydantic response model. If the backend shape changes, this fails until the
fixture is updated — and the frontend tests then catch the frontend side."""

import json
from pathlib import Path

from pkmn_quant.api.models import (
    BacktestDetail,
    EvaluateResponse,
    FillsPage,
    JobStatus,
    ProductHit,
    RunSummary,
    StrategyInfo,
    WalkForwardResponse,
)

FIX = Path(__file__).resolve().parents[2] / "web" / "tests" / "fixtures"


def test_runs_fixture_matches_model() -> None:
    for row in json.loads((FIX / "runs.json").read_text()):
        RunSummary.model_validate(row)


def test_walkforward_fixture_matches_model() -> None:
    WalkForwardResponse.model_validate(json.loads((FIX / "walkforward.json").read_text()))


def test_evaluate_fixture_matches_model() -> None:
    EvaluateResponse.model_validate(json.loads((FIX / "evaluate.json").read_text()))


def test_strategies_fixture_matches_model() -> None:
    for row in json.loads((FIX / "strategies.json").read_text()):
        StrategyInfo.model_validate(row)


def test_products_fixture_matches_model() -> None:
    for row in json.loads((FIX / "products.json").read_text()):
        ProductHit.model_validate(row)


def test_jobs_fixture_matches_model() -> None:
    for row in json.loads((FIX / "jobs.json").read_text()):
        JobStatus.model_validate(row)


def test_backtest_detail_fixture_matches_model() -> None:
    BacktestDetail.model_validate(json.loads((FIX / "backtest-detail.json").read_text()))


def test_fills_fixture_matches_model() -> None:
    FillsPage.model_validate(json.loads((FIX / "fills.json").read_text()))
