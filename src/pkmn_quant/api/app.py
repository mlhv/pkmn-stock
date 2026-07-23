"""FastAPI read-only research explorer API."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query

from pkmn_quant.api import data
from pkmn_quant.api.models import (
    ConfidenceInterval,
    EquityPoint,
    EvaluateResponse,
    FoldRow,
    RigorCI,
    RunDetail,
    RunSummary,
    StrategyInfo,
    StrategyStat,
    WalkForwardResponse,
)


def create_app(root: Path) -> FastAPI:
    app = FastAPI(title="pkmn_quant explorer", version="1")

    @app.get("/api/runs", response_model=list[RunSummary])
    def list_runs(
        command: str | None = Query(default=None),
        strategy: str | None = Query(default=None),
    ) -> list[RunSummary]:
        return [
            RunSummary(
                run_id=r.run_id,
                recorded_at=r.recorded_at,
                command=r.command,
                strategy=r.strategy,
                git_sha=r.git_sha,
                git_dirty=r.git_dirty,
                results=r.results,
            )
            for r in data.list_runs(root, command=command, strategy=strategy)
        ]

    @app.get("/api/runs/{run_id}", response_model=RunDetail)
    def get_run(run_id: str) -> RunDetail:
        try:
            r = data.get_run(root, run_id)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"unknown run_id: {run_id}") from None
        return RunDetail(
            run_id=r.run_id,
            recorded_at=r.recorded_at,
            command=r.command,
            strategy=r.strategy,
            git_sha=r.git_sha,
            git_dirty=r.git_dirty,
            results=r.results,
            config_hash=r.config_hash,
            config=r.config,
            data_fingerprint=r.data_fingerprint,
            runtime=r.runtime,
        )

    @app.get("/api/walkforward/{run_id}", response_model=WalkForwardResponse)
    def get_walkforward(run_id: str) -> WalkForwardResponse:
        try:
            raw = data.load_walkforward(root, run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        rigor = raw.get("rigor", {}).get("stitched_total_return_ci")
        return WalkForwardResponse(
            run_id=run_id,
            strategy=raw["strategy"],
            summary=raw["summary"],
            folds=[FoldRow(**f) for f in raw["folds"]],
            rigor=RigorCI(**rigor) if rigor else None,
            equity_curve=[EquityPoint(**p) for p in raw["equity_curve"]],
        )

    @app.get("/api/evaluate/{run_id}", response_model=EvaluateResponse)
    def get_evaluate(run_id: str) -> EvaluateResponse:
        try:
            raw = data.load_evaluate(root, run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        return EvaluateResponse(
            run_id=run_id,
            reality_check_p=raw["reality_check_p"],
            benchmark=raw["benchmark"],
            n_days=raw["n_days"],
            start=raw["start"],
            end=raw["end"],
            params=raw["params"],
            strategies=[
                StrategyStat(
                    strategy=name,
                    total_return=s["total_return"],
                    ci=ConfidenceInterval(**s["ci"]),
                    sharpe=s["sharpe"],
                    dsr=s["dsr"],
                )
                for name, s in sorted(raw["strategies"].items())
            ],
        )

    @app.get("/api/strategies", response_model=list[StrategyInfo])
    def get_strategies() -> list[StrategyInfo]:
        return [StrategyInfo(**s) for s in data.strategy_catalog()]

    return app


app = create_app(Path(os.environ.get("PKMN_DATA_ROOT", ".")))
