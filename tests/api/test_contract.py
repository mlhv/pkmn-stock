"""The web/ fixtures ARE the API contract: every one must satisfy its
Pydantic response model. If the backend shape changes, this fails until the
fixture is updated — and the frontend tests then catch the frontend side."""

import json
from pathlib import Path

from pkmn_quant.api.models import EvaluateResponse, RunSummary, WalkForwardResponse

FIX = Path(__file__).resolve().parents[2] / "web" / "tests" / "fixtures"


def test_runs_fixture_matches_model() -> None:
    for row in json.loads((FIX / "runs.json").read_text()):
        RunSummary.model_validate(row)


def test_walkforward_fixture_matches_model() -> None:
    WalkForwardResponse.model_validate(json.loads((FIX / "walkforward.json").read_text()))


def test_evaluate_fixture_matches_model() -> None:
    EvaluateResponse.model_validate(json.loads((FIX / "evaluate.json").read_text()))
