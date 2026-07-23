# Web Research Explorer Implementation Plan (Plan 1: read-only viewer)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A typed FastAPI read-only API over the existing artifacts/registry plus a React + TypeScript SPA that browses the registry, drills into walk-forward runs, and presents the rigor/evaluate strategy-zoo verdict.

**Architecture:** Backend under `src/pkmn_quant/api/` (inside the gated Python package, reuses `load_runs` and reads walkforward.json/evaluate.json/parquet), exposing 5 JSON endpoints keyed off the registry. Frontend in a new top-level `web/` (Vite + React + TS + Recharts + Vitest), three routes over a typed API client. Shared JSON fixtures are the cross-language contract: a Python test asserts each fixture matches its Pydantic model; the frontend tests render from the same fixtures.

**Tech Stack:** FastAPI, uvicorn, Pydantic, polars (backend); Vite, React 18, TypeScript, React Router, Recharts, Vitest, React Testing Library (frontend); the existing uv/ruff/mypy/pytest gates plus a new Node CI job.

**Spec:** `docs/superpowers/specs/2026-07-22-web-explorer-design.md`. Read it before starting any task.

## Global Constraints

- **Read-only.** No POST/PUT/DELETE, no run-triggering, no writes to `data/` (that is Plan 2). The API only reads artifacts the CLI already produced.
- **Registry is the index.** Detail endpoints resolve a run's artifact directory from its registry record's `artifact_path`; a run not in `data/runs/registry.jsonl` is not browsable. Unknown/missing → clean 404 JSON `detail`, never a 500 or a stack trace, never an absolute filesystem path in the response body.
- **Backend is gated like the rest of `src/`.** `ruff check`, `ruff format --check`, `mypy --strict`, and `pytest` all cover `src/pkmn_quant/api/`. New optional dependency group `api` (mirroring `dashboard`/`viz`); `pyproject.toml` + `uv.lock` committed together (CI runs `uv sync --frozen`).
- **Data root is injectable.** `create_app(root: Path) -> FastAPI`; the module-level `app` reads `PKMN_DATA_ROOT` (default `Path(".")`). Tests construct `create_app(tmp_path)`. Never hardcode `Path(".")` inside a handler.
- **Honesty carries over.** Any Sharpe/DSR/CI figure surfaced in the UI shows the mark-smoothing caveat inline (same rule as the reports/findings).
- **Frontend committed, artifacts not.** `web/` source is committed; `web/node_modules` and `web/dist` are gitignored. The app reads live data via the API, never bundles the warehouse.
- **Shared-fixture contract.** Response shapes are pinned by committed JSON fixtures under `web/tests/fixtures/`; a Python test deserializes each into its Pydantic model (fixture ≡ backend), and the frontend tests render from the same files (fixture ≡ frontend). Changing a shape breaks one side loudly.
- Node ≥ 20 and npm are prerequisites for the frontend tasks (Task 3 verifies). All four Python gates before every backend commit: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`. Frontend gate before every frontend commit: `cd web && npm run check` (defined in Task 3 as `tsc --noEmit && vitest run`).
- Baseline: pytest 388 passed + 1 skipped. No existing test may change. The Streamlit dashboard (`app/dashboard.py`) is untouched.
- Workflow: STOP after each completed task, explain at intern level, wait for explicit green light (CLAUDE.md).
- Branch: `feat/web-explorer` (already created; spec committed).

## File Map

Created (backend):
- `src/pkmn_quant/api/__init__.py` — exports `app`, `create_app`
- `src/pkmn_quant/api/models.py` — Pydantic response models
- `src/pkmn_quant/api/data.py` — data-access (list runs, resolve run_id, load wf/evaluate)
- `src/pkmn_quant/api/app.py` — `create_app`, routes, error handlers
- `tests/api/__init__.py`, `tests/api/conftest.py`, `tests/api/test_runs.py`, `tests/api/test_detail.py`, `tests/api/test_contract.py`

Created (frontend, all under `web/`):
- `web/package.json`, `web/tsconfig.json`, `web/vite.config.ts`, `web/index.html`, `web/.gitignore`
- `web/src/main.tsx`, `web/src/App.tsx`, `web/src/api/client.ts`, `web/src/api/types.ts`
- `web/src/components/Layout.tsx`, `web/src/components/AsyncState.tsx`, `web/src/theme.ts`
- `web/src/pages/RunsBrowser.tsx`, `web/src/pages/WalkForwardDetail.tsx`, `web/src/pages/RigorCompare.tsx`
- `web/tests/fixtures/*.json`, `web/src/**/**.test.tsx`
- `web/src/setupTests.ts`

Modified:
- `pyproject.toml` (+ `uv.lock`) — `api` dep group
- `.gitignore` — `web/node_modules`, `web/dist`
- `.github/workflows/ci.yml` — Node job
- `README.md`, `CLAUDE.md` — Task 7
- `justfile` or `Makefile` (create) — one-command dev target

---

### Task 1: Backend scaffold + `/api/runs` endpoints

**Files:**
- Create: `src/pkmn_quant/api/__init__.py`, `src/pkmn_quant/api/models.py`, `src/pkmn_quant/api/data.py`, `src/pkmn_quant/api/app.py`, `tests/api/__init__.py`, `tests/api/conftest.py`, `tests/api/test_runs.py`
- Modify: `pyproject.toml` (+ `uv.lock`)

**Interfaces (produces; later tasks rely on these):**
- `create_app(root: Path) -> FastAPI`; module `app: FastAPI`.
- `data.list_runs(root, command=None, strategy=None) -> list[RunRecord]`; `data.get_run(root, run_id) -> RunRecord` (raises `KeyError` if unknown).
- Pydantic models `RunSummary`, `RunDetail` (Task 2 adds `WalkForwardResponse`, `EvaluateResponse`, `StrategyInfo`).
- Endpoints `GET /api/runs`, `GET /api/runs/{run_id}`.

- [ ] **Step 1: Add the `api` dependency group**

In `pyproject.toml` `[dependency-groups]`, after `viz`:

```toml
api = ["fastapi>=0.115", "uvicorn>=0.32", "httpx>=0.27"]
```

(`httpx` is FastAPI's `TestClient` dependency.) Run `uv sync --group api` (updates `uv.lock`).

- [ ] **Step 2: Write the failing tests**

`tests/api/__init__.py`: empty file.

`tests/api/conftest.py`:

```python
"""Synthetic data root + app fixture for API tests."""

import json
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest
from fastapi.testclient import TestClient

from pkmn_quant.api import create_app


def _write_registry(root: Path, records: list[dict]) -> None:
    reg = root / "data" / "runs" / "registry.jsonl"
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in records))


