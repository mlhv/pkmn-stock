"""FastAPI research explorer API: read-only browsing plus (Plan 2c) a
trigger endpoint that hands parameterized backtests to a background job."""

from __future__ import annotations

import datetime as dt
import os
from concurrent.futures import Executor, ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Response

from pkmn_quant.api import data
from pkmn_quant.api.jobs import Job, JobManager, JobResult
from pkmn_quant.api.models import (
    BacktestDetail,
    BacktestRequest,
    BenchmarkCurve,
    ConfidenceInterval,
    EquityPoint,
    EvaluateResponse,
    FillRow,
    FillsPage,
    FoldRow,
    HoldingInput,
    JobStatus,
    ProductHit,
    RigorCI,
    RunDetail,
    RunSummary,
    StrategyInfo,
    StrategyStat,
    WalkForwardResponse,
)
from pkmn_quant.engine.native import SeededHolding
from pkmn_quant.engine.portfolio import Asset
from pkmn_quant.research.backtest_run import (
    resolve_params,
    run_with_benchmark,
    validate_opened_on,
)


def _job_status(job: Job) -> JobStatus:
    return JobStatus(**vars(job))


def _to_seeded(rows: list[HoldingInput], start: dt.date) -> list[SeededHolding]:
    """Row-shape validation only — universe/mark checks need a PreparedMarket
    and therefore happen inside the job (see the plan's validation split).

    ``opened_on`` vs the window start belongs here rather than in the job: it
    needs no market data, so it can be a synchronous 400. The capability layer
    re-checks it (``validate_opened_on``) for every non-HTTP caller."""
    out: list[SeededHolding] = []
    for i, r in enumerate(rows, start=1):
        if r.quantity <= 0:
            raise ValueError(f"holding {i}: quantity must be > 0, got {r.quantity}")
        if r.avg_cost < 0:
            raise ValueError(f"holding {i}: avg_cost must be >= 0, got {r.avg_cost}")
        try:
            opened = dt.date.fromisoformat(r.opened_on)
        except ValueError as exc:
            raise ValueError(f"holding {i}: opened_on {r.opened_on!r} is not a date") from exc
        validate_opened_on(i, opened, start)
        out.append(
            SeededHolding(
                asset=Asset(product_id=r.product_id, sub_type=r.sub_type),
                quantity=r.quantity,
                avg_cost=r.avg_cost,
                opened_on=opened,
            )
        )
    return out


