"""FastAPI read-only research explorer API."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query

from pkmn_quant.api import data
from pkmn_quant.api.models import RunDetail, RunSummary


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

    return app


app = create_app(Path(os.environ.get("PKMN_DATA_ROOT", ".")))