def _wf_record(root: Path, run_id: str, strategy: str) -> dict:
    """A walkforward registry record + its artifact dir (json + parquet)."""
    art = root / "data" / "results" / f"wf-{strategy}-2025-01-01-2025-03-01"
    art.mkdir(parents=True, exist_ok=True)
    days = [date(2025, 1, 1) + timedelta(days=i) for i in range(80)]
    equity = [1000.0 * (1.0 + 0.0005 * i) for i in range(80)]
    pl.DataFrame({"date": days, "equity": equity}).write_parquet(art / "stitched_equity.parquet")
    (art / "walkforward.json").write_text(
        json.dumps(
            {
                "strategy": strategy,
                "folds": [
                    {
                        "is_start": "2025-01-01", "is_end": "2025-01-20",
                        "oos_start": "2025-01-21", "oos_end": "2025-01-31",
                        "params": {"top_n": 8},
                        "is_summary": {"total_return": 0.05, "cagr": 0.4},
                        "oos_summary": {"total_return": -0.01, "cagr": -0.1},
                    }
                ],
                "summary": {"stitched_total_return": -0.02, "stitched_cagr": -0.05},
                "rigor": {
                    "stitched_total_return_ci": {
                        "point": -0.02, "lo": -0.09, "hi": 0.04,
                        "level": 0.95, "n_boot": 10000, "mean_block": 10.0, "seed": 42,
                    }
                },
            }
        )
    )
    return {
        "run_id": run_id, "recorded_at": "2026-07-01T00:00:00+00:00",
        "command": "walkforward", "strategy": strategy,
        "git_sha": "abc1234", "git_dirty": False,
        "config_hash": "deadbeef", "config": {"strategy": strategy, "trials": 15},
        "data_fingerprint": {"rows": 1000, "min_date": "2024-01-01", "max_date": "2025-03-01"},
        "results": {"stitched_total_return": -0.02},
        "artifact_path": str(art), "runtime": {"workers": 0, "workers_resolved": 4},
    }


def _evaluate_record(root: Path, run_id: str) -> dict:
    art = root / "data" / "results" / "evaluate-2026-07-01"
    art.mkdir(parents=True, exist_ok=True)
    (art / "evaluate.json").write_text(
        json.dumps(
            {
                "strategies": {
                    "sealed-accumulation": {
                        "total_return": -0.07,
                        "ci": {"point": -0.07, "lo": -0.21, "hi": 0.08, "level": 0.95},
                        "sharpe": -0.8, "dsr": 0.008,
                    },
                    "ml-ranker": {
                        "total_return": -0.075,
                        "ci": {"point": -0.075, "lo": -0.20, "hi": 0.057, "level": 0.95},
                        "sharpe": -0.73, "dsr": 0.007,
                    },
                },
                "reality_check_p": 1.0, "benchmark": "data/results/buy-and-hold-sealed-x",
                "n_days": 660, "start": "2024-08-28", "end": "2026-06-18",
                "params": {"n_boot": 10000, "mean_block": 10.0, "seed": 42},
            }
        )
    )
    return {
        "run_id": run_id, "recorded_at": "2026-07-02T00:00:00+00:00",
        "command": "evaluate", "strategy": "sealed-accumulation,ml-ranker",
        "git_sha": "abc1234", "git_dirty": False,
        "config_hash": "cafef00d", "config": {"n_boot": 10000},
        "data_fingerprint": {"rows": 1000, "min_date": "2024-01-01", "max_date": "2026-06-30"},
        "results": {"reality_check_p": 1.0}, "artifact_path": str(art),
    }


@pytest.fixture
def seeded_root(tmp_path: Path) -> Path:
    _write_registry(
        tmp_path,
        [
            _wf_record(tmp_path, "20260701T000000Z-aaa111", "sealed-accumulation"),
            _evaluate_record(tmp_path, "20260702T000000Z-bbb222"),
        ],
    )
    return tmp_path


@pytest.fixture
def client(seeded_root: Path) -> TestClient:
    return TestClient(create_app(seeded_root))
```

`tests/api/test_runs.py`:

```python
from fastapi.testclient import TestClient


def test_list_runs_newest_first(client: TestClient) -> None:
    resp = client.get("/api/runs")
    assert resp.status_code == 200
    runs = resp.json()
    assert [r["run_id"] for r in runs] == [
        "20260702T000000Z-bbb222",  # evaluate, recorded_at later
        "20260701T000000Z-aaa111",
    ]
    assert runs[0]["command"] == "evaluate"
    assert "artifact_path" not in runs[0]  # no filesystem paths leaked in summaries


def test_filter_runs_by_command(client: TestClient) -> None:
    resp = client.get("/api/runs", params={"command": "walkforward"})
    assert resp.status_code == 200
    runs = resp.json()
    assert len(runs) == 1 and runs[0]["strategy"] == "sealed-accumulation"


def test_run_detail_ok(client: TestClient) -> None:
    resp = client.get("/api/runs/20260701T000000Z-aaa111")
    assert resp.status_code == 200
    body = resp.json()
    assert body["config"]["trials"] == 15
    assert body["runtime"]["workers_resolved"] == 4


def test_run_detail_unknown_is_404(client: TestClient) -> None:
    resp = client.get("/api/runs/does-not-exist")
    assert resp.status_code == 404
    assert "detail" in resp.json()
```

Run: `uv run pytest tests/api/ -v` → FAIL (`ModuleNotFoundError: pkmn_quant.api`).

- [ ] **Step 3: Implement models.py**

```python
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
```

(Note: `RunSummary` deliberately omits `artifact_path`/`config` — the browser list is lean and leaks no paths; `RunDetail` adds config/fingerprint/runtime but still not `artifact_path`.)

- [ ] **Step 4: Implement data.py**

```python
"""Read-only data access for the API: registry index + artifact loading."""

from __future__ import annotations

from pathlib import Path

from pkmn_quant.research.runs import RunRecord, load_runs


def list_runs(
    root: Path, command: str | None = None, strategy: str | None = None
) -> list[RunRecord]:
    """Registry records, newest first, optionally filtered."""
    runs = load_runs(root)
    if command is not None:
        runs = [r for r in runs if r.command == command]
    if strategy is not None:
        runs = [r for r in runs if r.strategy == strategy]
    return sorted(runs, key=lambda r: r.recorded_at, reverse=True)


def get_run(root: Path, run_id: str) -> RunRecord:
    """One record by id; KeyError if unknown."""
    for r in load_runs(root):
        if r.run_id == run_id:
            return r
    raise KeyError(run_id)