def create_app(root: Path, executor: Executor | None = None) -> FastAPI:
    app = FastAPI(title="pkmn_quant explorer", version="1")
    jobs = JobManager(executor if executor is not None else ThreadPoolExecutor(max_workers=1))

    @app.get("/api/runs", response_model=list[RunSummary])
    def list_runs(
        command: str | None = Query(default=None),
        strategy: str | None = Query(default=None),
        include_benchmarks: bool = Query(default=False),
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
                execution=str(r.config.get("execution", "formula")),
            )
            for r in data.list_runs(
                root, command=command, strategy=strategy, include_benchmarks=include_benchmarks
            )
        ]

    @app.get("/api/runs/{run_id}", response_model=RunDetail)
    def get_run(run_id: str) -> RunDetail:
        try:
            r = data.get_run(root, run_id)
        except data.NotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        return RunDetail(
            run_id=r.run_id,
            recorded_at=r.recorded_at,
            command=r.command,
            strategy=r.strategy,
            git_sha=r.git_sha,
            git_dirty=r.git_dirty,
            results=r.results,
            execution=str(r.config.get("execution", "formula")),
            config_hash=r.config_hash,
            config=r.config,
            data_fingerprint=r.data_fingerprint,
            runtime=r.runtime,
        )

    @app.get("/api/walkforward/{run_id}", response_model=WalkForwardResponse)
    def get_walkforward(run_id: str) -> WalkForwardResponse:
        try:
            raw = data.load_walkforward(root, run_id)
        except data.NotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        rigor = raw.get("rigor", {}).get("stitched_total_return_ci")
        return WalkForwardResponse(
            run_id=run_id,
            strategy=raw["strategy"],
            summary=raw["summary"],
            folds=[FoldRow(**f) for f in raw["folds"]],
            rigor=RigorCI(**rigor) if rigor else None,
            equity_curve=[EquityPoint(**p) for p in raw["equity_curve"]],
            execution=str(raw.get("execution", "formula")),
        )

    @app.get("/api/evaluate/{run_id}", response_model=EvaluateResponse)
    def get_evaluate(run_id: str) -> EvaluateResponse:
        try:
            raw = data.load_evaluate(root, run_id)
        except data.NotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        return EvaluateResponse(
            run_id=run_id,
            reality_check_p=raw["reality_check_p"],
            benchmark=raw["benchmark"],
            n_days=raw["n_days"],
            start=raw["start"],
            end=raw["end"],
            params=raw["params"],
            execution=str(raw.get("execution", "formula")),
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

    @app.get("/api/products", response_model=list[ProductHit])
    def search_products(
        q: str = Query(default=""),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> list[ProductHit]:
        return [ProductHit(**p) for p in data.search_products(root, q, limit)]

    @app.post("/api/backtest", response_model=JobStatus, status_code=202)
    def trigger_backtest(req: BacktestRequest, response: Response) -> JobStatus:
        try:
            start = dt.date.fromisoformat(req.start)
            end = dt.date.fromisoformat(req.end)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        if start > end:
            raise HTTPException(status_code=400, detail=f"start {req.start} is after end {req.end}")
        if req.cash < 0:
            raise HTTPException(status_code=400, detail=f"cash must be >= 0, got {req.cash}")
        if req.kind not in ("sealed", "single"):
            raise HTTPException(
                status_code=400, detail=f"unknown kind {req.kind!r}; choose sealed or single"
            )
        try:
            resolve_params(req.strategy, dict(req.params))  # fail fast on params
            holdings = _to_seeded(req.holdings, start)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

        def work() -> JobResult:
            paired = run_with_benchmark(
                root=root,
                strategy_name=req.strategy,
                params=dict(req.params),
                cash=req.cash,
                holdings=holdings,
                start=start,
                end=end,
                impact=req.impact,
                execution=req.execution,
                kind=req.kind,
            )
            bench_id = paired.benchmark.run_id if paired.benchmark else None
            return (paired.primary.run_id, bench_id)

        job = jobs.submit(req.model_dump(), work)
        current = jobs.get(job.job_id) or job
        response.status_code = 202
        return _job_status(current)

    @app.get("/api/backtest/{run_id}", response_model=BacktestDetail)
    def get_backtest(run_id: str) -> BacktestDetail:
        try:
            raw = data.load_backtest(root, run_id)
        except data.NotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        bench = raw["benchmark"]
        return BacktestDetail(
            run_id=raw["run_id"],
            strategy=raw["strategy"],
            config=raw["config"],
            summary=raw["summary"],
            equity_curve=[EquityPoint(**p) for p in raw["equity_curve"]],
            benchmark=(
                BenchmarkCurve(
                    run_id=bench["run_id"],
                    strategy=bench["strategy"],
                    equity_curve=[EquityPoint(**p) for p in bench["equity_curve"]],
                )
                if bench
                else None
            ),
            n_fills=raw["n_fills"],
            seeded_exits=raw["seeded_exits"],
        )

    @app.get("/api/backtest/{run_id}/fills", response_model=FillsPage)
    def get_backtest_fills(
        run_id: str,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> FillsPage:
        try:
            raw = data.load_fills(root, run_id, offset, limit)
        except data.NotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        return FillsPage(
            run_id=raw["run_id"],
            offset=raw["offset"],
            limit=raw["limit"],
            n_fills=raw["n_fills"],
            fills=[FillRow(**f) for f in raw["fills"]],
        )

    @app.get("/api/jobs", response_model=list[JobStatus])
    def list_jobs() -> list[JobStatus]:
        return [_job_status(j) for j in jobs.list_jobs()]

    @app.get("/api/jobs/{job_id}", response_model=JobStatus)
    def get_job(job_id: str) -> JobStatus:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"unknown job_id: {job_id}")
        return _job_status(job)

    return app


app = create_app(Path(os.environ.get("PKMN_DATA_ROOT", ".")))