```

- [ ] **Step 5: Implement app.py + __init__.py**

`src/pkmn_quant/api/app.py`:

```python
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
                run_id=r.run_id, recorded_at=r.recorded_at, command=r.command,
                strategy=r.strategy, git_sha=r.git_sha, git_dirty=r.git_dirty,
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
            run_id=r.run_id, recorded_at=r.recorded_at, command=r.command,
            strategy=r.strategy, git_sha=r.git_sha, git_dirty=r.git_dirty,
            results=r.results, config_hash=r.config_hash, config=r.config,
            data_fingerprint=r.data_fingerprint, runtime=r.runtime,
        )

    return app


app = create_app(Path(os.environ.get("PKMN_DATA_ROOT", ".")))
```

`src/pkmn_quant/api/__init__.py`:

```python
"""pkmn_quant read-only web API."""

from pkmn_quant.api.app import app, create_app

__all__ = ["app", "create_app"]
```

- [ ] **Step 6: Run tests + gates, commit**

```bash
uv run pytest tests/api/ -v
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy
git add src/pkmn_quant/api/ tests/api/ pyproject.toml uv.lock
git commit -m "feat(api): FastAPI scaffold + /api/runs list & detail endpoints"
```

Expected: baseline + 4 new tests pass; mypy clean (fastapi ships types; add a `fastapi.*`/`pydantic.*` override ONLY if mypy complains — it should not).

---

### Task 2: Backend detail endpoints — walkforward, evaluate, strategies

**Files:**
- Modify: `src/pkmn_quant/api/models.py`, `src/pkmn_quant/api/data.py`, `src/pkmn_quant/api/app.py`
- Create: `tests/api/test_detail.py`

**Interfaces:**
- Consumes: `get_run` (Task 1), `RunRecord.artifact_path`.
- Produces: `data.load_walkforward(root, run_id) -> WalkForwardArtifact`, `data.load_evaluate(root, run_id) -> EvaluateArtifact`, `data.strategy_catalog() -> list[StrategyInfo]`; models `WalkForwardResponse`, `EvaluateResponse`, `StrategyInfo`, plus `EquityPoint`, `FoldRow`, `RigorCI`, `StrategyStat`; endpoints `GET /api/walkforward/{run_id}`, `GET /api/evaluate/{run_id}`, `GET /api/strategies`.

- [ ] **Step 1: Write the failing tests**

`tests/api/test_detail.py`:

```python
from fastapi.testclient import TestClient


def test_walkforward_detail(client: TestClient) -> None:
    resp = client.get("/api/walkforward/20260701T000000Z-aaa111")
    assert resp.status_code == 200
    body = resp.json()
    assert body["strategy"] == "sealed-accumulation"
    assert body["summary"]["stitched_total_return"] == -0.02
    assert len(body["folds"]) == 1
    assert body["folds"][0]["params"]["top_n"] == 8
    assert body["rigor"]["lo"] == -0.09 and body["rigor"]["level"] == 0.95
    assert len(body["equity_curve"]) == 80
    assert body["equity_curve"][0]["equity"] == 1000.0
    assert body["equity_curve"][0]["date"] == "2025-01-01"


def test_walkforward_on_non_wf_run_is_404(client: TestClient) -> None:
    resp = client.get("/api/walkforward/20260702T000000Z-bbb222")  # an evaluate run
    assert resp.status_code == 404


def test_walkforward_unknown_is_404(client: TestClient) -> None:
    assert client.get("/api/walkforward/nope").status_code == 404


def test_evaluate_detail(client: TestClient) -> None:
    resp = client.get("/api/evaluate/20260702T000000Z-bbb222")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reality_check_p"] == 1.0
    assert body["n_days"] == 660
    stats = {s["strategy"]: s for s in body["strategies"]}
    assert stats["ml-ranker"]["dsr"] == 0.007
    assert stats["sealed-accumulation"]["ci"]["hi"] == 0.08


def test_evaluate_on_non_evaluate_run_is_404(client: TestClient) -> None:
    assert client.get("/api/evaluate/20260701T000000Z-aaa111").status_code == 404


def test_strategies_catalog(client: TestClient) -> None:
    resp = client.get("/api/strategies")
    assert resp.status_code == 200
    names = {s["name"] for s in resp.json()}
    assert {"sealed-accumulation", "ml-ranker", "ml-ranker-v2"} <= names
    sealed = next(s for s in resp.json() if s["name"] == "sealed-accumulation")
    assert sealed["thesis"]  # non-empty thesis text
```

Run: `uv run pytest tests/api/test_detail.py -v` → FAIL (404s / missing routes).

- [ ] **Step 2: Add models**

Append to `src/pkmn_quant/api/models.py`:

```python
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
```

- [ ] **Step 3: Add data loaders**

Append to `src/pkmn_quant/api/data.py` (imports at top: `import json`, `import polars as pl`):

```python
def _artifact_dir(root: Path, run_id: str, expect_command: str) -> Path:
    r = get_run(root, run_id)  # raises KeyError -> handler maps to 404
    if r.command != expect_command:
        raise KeyError(f"{run_id} is a {r.command} run, not {expect_command}")
    art = Path(r.artifact_path)
    if not art.exists():
        raise KeyError(f"artifact for {run_id} is missing")
    return art


def load_walkforward(root: Path, run_id: str) -> dict:
    """Raw walkforward.json (carries the rigor block, unlike WalkForwardRun)
    plus the stitched equity curve. Returns a plain dict the handler shapes."""
    art = _artifact_dir(root, run_id, "walkforward")
    raw = json.loads((art / "walkforward.json").read_text())
    curve = pl.read_parquet(art / "stitched_equity.parquet").sort("date")
    raw["equity_curve"] = [
        {"date": d.isoformat(), "equity": float(e)}
        for d, e in zip(curve["date"].to_list(), curve["equity"].to_list(), strict=True)
    ]
    return raw


def load_evaluate(root: Path, run_id: str) -> dict:
    art = _artifact_dir(root, run_id, "evaluate")
    return json.loads((art / "evaluate.json").read_text())


def strategy_catalog() -> list[dict]:
    from pkmn_quant.live.report import THESIS
    from pkmn_quant.research.registry import REGISTRY

    return [{"name": n, "thesis": THESIS.get(n, "")} for n in sorted(REGISTRY)]
```

- [ ] **Step 4: Add the endpoints**

In `create_app` (app.py), import the new models and add:

```python
    @app.get("/api/walkforward/{run_id}", response_model=WalkForwardResponse)
    def get_walkforward(run_id: str) -> WalkForwardResponse:
        try:
            raw = data.load_walkforward(root, run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        rigor = raw.get("rigor", {}).get("stitched_total_return_ci")
        return WalkForwardResponse(
            run_id=run_id, strategy=raw["strategy"], summary=raw["summary"],
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
            run_id=run_id, reality_check_p=raw["reality_check_p"],
            benchmark=raw["benchmark"], n_days=raw["n_days"],
            start=raw["start"], end=raw["end"], params=raw["params"],
            strategies=[
                StrategyStat(
                    strategy=name, total_return=s["total_return"],
                    ci=ConfidenceInterval(**s["ci"]), sharpe=s["sharpe"], dsr=s["dsr"],
                )
                for name, s in sorted(raw["strategies"].items())
            ],
        )

    @app.get("/api/strategies", response_model=list[StrategyInfo])
    def get_strategies() -> list[StrategyInfo]:
        return [StrategyInfo(**s) for s in data.strategy_catalog()]
```

Update the app.py import line to include all new model names.

- [ ] **Step 5: Run tests + gates, commit**

```bash
uv run pytest tests/api/ -v
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy
git add src/pkmn_quant/api/ tests/api/test_detail.py
git commit -m "feat(api): walkforward, evaluate, strategies detail endpoints"
```

---

### Task 3: Frontend scaffold — Vite/React/TS, API client, nav shell

**Files:**
- Create: all `web/` config + `web/src/main.tsx`, `web/src/App.tsx`, `web/src/api/types.ts`, `web/src/api/client.ts`, `web/src/components/Layout.tsx`, `web/src/components/AsyncState.tsx`, `web/src/theme.ts`, `web/src/setupTests.ts`, `web/src/api/client.test.ts`
- Modify: `.gitignore`

**Interfaces:**
- Produces: the TS types mirroring Task 1-2 Pydantic models; `apiClient` with `listRuns`, `getRun`, `getWalkforward`, `getEvaluate`, `getStrategies`; `<Layout>`, `<AsyncState>`; `npm run check` = `tsc --noEmit && vitest run`.

- [ ] **Step 1: Verify Node, scaffold config files**

Run `node --version` (must be ≥ 20; if absent, STOP and report — Node is a prerequisite).

`web/package.json`:

```json
{
  "name": "pkmn-quant-web",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "test": "vitest run",
    "check": "tsc --noEmit && vitest run"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-router-dom": "^6.26.0",
    "recharts": "^2.12.7"
  },
  "devDependencies": {
    "@testing-library/jest-dom": "^6.4.0",
    "@testing-library/react": "^16.0.0",
    "@types/react": "^18.3.0",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.0",
    "jsdom": "^25.0.0",
    "typescript": "^5.5.0",
    "vite": "^5.4.0",
    "vitest": "^2.0.0"
  }
}
```

`web/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "useDefineForClassFields": true,
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true
  },
  "include": ["src", "tests"]
}
```

`web/vite.config.ts`:

```typescript
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://localhost:8000" } },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: "./src/setupTests.ts",
  },
});
```

`web/src/setupTests.ts`: `import "@testing-library/jest-dom";`

`web/index.html`:

```html
<!doctype html>
<html lang="en">
  <head><meta charset="UTF-8" /><title>pkmn_quant explorer</title></head>
  <body><div id="root"></div><script type="module" src="/src/main.tsx"></script></body>
</html>
```

`web/.gitignore` (two lines):

```
node_modules
dist
```

Append to repo-root `.gitignore` two lines: `web/node_modules` and `web/dist`.

Run `cd web && npm install` (creates `web/package-lock.json` — commit it).

- [ ] **Step 2: Write the failing client test**

`web/src/api/client.test.ts`:

```typescript
import { afterEach, describe, expect, it, vi } from "vitest";
import { apiClient } from "./client";

afterEach(() => vi.restoreAllMocks());

describe("apiClient", () => {
  it("lists runs from /api/runs", async () => {
    const fake = [{ run_id: "x", command: "walkforward", strategy: "s", recorded_at: "2026",
      git_sha: "abc", git_dirty: false, results: { stitched_total_return: -0.02 } }];
    vi.stubGlobal("fetch", vi.fn(async () =>
      new Response(JSON.stringify(fake), { status: 200 })));
    const runs = await apiClient.listRuns();
    expect(runs[0].run_id).toBe("x");
  });

  it("passes command filter as a query param", async () => {
    const spy = vi.fn(async () => new Response("[]", { status: 200 }));
    vi.stubGlobal("fetch", spy);
    await apiClient.listRuns({ command: "evaluate" });
    expect(String(spy.mock.calls[0][0])).toContain("command=evaluate");
  });

  it("throws a helpful error on 404", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      new Response(JSON.stringify({ detail: "unknown run_id: z" }), { status: 404 })));
    await expect(apiClient.getRun("z")).rejects.toThrow("unknown run_id: z");
  });
});
```

Run `cd web && npx vitest run src/api/client.test.ts` → FAIL (no client module).

- [ ] **Step 3: Implement types + client**

`web/src/api/types.ts` (mirror the Pydantic models exactly):

```typescript
export interface RunSummary {
  run_id: string;
  recorded_at: string;
  command: string;
  strategy: string;
  git_sha: string | null;
  git_dirty: boolean;
  results: Record<string, number>;
}

export interface RunDetail extends RunSummary {
  config_hash: string;
  config: Record<string, unknown>;
  data_fingerprint: Record<string, unknown>;
  runtime: Record<string, unknown> | null;
}

export interface EquityPoint { date: string; equity: number; }

export interface FoldRow {
  is_start: string; is_end: string; oos_start: string; oos_end: string;
  params: Record<string, unknown>;
  is_summary: Record<string, number>;
  oos_summary: Record<string, number>;
}

export interface RigorCI {
  point: number; lo: number; hi: number; level: number;
  n_boot: number; mean_block: number; seed: number;
}

export interface WalkForwardResponse {
  run_id: string; strategy: string;
  summary: Record<string, number>;
  folds: FoldRow[];
  rigor: RigorCI | null;
  equity_curve: EquityPoint[];
}

export interface ConfidenceInterval { point: number; lo: number; hi: number; level: number; }

export interface StrategyStat {
  strategy: string; total_return: number; ci: ConfidenceInterval;
  sharpe: number; dsr: number | null;
}

export interface EvaluateResponse {
  run_id: string; reality_check_p: number; benchmark: string;
  n_days: number; start: string; end: string;
  params: Record<string, unknown>;
  strategies: StrategyStat[];
}

export interface StrategyInfo { name: string; thesis: string; }
```

`web/src/api/client.ts`:

```typescript
import type {
  EvaluateResponse, RunDetail, RunSummary, StrategyInfo, WalkForwardResponse,
} from "./types";

async function get<T>(path: string): Promise<T> {
  const resp = await fetch(path);
  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`;
    try {
      const body = (await resp.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* non-JSON error body: keep the status line */
    }
    throw new Error(detail);
  }
  return (await resp.json()) as T;
}

export const apiClient = {
  listRuns: (filter?: { command?: string; strategy?: string }): Promise<RunSummary[]> => {
    const q = new URLSearchParams();
    if (filter?.command) q.set("command", filter.command);
    if (filter?.strategy) q.set("strategy", filter.strategy);
    const qs = q.toString();
    return get<RunSummary[]>(`/api/runs${qs ? `?${qs}` : ""}`);
  },
  getRun: (id: string): Promise<RunDetail> => get<RunDetail>(`/api/runs/${id}`),
  getWalkforward: (id: string): Promise<WalkForwardResponse> =>
    get<WalkForwardResponse>(`/api/walkforward/${id}`),
  getEvaluate: (id: string): Promise<EvaluateResponse> =>
    get<EvaluateResponse>(`/api/evaluate/${id}`),
  getStrategies: (): Promise<StrategyInfo[]> => get<StrategyInfo[]>("/api/strategies"),
};
```

- [ ] **Step 4: Layout, AsyncState, theme, App, main**

`web/src/theme.ts` — the dataviz palette slots (light values; the review's dataviz skill governs):

```typescript
export const palette = {
  series: ["#2a78d6", "#008300", "#e87ba4", "#eda100", "#1baf7a", "#eb6834"],
  ink: "#0b0b0b", muted: "#898781", grid: "#e1e0d9", surface: "#fcfcfb",
};
```

`web/src/components/AsyncState.tsx` — a tiny generic loading/error/empty wrapper:

```typescript
import type { ReactNode } from "react";

interface Props<T> {
  loading: boolean;
  error: string | null;
  data: T | null;
  empty?: (d: T) => boolean;
  children: (d: T) => ReactNode;
}

export function AsyncState<T>({ loading, error, data, empty, children }: Props<T>) {
  if (loading) return <p role="status">Loading…</p>;
  if (error) return <p role="alert">Error: {error}</p>;
  if (!data || (empty && empty(data))) return <p>Nothing to show yet.</p>;
  return <>{children(data)}</>;
}
```

`web/src/components/Layout.tsx` — nav shell:

```typescript
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

export function Layout({ children }: { children: ReactNode }) {
  return (
    <div>
      <header>
        <h1>pkmn_quant explorer</h1>
        <nav><Link to="/">Runs</Link></nav>
      </header>
      <main>{children}</main>
    </div>
  );
}
```

`web/src/App.tsx` — routes (pages are stubbed here; Tasks 4-6 fill them):

```typescript
import { Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { RigorCompare } from "./pages/RigorCompare";
import { RunsBrowser } from "./pages/RunsBrowser";
import { WalkForwardDetail } from "./pages/WalkForwardDetail";

export function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<RunsBrowser />} />
        <Route path="/walkforward/:runId" element={<WalkForwardDetail />} />
        <Route path="/evaluate/:runId" element={<RigorCompare />} />
        <Route path="*" element={<p>Not found.</p>} />
      </Routes>
    </Layout>
  );
}
```

`web/src/main.tsx`:

```typescript
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter><App /></BrowserRouter>
  </StrictMode>,
);
```

Create minimal placeholder page files so `tsc`/build pass (Tasks 4-6 replace them):

`web/src/pages/RunsBrowser.tsx`, `WalkForwardDetail.tsx`, `RigorCompare.tsx` each:

```typescript
export function RunsBrowser() { return <p>runs</p>; }   // rename per file
```

- [ ] **Step 5: Run frontend check, commit**

```bash
cd web && npm run check      # tsc --noEmit && vitest run: client tests pass
```

```bash
git add web/ .gitignore
git commit -m "feat(web): Vite/React/TS scaffold, typed API client, nav shell"
```

(Commit `web/package-lock.json`; do NOT commit `web/node_modules`.)

---

### Task 4: Runs browser screen

**Files:**
- Modify: `web/src/pages/RunsBrowser.tsx`
- Create: `web/src/pages/RunsBrowser.test.tsx`, `web/src/hooks/useAsync.ts`, `web/tests/fixtures/runs.json`

**Interfaces:**
- Consumes: `apiClient.listRuns`, `<AsyncState>`.
- Produces: `useAsync<T>(fn) -> {loading, error, data}` hook (reused by Tasks 5-6).

- [ ] **Step 1: Fixture + failing test**

`web/tests/fixtures/runs.json`:

```json
[
  {"run_id": "20260702T000000Z-bbb222", "recorded_at": "2026-07-02T00:00:00+00:00",
   "command": "evaluate", "strategy": "sealed-accumulation,ml-ranker",
   "git_sha": "abc1234", "git_dirty": false, "results": {"reality_check_p": 1.0}},
  {"run_id": "20260701T000000Z-aaa111", "recorded_at": "2026-07-01T00:00:00+00:00",
   "command": "walkforward", "strategy": "sealed-accumulation",
   "git_sha": "abc1234", "git_dirty": false, "results": {"stitched_total_return": -0.02}}
]
```

`web/src/pages/RunsBrowser.test.tsx`:

```typescript
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import runsFixture from "../../tests/fixtures/runs.json";
import { RunsBrowser } from "./RunsBrowser";

afterEach(() => vi.restoreAllMocks());

function mountWith(data: unknown) {
  vi.stubGlobal("fetch", vi.fn(async () =>
    new Response(JSON.stringify(data), { status: 200 })));
  return render(<MemoryRouter><RunsBrowser /></MemoryRouter>);
}

describe("RunsBrowser", () => {
  it("renders a row per run with a link to its detail", async () => {
    mountWith(runsFixture);
    await waitFor(() => expect(screen.getByText("sealed-accumulation")).toBeInTheDocument());
    const wfLink = screen.getByRole("link", { name: /20260701T000000Z-aaa111/ });
    expect(wfLink).toHaveAttribute("href", "/walkforward/20260701T000000Z-aaa111");
    const evalLink = screen.getByRole("link", { name: /20260702T000000Z-bbb222/ });
    expect(evalLink).toHaveAttribute("href", "/evaluate/20260702T000000Z-bbb222");
  });

  it("shows an error state when the fetch fails", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      new Response(JSON.stringify({ detail: "boom" }), { status: 500 })));
    render(<MemoryRouter><RunsBrowser /></MemoryRouter>);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("boom"));
  });
});
```

Run `cd web && npx vitest run src/pages/RunsBrowser.test.tsx` → FAIL.

- [ ] **Step 2: useAsync hook**

`web/src/hooks/useAsync.ts`:

```typescript
import { useEffect, useState } from "react";

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [state, setState] = useState<{ loading: boolean; error: string | null; data: T | null }>(
    { loading: true, error: null, data: null },
  );
  useEffect(() => {
    let alive = true;
    setState({ loading: true, error: null, data: null });
    fn().then(
      (data) => alive && setState({ loading: false, error: null, data }),
      (e: Error) => alive && setState({ loading: false, error: e.message, data: null }),
    );
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}
```

- [ ] **Step 3: Implement RunsBrowser**

`web/src/pages/RunsBrowser.tsx`:

```typescript
import { Link } from "react-router-dom";
import { apiClient } from "../api/client";
import type { RunSummary } from "../api/types";
import { AsyncState } from "../components/AsyncState";
import { useAsync } from "../hooks/useAsync";

const DETAIL_PATH: Record<string, string> = {
  walkforward: "walkforward",
  evaluate: "evaluate",
};

function headline(r: RunSummary): string {
  const [k, v] = Object.entries(r.results)[0] ?? ["", 0];
  return k ? `${k}: ${v}` : "-";
}

export function RunsBrowser() {
  const { loading, error, data } = useAsync(() => apiClient.listRuns());
  return (
    <section>
      <h2>Runs</h2>
      <AsyncState loading={loading} error={error} data={data} empty={(d) => d.length === 0}>
        {(runs) => (
          <table>
            <thead>
              <tr><th>Run</th><th>Command</th><th>Strategy</th><th>Result</th><th>Recorded</th></tr>
            </thead>
            <tbody>
              {runs.map((r) => {
                const seg = DETAIL_PATH[r.command];
                return (
                  <tr key={r.run_id}>
                    <td>
                      {seg
                        ? <Link to={`/${seg}/${r.run_id}`}>{r.run_id}</Link>
                        : r.run_id}
                    </td>
                    <td>{r.command}</td>
                    <td>{r.strategy}</td>
                    <td>{headline(r)}</td>
                    <td>{r.recorded_at.slice(0, 10)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </AsyncState>
    </section>
  );
}
```

- [ ] **Step 4: Run check, commit**

```bash
cd web && npm run check
git add web/src/pages/RunsBrowser.tsx web/src/pages/RunsBrowser.test.tsx web/src/hooks/useAsync.ts web/tests/fixtures/runs.json
git commit -m "feat(web): runs browser table with routing + loading/error states"
```

---

### Task 5: Walk-forward detail screen

**Files:**
- Modify: `web/src/pages/WalkForwardDetail.tsx`
- Create: `web/src/pages/WalkForwardDetail.test.tsx`, `web/src/components/EquityChart.tsx`, `web/tests/fixtures/walkforward.json`

**Interfaces:**
- Consumes: `apiClient.getWalkforward`, `useAsync`, `<AsyncState>`, `palette`, Recharts.
- Produces: `<EquityChart curve rigor>` (reused nowhere else in Plan 1, but isolated for testability).

- [ ] **Step 1: Fixture + failing test**

`web/tests/fixtures/walkforward.json` — a shrunk-but-shaped response (10 equity points, 1 fold, rigor present):

```json
{
  "run_id": "20260701T000000Z-aaa111", "strategy": "sealed-accumulation",
  "summary": {"stitched_total_return": -0.02, "stitched_cagr": -0.05, "overfitting_gap": 0.12},
  "folds": [
    {"is_start": "2025-01-01", "is_end": "2025-01-20", "oos_start": "2025-01-21",
     "oos_end": "2025-01-31", "params": {"top_n": 8},
     "is_summary": {"total_return": 0.05}, "oos_summary": {"total_return": -0.01}}
  ],
  "rigor": {"point": -0.02, "lo": -0.09, "hi": 0.04, "level": 0.95,
            "n_boot": 10000, "mean_block": 10.0, "seed": 42},
  "equity_curve": [
    {"date": "2025-01-01", "equity": 1000.0}, {"date": "2025-01-02", "equity": 1001.0},
    {"date": "2025-01-03", "equity": 1002.5}, {"date": "2025-01-04", "equity": 1001.8}
  ]
}
```

`web/src/pages/WalkForwardDetail.test.tsx`:

```typescript
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import wfFixture from "../../tests/fixtures/walkforward.json";
import { WalkForwardDetail } from "./WalkForwardDetail";

afterEach(() => vi.restoreAllMocks());

function mount() {
  vi.stubGlobal("fetch", vi.fn(async () =>
    new Response(JSON.stringify(wfFixture), { status: 200 })));
  return render(
    <MemoryRouter initialEntries={["/walkforward/20260701T000000Z-aaa111"]}>
      <Routes>
        <Route path="/walkforward/:runId" element={<WalkForwardDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("WalkForwardDetail", () => {
  it("shows the strategy, a fold row, the CI band, and the caveat", async () => {
    mount();
    await waitFor(() => expect(screen.getByText(/sealed-accumulation/)).toBeInTheDocument());
    expect(screen.getByText(/top_n/)).toBeInTheDocument();
    expect(screen.getByText(/95% CI/)).toBeInTheDocument();
    expect(screen.getByText(/-9\.00%/)).toBeInTheDocument();  // rigor.lo formatted
    expect(screen.getByText(/mark smoothing/i)).toBeInTheDocument();  // honesty caveat
  });
});
```

Run → FAIL.

- [ ] **Step 2: EquityChart component**

`web/src/components/EquityChart.tsx`:

```typescript
import { Line, LineChart, ReferenceArea, ResponsiveContainer, Tooltip, XAxis, YAxis }
  from "recharts";
import type { EquityPoint, RigorCI } from "../api/types";
import { palette } from "../theme";

export function EquityChart({ curve }: { curve: EquityPoint[]; rigor: RigorCI | null }) {
  return (
    <ResponsiveContainer width="100%" height={320}>
      <LineChart data={curve} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
        <XAxis dataKey="date" stroke={palette.muted} tick={{ fontSize: 11 }} minTickGap={40} />
        <YAxis stroke={palette.muted} tick={{ fontSize: 11 }} domain={["auto", "auto"]} />
        <Tooltip />
        <ReferenceArea /* placeholder so recharts import of ReferenceArea is used */ />
        <Line type="monotone" dataKey="equity" stroke={palette.series[0]} strokeWidth={2}
          dot={false} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}
```

(The CI band is textual in Plan 1's detail panel, not a chart overlay — the stitched equity curve and the single stitched-return CI are on different scales, so overlaying would violate the one-axis rule. The band is shown as a labeled figure beside the chart. Remove the placeholder `ReferenceArea` if it trips lint; it is only there to avoid an unused import if you keep it — simplest is to not import `ReferenceArea` at all.)

Simplify to no unused imports:

```typescript
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { EquityPoint, RigorCI } from "../api/types";
import { palette } from "../theme";

export function EquityChart({ curve }: { curve: EquityPoint[]; rigor: RigorCI | null }) {
  return (
    <ResponsiveContainer width="100%" height={320}>
      <LineChart data={curve} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
        <XAxis dataKey="date" stroke={palette.muted} tick={{ fontSize: 11 }} minTickGap={40} />
        <YAxis stroke={palette.muted} tick={{ fontSize: 11 }} domain={["auto", "auto"]} />
        <Tooltip />
        <Line type="monotone" dataKey="equity" stroke={palette.series[0]} strokeWidth={2}
          dot={false} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}
```

- [ ] **Step 3: Implement WalkForwardDetail**

`web/src/pages/WalkForwardDetail.tsx`:

```typescript
import { useParams } from "react-router-dom";
import { apiClient } from "../api/client";
import { AsyncState } from "../components/AsyncState";
import { EquityChart } from "../components/EquityChart";
import { useAsync } from "../hooks/useAsync";

const pct = (x: number) => `${(x * 100).toFixed(2)}%`;

export function WalkForwardDetail() {
  const { runId = "" } = useParams();
  const { loading, error, data } = useAsync(() => apiClient.getWalkforward(runId), [runId]);
  return (
    <section>
      <AsyncState loading={loading} error={error} data={data}>
        {(wf) => (
          <>
            <h2>Walk-forward: {wf.strategy}</h2>
            <EquityChart curve={wf.equity_curve} rigor={wf.rigor} />
            {wf.rigor && (
              <p>
                Stitched OOS total return {pct(wf.rigor.point)}, {" "}
                {(wf.rigor.level * 100).toFixed(0)}% CI [{pct(wf.rigor.lo)}, {pct(wf.rigor.hi)}]
                {" "}(block bootstrap, n_boot={wf.rigor.n_boot}, seed {wf.rigor.seed})
              </p>
            )}
            <p><em>Sharpe-derived figures are inflated by mark smoothing; treat bands as
              optimistic.</em></p>
            <h3>Summary</h3>
            <ul>{Object.entries(wf.summary).map(([k, v]) =>
              <li key={k}>{k}: {v.toFixed(4)}</li>)}</ul>
            <h3>Folds</h3>
            <table>
              <thead><tr><th>IS</th><th>OOS</th><th>params</th><th>IS ret</th><th>OOS ret</th>
              </tr></thead>
              <tbody>
                {wf.folds.map((f, i) => (
                  <tr key={i}>
                    <td>{f.is_start} .. {f.is_end}</td>
                    <td>{f.oos_start} .. {f.oos_end}</td>
                    <td>{Object.entries(f.params).map(([k, v]) => `${k}=${v}`).join(", ")}</td>
                    <td>{pct(f.is_summary.total_return ?? 0)}</td>
                    <td>{pct(f.oos_summary.total_return ?? 0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </AsyncState>
    </section>
  );
}
```

- [ ] **Step 4: Run check, commit**

```bash
cd web && npm run check
git add web/src/pages/WalkForwardDetail.tsx web/src/pages/WalkForwardDetail.test.tsx web/src/components/EquityChart.tsx web/tests/fixtures/walkforward.json
git commit -m "feat(web): walk-forward detail — equity chart, CI band, folds, caveat"
```

---

### Task 6: Rigor comparison screen

**Files:**
- Modify: `web/src/pages/RigorCompare.tsx`
- Create: `web/src/pages/RigorCompare.test.tsx`, `web/tests/fixtures/evaluate.json`

**Interfaces:**
- Consumes: `apiClient.getEvaluate`, `useAsync`, `<AsyncState>`.

- [ ] **Step 1: Fixture + failing test**

`web/tests/fixtures/evaluate.json`:

```json
{
  "run_id": "20260702T000000Z-bbb222", "reality_check_p": 1.0,
  "benchmark": "data/results/buy-and-hold-sealed-x", "n_days": 660,
  "start": "2024-08-28", "end": "2026-06-18",
  "params": {"n_boot": 10000, "mean_block": 10.0, "seed": 42},
  "strategies": [
    {"strategy": "ml-ranker", "total_return": -0.075,
     "ci": {"point": -0.075, "lo": -0.20, "hi": 0.057, "level": 0.95},
     "sharpe": -0.73, "dsr": 0.007},
    {"strategy": "sealed-accumulation", "total_return": -0.07,
     "ci": {"point": -0.07, "lo": -0.21, "hi": 0.08, "level": 0.95},
     "sharpe": -0.8, "dsr": 0.008}
  ]
}
```

`web/src/pages/RigorCompare.test.tsx`:

```typescript
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import evalFixture from "../../tests/fixtures/evaluate.json";
import { RigorCompare } from "./RigorCompare";

afterEach(() => vi.restoreAllMocks());

function mount() {
  vi.stubGlobal("fetch", vi.fn(async () =>
    new Response(JSON.stringify(evalFixture), { status: 200 })));
  return render(
    <MemoryRouter initialEntries={["/evaluate/20260702T000000Z-bbb222"]}>
      <Routes><Route path="/evaluate/:runId" element={<RigorCompare />} /></Routes>
    </MemoryRouter>,
  );
}

describe("RigorCompare", () => {
  it("shows the Reality Check headline and a row per strategy", async () => {
    mount();
    await waitFor(() => expect(screen.getByText(/Reality Check/)).toBeInTheDocument());
    expect(screen.getByText(/p = 1\.0000/)).toBeInTheDocument();
    expect(screen.getByText("ml-ranker")).toBeInTheDocument();
    expect(screen.getByText("sealed-accumulation")).toBeInTheDocument();
    expect(screen.getByText(/mark smoothing/i)).toBeInTheDocument();
  });

  it("sorts strategies by deflated Sharpe when that header is clicked", async () => {
    mount();
    await waitFor(() => expect(screen.getByText("ml-ranker")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /deflated sharpe/i }));
    const rows = screen.getAllByTestId("strategy-row");
    // sealed-accumulation dsr 0.008 > ml-ranker 0.007: descending puts sealed first
    expect(rows[0]).toHaveTextContent("sealed-accumulation");
  });
});
```

Run → FAIL.

- [ ] **Step 2: Implement RigorCompare**

`web/src/pages/RigorCompare.tsx`:

```typescript
import { useState } from "react";
import { useParams } from "react-router-dom";
import { apiClient } from "../api/client";
import type { StrategyStat } from "../api/types";
import { AsyncState } from "../components/AsyncState";
import { useAsync } from "../hooks/useAsync";

const pct = (x: number) => `${(x * 100).toFixed(2)}%`;
const dsrText = (d: number | null) => (d === null ? "n/a" : d.toFixed(3));

export function RigorCompare() {
  const { runId = "" } = useParams();
  const { loading, error, data } = useAsync(() => apiClient.getEvaluate(runId), [runId]);
  const [byDsr, setByDsr] = useState(false);
  return (
    <section>
      <AsyncState loading={loading} error={error} data={data}>
        {(ev) => {
          const rows: StrategyStat[] = byDsr
            ? [...ev.strategies].sort((a, b) => (b.dsr ?? -1) - (a.dsr ?? -1))
            : ev.strategies;
          return (
            <>
              <h2>Rigor comparison</h2>
              <p>
                <strong>White&apos;s Reality Check</strong> (best vs benchmark, jointly over{" "}
                {ev.strategies.length} strategies): p = {ev.reality_check_p.toFixed(4)}
              </p>
              <p>{ev.n_days} aligned days ({ev.start} .. {ev.end}), benchmark{" "}
                <code>{ev.benchmark.split("/").pop()}</code>.</p>
              <table>
                <thead>
                  <tr>
                    <th>Strategy</th><th>OOS return</th><th>95% CI</th><th>Sharpe</th>
                    <th><button type="button" onClick={() => setByDsr(true)}>
                      Deflated Sharpe</button></th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((s) => (
                    <tr key={s.strategy} data-testid="strategy-row">
                      <td>{s.strategy}</td>
                      <td>{pct(s.total_return)}</td>
                      <td>[{pct(s.ci.lo)}, {pct(s.ci.hi)}]</td>
                      <td>{s.sharpe.toFixed(2)}</td>
                      <td>{dsrText(s.dsr)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p><em>Sharpe-derived figures (Sharpe, deflated Sharpe, CIs) are inflated by
                mark smoothing; treat them as optimistic.</em></p>
            </>
          );
        }}
      </AsyncState>
    </section>
  );
}
```

- [ ] **Step 3: Run check, commit**

```bash
cd web && npm run check
git add web/src/pages/RigorCompare.tsx web/src/pages/RigorCompare.test.tsx web/tests/fixtures/evaluate.json
git commit -m "feat(web): rigor comparison — Reality Check headline, sortable strategy table"
```

---

### Task 7: Contract test, CI, one-command dev, docs

**Files:**
- Create: `tests/api/test_contract.py`, `justfile`
- Modify: `.github/workflows/ci.yml`, `README.md`, `CLAUDE.md`

**Interfaces:**
- Consumes: everything; the committed `web/tests/fixtures/*.json` are the shared contract.

- [ ] **Step 1: The shared-fixture contract test (Python side)**

`tests/api/test_contract.py` — asserts each committed frontend fixture deserializes into its backend Pydantic model, so the two languages cannot silently drift:

```python
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
```

Run: `uv run pytest tests/api/test_contract.py -v` → PASS (fixtures from Tasks 4-6 already match). If any FAILS, the fixture and the model disagree — fix whichever is wrong (the model is the source of truth for shape).

- [ ] **Step 2: One-command dev (justfile)**

`justfile` at repo root (if the repo has no `just`, a `Makefile` with the same two recipes is the fallback — check `command -v just`):

```make
# Run the API and the web dev server together.
web:
    #!/usr/bin/env bash
    set -euo pipefail
    trap 'kill 0' EXIT
    uv run --group api uvicorn pkmn_quant.api:app --port 8000 &
    (cd web && npm run dev) &
    wait
```

Verify it launches both (Ctrl-C stops both); do not leave it running.

- [ ] **Step 3: CI — add the Node job**

In `.github/workflows/ci.yml`, add a second job (the existing `checks` job already covers the Python API tests since they live under `tests/`):

```yaml
  web:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: web
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: npm
          cache-dependency-path: web/package-lock.json
      - run: npm ci
      - run: npm run check   # tsc --noEmit && vitest run
      - run: npm run build
```

- [ ] **Step 4: Docs**

- `README.md`: a "Web explorer" subsection — what it is (read-only research explorer over the registry/artifacts), the one-command dev (`just web` or the Makefile target), the stack (FastAPI + React/TS), a screenshot placeholder line (`docs/assets/` — capture manually later), and the note that run-triggering is a planned Plan 2.
- `CLAUDE.md`: status bullet (what shipped: 5 endpoints, 3 screens, test counts — the actual pytest count after this branch and the web test count from `npm run check`); Commands: add `just web  # run the API + web dev server`; Layout: new `src/pkmn_quant/api/` bullet (FastAPI read-only explorer API) and a `web/` bullet (React/TS SPA, its own toolchain, Node CI job).

- [ ] **Step 5: Full gates + web check, commit**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy
cd web && npm run check && npm run build && cd ..
git add tests/api/test_contract.py justfile .github/workflows/ci.yml README.md CLAUDE.md
git commit -m "feat: shared-fixture contract test, Node CI job, one-command dev, docs"
```

---

## Self-review notes (already applied)

- Spec coverage: architecture/repo layout → Tasks 1 & 3; backend endpoints (runs, walkforward, evaluate, strategies) → Tasks 1-2; three screens → Tasks 4-6; charting/palette → Task 5 (dataviz palette in theme.ts); typed client + shared-fixture contract → Tasks 3 & 7; error handling (404s, per-screen states) → Tasks 1-2 (backend) + AsyncState in Tasks 3-6; testing (backend TestClient, frontend vitest, contract) → every task; CI Node job + one-command dev → Task 7; honesty caveat → Tasks 5-6 UI. Out-of-scope items (job-runner, operational tabs, hosting, retiring Streamlit) untouched.
- Type consistency: the TS interfaces in `web/src/api/types.ts` (Task 3) mirror the Pydantic models field-for-field (Tasks 1-2); the shared fixtures (Tasks 4-6) are validated against both the Pydantic models (Task 7) and the TS types (via the component tests that consume them). `useAsync` signature identical in Tasks 4-6; `apiClient` method names identical in client (Task 3) and pages (Tasks 4-6).
- Deliberate choices an executor should not "fix": the CI equity chart does NOT overlay the stitched-return CI band (different scale — one-axis rule; the band is a labeled figure beside the chart); `RunSummary` omits `artifact_path`/`config` on purpose (lean list, no path leak); the registry is the only index (loose artifact dirs are intentionally not browsable in Plan 1); hand-written TS types + shared-fixture contract instead of OpenAPI codegen (fewer moving parts, still drift-proof); Node is a hard prerequisite (Task 3 Step 1 stops if absent).
- Known judgment calls resolved at execution time: `just` vs `Makefile` (Task 7 checks `command -v just`); exact package versions may float to latest compatible (lockfile pins them — commit `web/package-lock.json` with the code); if `mypy` flags fastapi/pydantic, add a targeted override (not expected — both ship types).
