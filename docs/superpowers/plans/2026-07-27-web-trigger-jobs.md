# Web Trigger, Background Jobs, Backtest Detail (Plan 2c) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Trigger a backtest from the web frontend — any registered strategy, its hyperparameters, starting cash, initial card holdings, a window — run it as a background job, and read the result on a detail screen against a buy-and-hold benchmark computed over the identical starting state.

**Architecture:** A single-worker `JobManager` (injected `Executor`) runs Plan 2b's `run_single_backtest` twice per job: the user's strategy, then `buy-and-hold` over the identical window/cash/holdings/impact, tagged `benchmark_for: <primary run_id>` in its registry config. The API gains write capability for the first time (`POST /api/backtest`) plus read endpoints for the detail screen, a product-search endpoint backing holdings autocomplete, and `ParamSpec` exposure so the form generates itself. Three new React routes consume them.

**Tech Stack:** Python 3.12, FastAPI + Pydantic (dependency group `api`), `concurrent.futures`, polars, DuckDB (via `Warehouse.query`), pytest + `fastapi.testclient`; React 18 + TypeScript + react-router-dom 6 + recharts + vitest.

## Global Constraints

- All four Python gates pass before every commit: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`. mypy/pytest need the `api` group — run `uv sync --group api` first if `fastapi`/`pydantic` import errors appear.
- Web gates pass before every commit that touches `web/`: `cd web && npm run check && npm run build`.
- **No engine or C++ changes in this plan.** Nothing under `cpp/`, `engine/backtest.py`, `engine/portfolio.py`, `engine/native.py`. No forced-liquidation or scheduled-sale capability. `tests/test_native_parity.py` stays untouched and green.
- **All backtest execution goes through `run_single_backtest`.** The API adds no engine or capability logic of its own.
- Every new Pydantic response model gets a committed fixture in `web/tests/fixtures/` validated by `tests/api/test_contract.py`. That cross-language contract is not optional.
- `ApiModel` sets `extra="forbid"`; every new model inherits it. Request models too — an unexpected field is a 422, not a silent drop.
- Frozen dataclasses for value objects; mutable state in the smallest scope, guarded by a lock where threads touch it.
- `tests/test_cli_backtest.py` pins exact engine numbers; they must not change.
- Never leak filesystem paths in API responses (`data.NotFound`'s message discipline — see its docstring).

## Reference: existing interfaces this plan builds on

- `run_single_backtest(root, strategy_name, params, cash, holdings, start, end, *, impact=True, warmup_days=0, kind="sealed") -> BacktestRunResult` with fields `run_id: str | None`, `result: Result`, `artifact_dir: Path` (`src/pkmn_quant/research/backtest_run.py`).
- `resolve_params(strategy_name, params) -> Params` raises `ValueError` on unknown strategy, unknown param, out-of-range, non-numeric.
- `SeededHolding(asset: Asset, quantity: int, avg_cost: float, opened_on: date)` (`engine/native.py`); `Asset(product_id: int, sub_type: str)` (`engine/portfolio.py`).
- `RunRecord` fields: `run_id, recorded_at, command, strategy, git_sha, git_dirty, config_hash, config, data_fingerprint, results, artifact_path, runtime`.
- `data.NotFound`, `data.list_runs(root, command=, strategy=)`, `data.get_run(root, run_id)`, `data._artifact_dir(root, run_id, expect_command)`.
- `Warehouse(Paths(root=root)).query(sql) -> pl.DataFrame` with `prices` and `products` DuckDB views. `PRODUCT_SCHEMA`: `product_id, group_id, name, rarity, kind, released_on`. `PRICE_SCHEMA` includes `date, product_id, sub_type, low, mid, high, market`.
- **Fill sign convention: `quantity > 0` is a buy, `< 0` is a sell** (`engine/portfolio.py:19`). A "seeded exit" is a fill with `quantity < 0` on an asset present in the run's seeded holdings.
- Artifacts per backtest run: `data/results/<run_id>/equity.parquet` (columns `date`, `equity`) and `fills.parquet` (columns `day, product_id, sub_type, quantity, price, fees, impact`).
- Frontend: `useAsync(fn, deps)` returns `{loading, error, data}`; `AsyncState` renders loading/error/empty; `apiClient` in `web/src/api/client.ts` wraps `fetch` and throws `Error(detail)`; tests stub `fetch` with `vi.stubGlobal`.

---

### Task 1: Benchmark pairing in the capability layer

**Files:**
- Modify: `src/pkmn_quant/research/backtest_run.py`
- Modify: `tests/research/test_backtest_run.py`

**Interfaces:**
- Consumes: `run_single_backtest`, `record_run`.
- Produces:
  - `run_single_backtest(..., config_extra: dict[str, Any] | None = None)` — merged into the recorded config; existing callers unaffected.
  - `@dataclass(frozen=True) class PairedRunResult: primary: BacktestRunResult; benchmark: BacktestRunResult | None`
  - `run_with_benchmark(root, strategy_name, params, cash, holdings, start, end, *, impact=True, warmup_days=0, kind="sealed") -> PairedRunResult`

- [ ] **Step 1: Write the failing tests**

Append to `tests/research/test_backtest_run.py` (it already has `_seed`, `_run`, `START`, and imports `Warehouse`/`Paths`/`SeededHolding`/`Asset`):

```python
# --- benchmark pairing ----------------------------------------------------


def test_config_extra_merged_into_record(tmp_path: Path) -> None:
    _seed(tmp_path)
    from pkmn_quant.research.backtest_run import run_single_backtest
    from pkmn_quant.research.runs import load_runs

    run_single_backtest(
        root=tmp_path,
        strategy_name="buy-and-hold",
        params={},
        cash=100.0,
        holdings=[],
        start=START,
        end=START + timedelta(days=5),
        config_extra={"benchmark_for": "20250101T000000Z-abcdef"},
    )
    rec = load_runs(tmp_path)[-1]
    assert rec.config["benchmark_for"] == "20250101T000000Z-abcdef"
    assert rec.config["strategy"] == "buy-and-hold"  # untouched keys survive


def test_run_with_benchmark_pairs_two_runs(tmp_path: Path) -> None:
    _seed(tmp_path)
    from pkmn_quant.research.backtest_run import run_with_benchmark
    from pkmn_quant.research.runs import load_runs

    paired = run_with_benchmark(
        root=tmp_path,
        strategy_name="sealed-accumulation",
        params={},
        cash=500.0,
        holdings=[],
        start=START,
        end=START + timedelta(days=5),
    )
    assert paired.benchmark is not None
    assert paired.primary.run_id != paired.benchmark.run_id
    by_id = {r.run_id: r for r in load_runs(tmp_path)}
    bench = by_id[paired.benchmark.run_id]
    assert bench.config["benchmark_for"] == paired.primary.run_id
    assert bench.config["strategy"] == "buy-and-hold"
    # identical starting state: same cash, window, impact, holdings
    primary = by_id[paired.primary.run_id]
    for key in ("cash", "start", "end", "impact", "holdings"):
        assert bench.config[key] == primary.config[key]


def test_run_with_benchmark_passes_holdings_to_both(tmp_path: Path) -> None:
    _seed(tmp_path)
    from pkmn_quant.research.backtest_run import run_with_benchmark

    holding = SeededHolding(
        asset=Asset(product_id=1, sub_type="Normal"),
        quantity=3,
        avg_cost=9.0,
        opened_on=date(2024, 12, 1),
    )
    paired = run_with_benchmark(
        root=tmp_path,
        strategy_name="sealed-accumulation",
        params={},
        cash=100.0,
        holdings=[holding],
        start=START,
        end=START + timedelta(days=5),
    )
    assert paired.benchmark is not None
    # both curves start from cash + 3 * day-0 market (10.0) = 130
    assert paired.primary.result.equity_curve["equity"].to_list()[0] == 130.0
    assert paired.benchmark.result.equity_curve["equity"].to_list()[0] == 130.0


def test_run_with_benchmark_skips_self_pairing(tmp_path: Path) -> None:
    # buy-and-hold-sealed IS the benchmark; pairing it with itself is waste.
    _seed(tmp_path)
    from pkmn_quant.research.backtest_run import run_with_benchmark

    paired = run_with_benchmark(
        root=tmp_path,
        strategy_name="buy-and-hold",
        params={},
        cash=100.0,
        holdings=[],
        start=START,
        end=START + timedelta(days=5),
        kind="sealed",
    )
    assert paired.benchmark is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/research/test_backtest_run.py -k "config_extra or benchmark" -v`
Expected: FAIL — `run_single_backtest() got an unexpected keyword argument 'config_extra'`; `run_with_benchmark` does not exist.

- [ ] **Step 3: Add `config_extra` to `run_single_backtest`**

In `backtest_run.py`, add `from typing import Any` to the imports. Add the keyword-only parameter after `kind`:

```python
    kind: str = "sealed",
    config_extra: dict[str, Any] | None = None,
) -> BacktestRunResult:
```

Build the config dict into a local, merge, then pass it:

```python
    config: dict[str, Any] = {
        "command": "backtest",
        "strategy": strategy_name,
        "params": resolved,
        "holdings": _holdings_config(holdings),
        "cash": cash,
        "kind": kind if strategy_name == "buy-and-hold" else None,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "impact": impact,
        "warmup_days": warmup_days,
        "cost_model": cm.as_dict(),
    }
    if config_extra:
        config.update(config_extra)

    recorded = record_run(
        root=root,
        command="backtest",
        strategy=result.strategy_name,
        config=config,
        results=result.summary,
        artifact_path=run_dir,
        warehouse=wh,
        run_id=run_id,
    )
```

- [ ] **Step 4: Add `PairedRunResult` and `run_with_benchmark`**

Append to `backtest_run.py`:

```python
@dataclass(frozen=True)
class PairedRunResult:
    """A strategy run and the buy-and-hold benchmark computed beside it."""

    primary: BacktestRunResult
    benchmark: BacktestRunResult | None


def run_with_benchmark(
    root: Path,
    strategy_name: str,
    params: dict[str, ParamValue],
    cash: float,
    holdings: list[SeededHolding],
    start: date,
    end: date,
    *,
    impact: bool = True,
    warmup_days: int = 0,
    kind: str = "sealed",
) -> PairedRunResult:
    """Run `strategy_name`, then buy-and-hold-sealed over the IDENTICAL
    starting state (same window, cash, holdings, impact) so the two equity
    curves differ only by trading policy. The benchmark record carries
    ``benchmark_for: <primary run_id>``; that tag is the only linkage.

    Returns benchmark=None when the primary IS buy-and-hold-sealed — pairing
    a run with a copy of itself buys nothing.
    """
    primary = run_single_backtest(
        root=root,
        strategy_name=strategy_name,
        params=params,
        cash=cash,
        holdings=holdings,
        start=start,
        end=end,
        impact=impact,
        warmup_days=warmup_days,
        kind=kind,
    )
    if strategy_name == "buy-and-hold" and kind == "sealed":
        return PairedRunResult(primary=primary, benchmark=None)
    benchmark = run_single_backtest(
        root=root,
        strategy_name="buy-and-hold",
        params={},
        cash=cash,
        holdings=holdings,
        start=start,
        end=end,
        impact=impact,
        warmup_days=warmup_days,
        kind="sealed",
        config_extra={"benchmark_for": primary.run_id},
    )
    return PairedRunResult(primary=primary, benchmark=benchmark)
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/research/test_backtest_run.py -v`
Expected: PASS — all prior cases plus the four new ones.

- [ ] **Step 6: Run the four gates**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/pkmn_quant/research/backtest_run.py tests/research/test_backtest_run.py
git commit -m "feat(research): run_with_benchmark pairs every run with an identical-state buy-and-hold"
```

---

### Task 2: The job layer

**Files:**
- Create: `src/pkmn_quant/api/jobs.py`
- Create: `tests/api/test_jobs.py`

**Interfaces:**
- Consumes: `concurrent.futures.Executor`.
- Produces:
  - `@dataclass class Job` with `job_id, status, request, submitted_at, started_at, finished_at, run_id, benchmark_run_id, error`
  - `JobResult = tuple[str | None, str | None]` — `(run_id, benchmark_run_id)`
  - `class JobManager`: `__init__(executor: Executor, max_history: int = 200)`, `submit(request: dict[str, Any], work: Callable[[], JobResult]) -> Job`, `get(job_id: str) -> Job | None`, `list_jobs() -> list[Job]` (newest first).

- [ ] **Step 1: Write the failing tests**

Create `tests/api/test_jobs.py`:

```python
"""JobManager unit tests. An inline executor makes every transition
observable without sleeping — no polling, no flakes."""

from concurrent.futures import Executor, Future
from typing import Any, Callable, TypeVar

import pytest

from pkmn_quant.api.jobs import JobManager

T = TypeVar("T")


class InlineExecutor(Executor):
    """Runs the callable immediately on submit, in the calling thread."""

    def submit(self, fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> "Future[T]":
        fut: Future[T] = Future()
        try:
            fut.set_result(fn(*args, **kwargs))
        except BaseException as exc:  # noqa: BLE001 - mirrors Executor semantics
            fut.set_exception(exc)
        return fut


def test_successful_job_records_both_run_ids() -> None:
    mgr = JobManager(InlineExecutor())
    job = mgr.submit({"strategy": "buy-and-hold"}, lambda: ("run-1", "bench-1"))
    done = mgr.get(job.job_id)
    assert done is not None
    assert done.status == "succeeded"
    assert done.run_id == "run-1"
    assert done.benchmark_run_id == "bench-1"
    assert done.error is None
    assert done.started_at is not None and done.finished_at is not None


def test_failed_job_captures_the_message() -> None:
    def boom() -> tuple[str | None, str | None]:
        raise ValueError("holding asset Asset(product_id=999) not in backtest universe")

    mgr = JobManager(InlineExecutor())
    job = mgr.submit({"strategy": "dip-buyer"}, boom)
    done = mgr.get(job.job_id)
    assert done is not None
    assert done.status == "failed"
    assert "not in backtest universe" in (done.error or "")
    assert done.run_id is None
    assert done.finished_at is not None


def test_returned_job_is_a_snapshot_not_a_live_handle() -> None:
    """submit() hands back a pre-run snapshot so a POST can answer 202
    without waiting, and mutating it must not corrupt manager state."""
    mgr = JobManager(InlineExecutor())
    returned = mgr.submit({"strategy": "xs-momentum"}, lambda: ("run-2", None))
    assert returned.status == "queued"  # snapshot taken before the work ran
    returned.status = "tampered"
    stored = mgr.get(returned.job_id)
    assert stored is not None and stored.status == "succeeded"


def test_status_is_running_while_work_executes() -> None:
    """The worker flips the job to `running` BEFORE calling the work
    function — that is what makes a `running` row appear in the jobs view
    while a long backtest is in flight."""
    observed: list[str] = []
    mgr = JobManager(InlineExecutor())

    def work() -> tuple[str | None, str | None]:
        observed.extend(j.status for j in mgr.list_jobs())
        return ("run-3", None)

    mgr.submit({"strategy": "dip-buyer"}, work)
    assert observed == ["running"]


def test_list_jobs_is_newest_first() -> None:
    mgr = JobManager(InlineExecutor())
    a = mgr.submit({"n": 1}, lambda: ("a", None))
    b = mgr.submit({"n": 2}, lambda: ("b", None))
    assert [j.job_id for j in mgr.list_jobs()] == [b.job_id, a.job_id]


def test_job_ids_are_unique() -> None:
    mgr = JobManager(InlineExecutor())
    ids = {mgr.submit({}, lambda: (None, None)).job_id for _ in range(50)}
    assert len(ids) == 50


def test_unknown_job_id_is_none() -> None:
    assert JobManager(InlineExecutor()).get("nope") is None


def test_history_is_capped_dropping_oldest() -> None:
    mgr = JobManager(InlineExecutor(), max_history=3)
    jobs = [mgr.submit({"n": i}, lambda: (None, None)) for i in range(5)]
    kept = [j.job_id for j in mgr.list_jobs()]
    assert len(kept) == 3
    assert jobs[0].job_id not in kept and jobs[4].job_id in kept


@pytest.mark.parametrize("bad", ["", None])
def test_request_is_echoed_verbatim(bad: object) -> None:
    mgr = JobManager(InlineExecutor())
    payload = {"strategy": "ml-ranker", "note": bad}
    job = mgr.submit(payload, lambda: (None, None))
    stored = mgr.get(job.job_id)
    assert stored is not None and stored.request == payload
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/api/test_jobs.py -v`
Expected: FAIL — `ModuleNotFoundError: pkmn_quant.api.jobs`.

- [ ] **Step 3: Implement `jobs.py`**

Create `src/pkmn_quant/api/jobs.py`:

```python
"""In-process background jobs for triggered backtests.

A single worker (the app injects `ThreadPoolExecutor(max_workers=1)`) keeps
memory predictable — each run builds its own PreparedMarket — makes `queued`
a real observable state, and keeps tests deterministic. Job state is
in-memory only: a restart loses the job list, never the results, which are
already durable in the run registry.
"""

from __future__ import annotations

import secrets
import threading
from collections import OrderedDict
from concurrent.futures import Executor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable

# (run_id, benchmark_run_id) — either may be None if record_run failed.
JobResult = tuple[str | None, str | None]


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class Job:
    """Mutable by design: the worker thread advances it under the manager's
    lock. Never mutate a Job outside JobManager."""

    job_id: str
    status: str  # queued | running | succeeded | failed
    request: dict[str, Any]
    submitted_at: str
    started_at: str | None = None
    finished_at: str | None = None
    run_id: str | None = None
    benchmark_run_id: str | None = None
    error: str | None = None


@dataclass
class JobManager:
    executor: Executor
    max_history: int = 200
    _jobs: "OrderedDict[str, Job]" = field(default_factory=OrderedDict, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def submit(self, request: dict[str, Any], work: Callable[[], JobResult]) -> Job:
        """Register a queued job and hand it to the executor. The returned
        Job is a snapshot copy — safe to serialize without holding the lock."""
        job = Job(
            job_id=secrets.token_hex(8),
            status="queued",
            request=request,
            submitted_at=_now(),
        )
        with self._lock:
            self._jobs[job.job_id] = job
            while len(self._jobs) > self.max_history:
                self._jobs.popitem(last=False)
            snapshot = Job(**vars(job))
        self.executor.submit(self._run, job.job_id, work)
        return snapshot

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return Job(**vars(job)) if job is not None else None

    def list_jobs(self) -> list[Job]:
        with self._lock:
            return [Job(**vars(j)) for j in reversed(self._jobs.values())]

    def _run(self, job_id: str, work: Callable[[], JobResult]) -> None:
        self._update(job_id, status="running", started_at=_now())
        try:
            run_id, benchmark_run_id = work()
        except Exception as exc:  # any failure becomes a readable job error
            self._update(job_id, status="failed", finished_at=_now(), error=str(exc))
            return
        self._update(
            job_id,
            status="succeeded",
            finished_at=_now(),
            run_id=run_id,
            benchmark_run_id=benchmark_run_id,
        )

    def _update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:  # evicted by max_history while running
                return
            for key, value in fields.items():
                setattr(job, key, value)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/api/test_jobs.py -v`
Expected: PASS — all cases.

- [ ] **Step 5: Run the four gates**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/pkmn_quant/api/jobs.py tests/api/test_jobs.py
git commit -m "feat(api): JobManager — single-worker in-process backtest jobs"
```

---

### Task 3: Trigger endpoints

**Files:**
- Modify: `src/pkmn_quant/api/models.py`
- Modify: `src/pkmn_quant/api/app.py`
- Modify: `tests/api/conftest.py`
- Create: `tests/api/test_trigger.py`

**Interfaces:**
- Consumes: `JobManager` (Task 2), `run_with_benchmark` (Task 1), `resolve_params`, `SeededHolding`, `Asset`.
- Produces:
  - Models `HoldingInput`, `BacktestRequest`, `JobStatus`.
  - `create_app(root: Path, executor: Executor | None = None) -> FastAPI` — default `ThreadPoolExecutor(max_workers=1)`.
  - `POST /api/backtest` → 202 `JobStatus`; `GET /api/jobs` → `list[JobStatus]`; `GET /api/jobs/{job_id}` → `JobStatus` (404 unknown).

- [ ] **Step 1: Add the models**

Append to `src/pkmn_quant/api/models.py`:

```python
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
```

- [ ] **Step 2: Write the failing tests**

Add to `tests/api/conftest.py` (the price/product warehouse the trigger tests run against, plus an inline-executor client):

```python
from concurrent.futures import Executor, Future
from typing import Callable, TypeVar

from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA
from pkmn_quant.data.warehouse import Warehouse

T = TypeVar("T")


class InlineExecutor(Executor):
    """Runs work on submit so a POST completes its job before returning."""

    def submit(self, fn: Callable[..., T], /, *args: object, **kwargs: object) -> "Future[T]":
        fut: Future[T] = Future()
        try:
            fut.set_result(fn(*args, **kwargs))
        except BaseException as exc:  # noqa: BLE001
            fut.set_exception(exc)
        return fut


def seed_warehouse(root: Path, days: int = 8) -> None:
    """A 2-asset warehouse: product 1 prints every day, product 2 only from
    day 3 (so it is in-universe but cannot seed a day-0 holding)."""
    wh = Warehouse(Paths(root=root))
    start = date(2025, 1, 1)
    for i in range(days):
        day = start + timedelta(days=i)
        rows = [
            {
                "date": day,
                "product_id": 1,
                "sub_type": "Normal",
                "low": 9.0 + i,
                "mid": 11.5 + i,
                "high": 30.0 + i,
                "market": 10.0 + i,
            }
        ]
        if i >= 3:
            rows.append(
                {
                    "date": day,
                    "product_id": 2,
                    "sub_type": "Normal",
                    "low": 4.0,
                    "mid": 5.5,
                    "high": 15.0,
                    "market": 5.0,
                }
            )
        wh.write_prices(day, pl.DataFrame(rows, schema=PRICE_SCHEMA))
    wh.write_products(
        pl.DataFrame(
            {
                "product_id": [1, 2],
                "group_id": [1, 1],
                "name": ["Alpha Booster Box", "Beta Elite Trainer Box"],
                "rarity": [None, None],
                "kind": ["sealed", "sealed"],
                "released_on": [date(2024, 11, 1), date(2024, 12, 1)],
            }
        )
    )


@pytest.fixture
def live_root(tmp_path: Path) -> Path:
    """A root with real price data (no pre-seeded registry) for trigger tests."""
    seed_warehouse(tmp_path)
    return tmp_path


@pytest.fixture
def live_client(live_root: Path) -> TestClient:
    return TestClient(create_app(live_root, executor=InlineExecutor()))
```

Create `tests/api/test_trigger.py`:

```python
"""POST /api/backtest + the jobs endpoints. The InlineExecutor means a job
has already finished by the time the POST returns."""

from pathlib import Path

from fastapi.testclient import TestClient

BASE = {"strategy": "buy-and-hold", "start": "2025-01-01", "end": "2025-01-08", "cash": 500.0}


def test_trigger_runs_job_and_records_two_runs(live_client: TestClient, live_root: Path) -> None:
    resp = live_client.post("/api/backtest", json={**BASE, "strategy": "sealed-accumulation"})
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "succeeded"
    assert body["run_id"] and body["benchmark_run_id"]

    from pkmn_quant.research.runs import load_runs

    by_id = {r.run_id: r for r in load_runs(live_root)}
    assert by_id[body["benchmark_run_id"]].config["benchmark_for"] == body["run_id"]


def test_trigger_echoes_request_and_lists_the_job(live_client: TestClient) -> None:
    job_id = live_client.post("/api/backtest", json=BASE).json()["job_id"]
    listed = live_client.get("/api/jobs").json()
    assert [j["job_id"] for j in listed] == [job_id]
    assert listed[0]["request"]["strategy"] == "buy-and-hold"
    one = live_client.get(f"/api/jobs/{job_id}")
    assert one.status_code == 200 and one.json()["job_id"] == job_id


def test_unknown_job_is_404(live_client: TestClient) -> None:
    assert live_client.get("/api/jobs/nope").status_code == 404


def test_buy_and_hold_sealed_has_no_paired_benchmark(live_client: TestClient) -> None:
    body = live_client.post("/api/backtest", json=BASE).json()
    assert body["status"] == "succeeded"
    assert body["benchmark_run_id"] is None


def test_params_reach_the_run(live_client: TestClient, live_root: Path) -> None:
    body = live_client.post(
        "/api/backtest",
        json={**BASE, "strategy": "sealed-accumulation", "params": {"take_profit": 2.0}},
    ).json()
    from pkmn_quant.research.runs import load_runs

    by_id = {r.run_id: r for r in load_runs(live_root)}
    assert by_id[body["run_id"]].config["params"]["take_profit"] == 2.0


# --- synchronous validation: 400 before anything is queued ----------------


def test_unknown_strategy_is_400(live_client: TestClient) -> None:
    resp = live_client.post("/api/backtest", json={**BASE, "strategy": "nope"})
    assert resp.status_code == 400 and "unknown strategy" in resp.json()["detail"]
    assert live_client.get("/api/jobs").json() == []


def test_out_of_range_param_is_400(live_client: TestClient) -> None:
    resp = live_client.post(
        "/api/backtest",
        json={**BASE, "strategy": "sealed-accumulation", "params": {"take_profit": 99.0}},
    )
    assert resp.status_code == 400 and "out of range" in resp.json()["detail"]


def test_inverted_window_is_400(live_client: TestClient) -> None:
    resp = live_client.post("/api/backtest", json={**BASE, "start": "2025-02-01"})
    assert resp.status_code == 400 and "start" in resp.json()["detail"]


def test_unparseable_date_is_400(live_client: TestClient) -> None:
    assert live_client.post("/api/backtest", json={**BASE, "end": "nope"}).status_code == 400


def test_negative_cash_is_400(live_client: TestClient) -> None:
    assert live_client.post("/api/backtest", json={**BASE, "cash": -1.0}).status_code == 400


def test_bad_kind_is_400(live_client: TestClient) -> None:
    assert live_client.post("/api/backtest", json={**BASE, "kind": "foil"}).status_code == 400


def test_bad_holding_row_is_400(live_client: TestClient) -> None:
    bad = {"product_id": 1, "sub_type": "Normal", "quantity": 0, "avg_cost": 1.0,
           "opened_on": "2024-12-01"}
    resp = live_client.post("/api/backtest", json={**BASE, "holdings": [bad]})
    assert resp.status_code == 400 and "quantity" in resp.json()["detail"]


def test_unknown_field_is_422(live_client: TestClient) -> None:
    assert live_client.post("/api/backtest", json={**BASE, "bogus": 1}).status_code == 422


# --- deferred validation: surfaces as a failed job, not a 400 -------------


def test_holding_without_day_one_mark_fails_the_job(live_client: TestClient) -> None:
    holding = {"product_id": 2, "sub_type": "Normal", "quantity": 1, "avg_cost": 1.0,
               "opened_on": "2024-12-01"}
    resp = live_client.post("/api/backtest", json={**BASE, "holdings": [holding]})
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "failed"
    assert "no price on/before window start" in body["error"]
    assert body["run_id"] is None
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/api/test_trigger.py -v`
Expected: FAIL — `create_app()` takes no `executor` argument; `POST /api/backtest` is 405/404.

- [ ] **Step 4: Wire the endpoints**

In `src/pkmn_quant/api/app.py`, extend the imports:

```python
import datetime as dt
from concurrent.futures import Executor, ThreadPoolExecutor

from fastapi import FastAPI, HTTPException, Query, Response

from pkmn_quant.api.jobs import JobManager, JobResult
from pkmn_quant.api.models import BacktestRequest, HoldingInput, JobStatus  # plus existing
```

Change the signature and build the manager:

```python
def create_app(root: Path, executor: Executor | None = None) -> FastAPI:
    app = FastAPI(title="pkmn_quant explorer", version="1")
    jobs = JobManager(executor if executor is not None else ThreadPoolExecutor(max_workers=1))
```

Add two module-level helpers above `create_app`:

```python
def _job_status(job: Job) -> JobStatus:
    return JobStatus(**vars(job))


def _to_seeded(rows: list[HoldingInput]) -> list[SeededHolding]:
    """Row-shape validation only — universe/mark checks need a PreparedMarket
    and therefore happen inside the job (see the plan's validation split)."""
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
        out.append(
            SeededHolding(
                asset=Asset(product_id=r.product_id, sub_type=r.sub_type),
                quantity=r.quantity,
                avg_cost=r.avg_cost,
                opened_on=opened,
            )
        )
    return out
```

with these imports at module scope:

```python
from pkmn_quant.api.jobs import Job, JobManager, JobResult
from pkmn_quant.engine.native import SeededHolding
from pkmn_quant.engine.portfolio import Asset
from pkmn_quant.research.backtest_run import resolve_params, run_with_benchmark
```

Then the three endpoints inside `create_app`:

```python
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
            holdings = _to_seeded(req.holdings)
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
                kind=req.kind,
            )
            bench_id = paired.benchmark.run_id if paired.benchmark else None
            return (paired.primary.run_id, bench_id)

        job = jobs.submit(req.model_dump(), work)
        current = jobs.get(job.job_id) or job
        response.status_code = 202
        return _job_status(current)

    @app.get("/api/jobs", response_model=list[JobStatus])
    def list_jobs() -> list[JobStatus]:
        return [_job_status(j) for j in jobs.list_jobs()]

    @app.get("/api/jobs/{job_id}", response_model=JobStatus)
    def get_job(job_id: str) -> JobStatus:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"unknown job_id: {job_id}")
        return _job_status(job)
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/api/ -v`
Expected: PASS — new trigger tests plus every existing API test (the `client` fixture keeps working because `executor` defaults).

- [ ] **Step 6: Run the four gates**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/pkmn_quant/api/app.py src/pkmn_quant/api/models.py tests/api/conftest.py tests/api/test_trigger.py
git commit -m "feat(api): POST /api/backtest + jobs endpoints (single-worker, injected executor)"
```

---

### Task 4: Backtest detail + fills endpoints

**Files:**
- Modify: `src/pkmn_quant/api/data.py`
- Modify: `src/pkmn_quant/api/models.py`
- Modify: `src/pkmn_quant/api/app.py`
- Create: `tests/api/test_backtest_detail.py`

**Interfaces:**
- Consumes: `data.get_run`, `data.NotFound`, `data.list_runs`.
- Produces:
  - `data.find_benchmark(root, run_id) -> RunRecord | None`
  - `data.load_backtest(root, run_id) -> dict[str, Any]`
  - `data.load_fills(root, run_id, offset: int, limit: int) -> dict[str, Any]`
  - Models `FillRow`, `FillsPage`, `BenchmarkCurve`, `BacktestDetail`
  - `GET /api/backtest/{run_id}`, `GET /api/backtest/{run_id}/fills?offset=&limit=`

- [ ] **Step 1: Add the models**

Append to `src/pkmn_quant/api/models.py`:

```python
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
```

- [ ] **Step 2: Write the failing tests**

Create `tests/api/test_backtest_detail.py`:

```python
"""GET /api/backtest/{run_id} and its fills page."""

from pathlib import Path

from fastapi.testclient import TestClient

WINDOW = {"start": "2025-01-01", "end": "2025-01-08", "cash": 500.0}
HOLDING = {"product_id": 1, "sub_type": "Normal", "quantity": 2, "avg_cost": 9.0,
           "opened_on": "2024-12-01"}


def _trigger(client: TestClient, **overrides: object) -> dict:
    body = {"strategy": "sealed-accumulation", **WINDOW, **overrides}
    resp = client.post("/api/backtest", json=body)
    assert resp.status_code == 202, resp.text
    out = resp.json()
    assert out["status"] == "succeeded", out["error"]
    return out


def test_detail_carries_summary_curve_and_benchmark(live_client: TestClient) -> None:
    job = _trigger(live_client)
    resp = live_client.get(f"/api/backtest/{job['run_id']}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["run_id"] == job["run_id"]
    assert body["config"]["strategy"] == "sealed-accumulation"
    assert body["summary"]["total_return"] is not None
    assert len(body["equity_curve"]) == 8
    assert body["benchmark"]["run_id"] == job["benchmark_run_id"]
    assert len(body["benchmark"]["equity_curve"]) == 8


def test_benchmark_is_null_when_unpaired(live_client: TestClient) -> None:
    job = _trigger(live_client, strategy="buy-and-hold")
    body = live_client.get(f"/api/backtest/{job['run_id']}").json()
    assert body["benchmark"] is None


def test_seeded_exits_and_fill_flags(live_client: TestClient) -> None:
    """A seeded position sold by the strategy is flagged; a plain engine buy
    is not."""
    job = _trigger(live_client, holdings=[HOLDING])
    detail = live_client.get(f"/api/backtest/{job['run_id']}").json()
    fills = live_client.get(f"/api/backtest/{job['run_id']}/fills").json()
    assert fills["n_fills"] == detail["n_fills"]
    seeded_rows = [f for f in fills["fills"] if f["seeded"]]
    # every flagged row is a sale of a seeded asset, and its day is reported
    for row in seeded_rows:
        assert row["quantity"] < 0 and row["product_id"] == 1
        assert row["day"] in detail["seeded_exits"]
    assert len(detail["seeded_exits"]) == len({r["day"] for r in seeded_rows})
    assert all(not f["seeded"] for f in fills["fills"] if f["quantity"] > 0)


def test_fills_paginate(live_client: TestClient) -> None:
    job = _trigger(live_client)
    first = live_client.get(f"/api/backtest/{job['run_id']}/fills", params={"limit": 1}).json()
    assert first["offset"] == 0 and first["limit"] == 1
    assert len(first["fills"]) <= 1
    second = live_client.get(
        f"/api/backtest/{job['run_id']}/fills", params={"offset": 1, "limit": 1}
    ).json()
    assert second["offset"] == 1
    if first["n_fills"] > 1:
        assert second["fills"][0] != first["fills"][0]


def test_detail_of_a_walkforward_run_is_404(client: TestClient) -> None:
    resp = client.get("/api/backtest/20260701T000000Z-aaa111")
    assert resp.status_code == 404
    assert "walkforward" in resp.json()["detail"]


def test_detail_of_unknown_run_is_404(live_client: TestClient) -> None:
    assert live_client.get("/api/backtest/nope").status_code == 404


def test_no_filesystem_paths_leak(live_client: TestClient) -> None:
    job = _trigger(live_client)
    assert "artifact_path" not in live_client.get(f"/api/backtest/{job['run_id']}").json()
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/api/test_backtest_detail.py -v`
Expected: FAIL — 404 on an unrouted path (`/api/backtest/{run_id}` does not exist).

- [ ] **Step 4: Implement the data layer**

Append to `src/pkmn_quant/api/data.py`:

```python
def find_benchmark(root: Path, run_id: str) -> RunRecord | None:
    """The paired buy-and-hold run, located by the `benchmark_for` tag its
    config carries. Returns None for runs predating the pairing (Plan 2c) or
    for a buy-and-hold-sealed run, which is its own benchmark."""
    for r in load_runs(root):
        if r.command == "backtest" and r.config.get("benchmark_for") == run_id:
            return r
    return None


def _equity_points(art: Path) -> list[dict[str, Any]]:
    try:
        curve = pl.read_parquet(art / "equity.parquet").sort("date")
    except FileNotFoundError:
        raise NotFound("equity artifact is missing") from None
    return [
        {"date": d.isoformat(), "equity": float(e)}
        for d, e in zip(curve["date"].to_list(), curve["equity"].to_list(), strict=True)
    ]


def _seeded_assets(config: dict[str, Any]) -> set[tuple[int, str]]:
    return {(h["product_id"], h["sub_type"]) for h in config.get("holdings") or []}


def _fills_frame(art: Path) -> pl.DataFrame:
    try:
        return pl.read_parquet(art / "fills.parquet").sort("day")
    except FileNotFoundError:
        raise NotFound("fills artifact is missing") from None


def load_backtest(root: Path, run_id: str) -> dict[str, Any]:
    """Detail payload: config echo, summary, equity curve, paired benchmark
    curve (or None), fill count, and the days seeded positions were sold."""
    art = _artifact_dir(root, run_id, "backtest")
    record = get_run(root, run_id)
    fills = _fills_frame(art)
    seeded = _seeded_assets(record.config)
    exits = sorted(
        {
            d.isoformat()
            for d, pid, sub, qty in zip(
                fills["day"].to_list(),
                fills["product_id"].to_list(),
                fills["sub_type"].to_list(),
                fills["quantity"].to_list(),
                strict=True,
            )
            if qty < 0 and (pid, sub) in seeded
        }
    )
    bench = find_benchmark(root, run_id)
    benchmark = None
    if bench is not None:
        bench_art = Path(bench.artifact_path)
        if bench_art.exists():
            benchmark = {
                "run_id": bench.run_id,
                "strategy": bench.strategy,
                "equity_curve": _equity_points(bench_art),
            }
    return {
        "run_id": run_id,
        "strategy": record.strategy,
        "config": record.config,
        "summary": record.results,
        "equity_curve": _equity_points(art),
        "benchmark": benchmark,
        "n_fills": fills.height,
        "seeded_exits": exits,
    }


def load_fills(root: Path, run_id: str, offset: int, limit: int) -> dict[str, Any]:
    art = _artifact_dir(root, run_id, "backtest")
    record = get_run(root, run_id)
    seeded = _seeded_assets(record.config)
    fills = _fills_frame(art)
    page = fills.slice(offset, limit)
    rows = [
        {
            "day": r["day"].isoformat(),
            "product_id": r["product_id"],
            "sub_type": r["sub_type"],
            "quantity": r["quantity"],
            "price": r["price"],
            "fees": r["fees"],
            "impact": r["impact"],
            "seeded": r["quantity"] < 0 and (r["product_id"], r["sub_type"]) in seeded,
        }
        for r in page.iter_rows(named=True)
    ]
    return {
        "run_id": run_id,
        "offset": offset,
        "limit": limit,
        "n_fills": fills.height,
        "fills": rows,
    }
```

- [ ] **Step 5: Wire the endpoints**

In `app.py`, import the new models (`BacktestDetail`, `BenchmarkCurve`, `EquityPoint` is already imported, `FillRow`, `FillsPage`) and add inside `create_app`:

```python
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
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/api/ -v`
Expected: PASS.

- [ ] **Step 7: Run the four gates**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add src/pkmn_quant/api/data.py src/pkmn_quant/api/models.py src/pkmn_quant/api/app.py tests/api/test_backtest_detail.py
git commit -m "feat(api): backtest detail + paginated fills with seeded-exit flags"
```

---

### Task 5: Form-support endpoints and contract fixtures

**Files:**
- Modify: `src/pkmn_quant/api/data.py`
- Modify: `src/pkmn_quant/api/models.py`
- Modify: `src/pkmn_quant/api/app.py`
- Create: `tests/api/test_catalog.py`
- Create: `web/tests/fixtures/strategies.json`, `web/tests/fixtures/products.json`, `web/tests/fixtures/backtest-detail.json`, `web/tests/fixtures/fills.json`, `web/tests/fixtures/jobs.json`
- Modify: `tests/api/test_contract.py`

**Interfaces:**
- Consumes: `REGISTRY`, `ParamSpec`, `THESIS`, `Warehouse.query`.
- Produces:
  - `data.product_index(root) -> list[dict[str, Any]]` (process-cached) and `data.search_products(root, q, limit)`
  - `data.strategy_catalog()` now returns `params` per strategy and includes `buy-and-hold`
  - Models `ParamSpecInfo`, `ProductHit`; `StrategyInfo` gains `params: list[ParamSpecInfo]`
  - `GET /api/products?q=&limit=`; `GET /api/runs?include_benchmarks=`

- [ ] **Step 1: Add the models**

In `src/pkmn_quant/api/models.py`, add before `StrategyInfo` and extend it:

```python
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
```

- [ ] **Step 2: Write the failing tests**

Create `tests/api/test_catalog.py`:

```python
"""Strategy catalog (now param-carrying), product search, benchmark filter."""

from fastapi.testclient import TestClient


def test_strategies_carry_paramspecs(client: TestClient) -> None:
    body = client.get("/api/strategies").json()
    by_name = {s["name"]: s for s in body}
    sealed = by_name["sealed-accumulation"]
    assert [p["name"] for p in sealed["params"]] == [
        "min_drawdown",
        "take_profit",
        "min_age_days",
    ]
    take_profit = sealed["params"][1]
    assert take_profit == {
        "name": "take_profit",
        "kind": "float",
        "default": 1.5,
        "low": 1.2,
        "high": 2.5,
        "log": False,
    }
    ml = by_name["ml-ranker"]
    assert [p["log"] for p in ml["params"] if p["name"] == "max_iter"] == [True]


def test_buy_and_hold_is_offered_with_no_params(client: TestClient) -> None:
    by_name = {s["name"]: s for s in client.get("/api/strategies").json()}
    assert by_name["buy-and-hold"]["params"] == []
    assert by_name["buy-and-hold"]["thesis"]


def test_product_search_matches_case_insensitively(live_client: TestClient) -> None:
    hits = live_client.get("/api/products", params={"q": "booster"}).json()
    assert [h["product_id"] for h in hits] == [1]
    assert hits[0]["name"] == "Alpha Booster Box"
    assert hits[0]["sub_types"] == ["Normal"]
    assert hits[0]["last_priced_on"] == "2025-01-08"
    assert hits[0]["kind"] == "sealed"


def test_product_search_respects_limit_and_empty_query(live_client: TestClient) -> None:
    assert len(live_client.get("/api/products", params={"q": "box", "limit": 1}).json()) == 1
    assert live_client.get("/api/products", params={"q": ""}).json() == []
    assert live_client.get("/api/products", params={"q": "zzz"}).json() == []


def test_product_search_reports_stale_last_priced_date(live_client: TestClient) -> None:
    """Product 2 stops being useful as a day-0 holding; the UI needs its last
    priced date to warn, so it must be reported per product."""
    hits = live_client.get("/api/products", params={"q": "elite"}).json()
    assert hits[0]["product_id"] == 2 and hits[0]["last_priced_on"] == "2025-01-08"


def test_runs_hides_paired_benchmarks_by_default(live_client: TestClient) -> None:
    job = live_client.post(
        "/api/backtest",
        json={"strategy": "sealed-accumulation", "start": "2025-01-01",
              "end": "2025-01-08", "cash": 500.0},
    ).json()
    default = live_client.get("/api/runs").json()
    assert [r["run_id"] for r in default] == [job["run_id"]]
    everything = live_client.get("/api/runs", params={"include_benchmarks": True}).json()
    assert {r["run_id"] for r in everything} == {job["run_id"], job["benchmark_run_id"]}
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/api/test_catalog.py -v`
Expected: FAIL — `StrategyInfo` has no `params`; `/api/products` is 404; `include_benchmarks` is ignored.

- [ ] **Step 4: Implement the data layer**

In `src/pkmn_quant/api/data.py`, replace `strategy_catalog` and add product search:

```python
@functools.lru_cache(maxsize=8)
def _cached_product_index(root_key: str) -> tuple[dict[str, Any], ...]:
    """One DuckDB pass over the warehouse, cached per process.

    Search-as-you-type cannot afford a 2.9M-row scan per keystroke, and the
    catalog only changes on ingest — a long-lived server sees a new product
    after a restart. Deliberate tradeoff for a local single-user tool.
    """
    from pkmn_quant.config import Paths
    from pkmn_quant.data.warehouse import Warehouse

    wh = Warehouse(Paths(root=Path(root_key)))
    try:
        frame = wh.query(
            """
            SELECT p.product_id AS product_id,
                   any_value(p.name) AS name,
                   any_value(p.kind) AS kind,
                   list_sort(list(DISTINCT s.sub_type)) AS sub_types,
                   max(s.date) AS last_priced_on
            FROM products p
            JOIN prices s ON s.product_id = p.product_id
            GROUP BY p.product_id
            ORDER BY name
            """
        )
    except FileNotFoundError:
        return ()
    return tuple(
        {
            "product_id": int(r["product_id"]),
            "name": r["name"],
            "kind": r["kind"] or "other",
            "sub_types": list(r["sub_types"]),
            "last_priced_on": r["last_priced_on"].isoformat(),
        }
        for r in frame.iter_rows(named=True)
    )


def product_index(root: Path) -> list[dict[str, Any]]:
    return list(_cached_product_index(str(root.resolve())))


def search_products(root: Path, q: str, limit: int) -> list[dict[str, Any]]:
    """Case-insensitive substring match on name. Filtering happens in Python
    against the cached index, so no user input ever reaches SQL."""
    needle = q.strip().lower()
    if not needle:
        return []
    hits = [p for p in product_index(root) if needle in p["name"].lower()]
    return hits[:limit]


def strategy_catalog() -> list[dict[str, Any]]:
    """Every triggerable strategy with its declarative ParamSpecs — the
    single source the web form generates its inputs from. `buy-and-hold` is
    triggerable but absent from REGISTRY, so it is added explicitly."""
    from pkmn_quant.live.report import THESIS
    from pkmn_quant.research.registry import REGISTRY

    out: list[dict[str, Any]] = [
        {
            "name": "buy-and-hold",
            "thesis": THESIS.get("buy-and-hold", "Buy the universe on day one and hold."),
            "params": [],
        }
    ]
    for name in sorted(REGISTRY):
        out.append(
            {
                "name": name,
                "thesis": THESIS.get(name, ""),
                "params": [
                    {
                        "name": p.name,
                        "kind": p.kind,
                        "default": float(p.default),
                        "low": float(p.low),
                        "high": float(p.high),
                        "log": p.log,
                    }
                    for p in REGISTRY[name].params
                ],
            }
        )
    return out
```

Add `import functools` at the top of `data.py`.

Then extend `list_runs` with the benchmark filter:

```python
def list_runs(
    root: Path,
    command: str | None = None,
    strategy: str | None = None,
    include_benchmarks: bool = True,
) -> list[RunRecord]:
    """Registry records, newest first, optionally filtered."""
    runs = load_runs(root)
    if command is not None:
        runs = [r for r in runs if r.command == command]
    if strategy is not None:
        runs = [r for r in runs if r.strategy == strategy]
    if not include_benchmarks:
        runs = [r for r in runs if not r.config.get("benchmark_for")]
    return sorted(runs, key=lambda r: r.recorded_at, reverse=True)
```

- [ ] **Step 5: Wire the endpoints**

In `app.py`, add `include_benchmarks` to the existing `list_runs` handler (default `False`, so the UI hides paired benchmarks unless asked):

```python
    @app.get("/api/runs", response_model=list[RunSummary])
    def list_runs(
        command: str | None = Query(default=None),
        strategy: str | None = Query(default=None),
        include_benchmarks: bool = Query(default=False),
    ) -> list[RunSummary]:
        return [
            RunSummary(...)  # body unchanged
            for r in data.list_runs(
                root, command=command, strategy=strategy, include_benchmarks=include_benchmarks
            )
        ]
```

and add the products endpoint:

```python
    @app.get("/api/products", response_model=list[ProductHit])
    def search_products(
        q: str = Query(default=""),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> list[ProductHit]:
        return [ProductHit(**p) for p in data.search_products(root, q, limit)]
```

- [ ] **Step 6: Write the contract fixtures**

Capture real responses rather than hand-writing them. Run this once and paste the output into the five fixture files:

```bash
uv run --group api python - <<'PY'
import json, pathlib, tempfile
from datetime import date, timedelta
from fastapi.testclient import TestClient
import sys
sys.path.insert(0, "tests")
from api.conftest import InlineExecutor, seed_warehouse  # noqa: E402
from pkmn_quant.api import create_app

root = pathlib.Path(tempfile.mkdtemp())
seed_warehouse(root)
c = TestClient(create_app(root, executor=InlineExecutor()))
job = c.post("/api/backtest", json={"strategy": "sealed-accumulation",
    "start": "2025-01-01", "end": "2025-01-08", "cash": 500.0,
    "holdings": [{"product_id": 1, "sub_type": "Normal", "quantity": 2,
                  "avg_cost": 9.0, "opened_on": "2024-12-01"}]}).json()
fix = pathlib.Path("web/tests/fixtures")
out = {
    "strategies.json": c.get("/api/strategies").json(),
    "products.json": c.get("/api/products", params={"q": "box"}).json(),
    "jobs.json": c.get("/api/jobs").json(),
    "backtest-detail.json": c.get(f"/api/backtest/{job['run_id']}").json(),
    "fills.json": c.get(f"/api/backtest/{job['run_id']}/fills").json(),
}
for name, body in out.items():
    (fix / name).write_text(json.dumps(body, indent=2) + "\n")
    print("wrote", name)
PY
```

Then add to `tests/api/test_contract.py`:

```python
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
```

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/api/ -v`
Expected: PASS — including the five new contract tests.

- [ ] **Step 8: Run the four gates**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add src/pkmn_quant/api/ tests/api/test_catalog.py tests/api/test_contract.py web/tests/fixtures/
git commit -m "feat(api): ParamSpec-carrying strategy catalog, product search, benchmark filter + fixtures"
```

---

### Task 6: Frontend plumbing and the New Run form

**Files:**
- Modify: `web/src/api/types.ts`, `web/src/api/client.ts`, `web/src/App.tsx`, `web/src/components/Layout.tsx`
- Create: `web/src/pages/NewRun.tsx`, `web/src/components/HoldingsEditor.tsx`
- Create: `web/src/pages/NewRun.test.tsx`

**Interfaces:**
- Consumes: `/api/strategies`, `/api/products`, `POST /api/backtest`.
- Produces:
  - Types `ParamSpecInfo`, `ProductHit`, `HoldingInput`, `BacktestRequest`, `JobStatus`, `FillRow`, `FillsPage`, `BenchmarkCurve`, `BacktestDetail`; `StrategyInfo` gains `params`.
  - `apiClient.searchProducts`, `apiClient.triggerBacktest`, `apiClient.listJobs`, `apiClient.getBacktest`, `apiClient.getFills`.
  - `<NewRun />` at route `/new`; `<HoldingsEditor value onChange />`.

- [ ] **Step 1: Extend the types**

Append to `web/src/api/types.ts` and replace `StrategyInfo`:

```ts
export interface ParamSpecInfo {
  name: string; kind: "int" | "float"; default: number;
  low: number; high: number; log: boolean;
}

export interface StrategyInfo { name: string; thesis: string; params: ParamSpecInfo[]; }

export interface ProductHit {
  product_id: number; name: string; kind: string;
  sub_types: string[]; last_priced_on: string;
}

export interface HoldingInput {
  product_id: number; sub_type: string; quantity: number;
  avg_cost: number; opened_on: string;
}

export interface BacktestRequest {
  strategy: string; start: string; end: string;
  params: Record<string, number>;
  cash: number; impact: boolean; kind: string;
  holdings: HoldingInput[];
}

export interface JobStatus {
  job_id: string; status: string; request: Record<string, unknown>;
  submitted_at: string; started_at: string | null; finished_at: string | null;
  run_id: string | null; benchmark_run_id: string | null; error: string | null;
}

export interface FillRow {
  day: string; product_id: number; sub_type: string; quantity: number;
  price: number; fees: number; impact: number; seeded: boolean;
}

export interface FillsPage {
  run_id: string; offset: number; limit: number; n_fills: number; fills: FillRow[];
}

export interface BenchmarkCurve {
  run_id: string; strategy: string; equity_curve: EquityPoint[];
}

export interface BacktestDetail {
  run_id: string; strategy: string;
  config: Record<string, unknown>;
  summary: Record<string, number>;
  equity_curve: EquityPoint[];
  benchmark: BenchmarkCurve | null;
  n_fills: number;
  seeded_exits: string[];
}
```

- [ ] **Step 2: Extend the client**

In `web/src/api/client.ts`, add a `post` helper mirroring `get`'s error handling, and five methods:

```ts
async function post<T>(path: string, body: unknown): Promise<T> {
  const resp = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`;
    try {
      const parsed = (await resp.json()) as { detail?: unknown };
      if (typeof parsed.detail === "string") detail = parsed.detail;
      else if (parsed.detail) detail = JSON.stringify(parsed.detail);
    } catch {
      /* non-JSON error body: keep the status line */
    }
    throw new Error(detail);
  }
  return (await resp.json()) as T;
}
```

added to `apiClient`:

```ts
  searchProducts: (q: string, limit = 20): Promise<ProductHit[]> =>
    get<ProductHit[]>(`/api/products?q=${encodeURIComponent(q)}&limit=${limit}`),
  triggerBacktest: (req: BacktestRequest): Promise<JobStatus> =>
    post<JobStatus>("/api/backtest", req),
  listJobs: (): Promise<JobStatus[]> => get<JobStatus[]>("/api/jobs"),
  getBacktest: (id: string): Promise<BacktestDetail> =>
    get<BacktestDetail>(`/api/backtest/${id}`),
  getFills: (id: string, offset = 0, limit = 100): Promise<FillsPage> =>
    get<FillsPage>(`/api/backtest/${id}/fills?offset=${offset}&limit=${limit}`),
```

- [ ] **Step 3: Write the failing tests**

Create `web/src/pages/NewRun.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import strategiesFixture from "../../tests/fixtures/strategies.json";
import productsFixture from "../../tests/fixtures/products.json";
import { NewRun } from "./NewRun";

afterEach(() => vi.restoreAllMocks());

function stubRoutes(overrides: Record<string, unknown> = {}) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    if (url.startsWith("/api/strategies"))
      return new Response(JSON.stringify(strategiesFixture), { status: 200 });
    if (url.startsWith("/api/products"))
      return new Response(JSON.stringify(productsFixture), { status: 200 });
    if (url.startsWith("/api/backtest"))
      return new Response(JSON.stringify(overrides.job ?? {
        job_id: "j1", status: "queued", request: {}, submitted_at: "2026-07-27T00:00:00+00:00",
        started_at: null, finished_at: null, run_id: null, benchmark_run_id: null, error: null,
      }), { status: 202 });
    return new Response("{}", { status: 404 });
  }));
  return calls;
}

describe("NewRun", () => {
  it("generates param inputs from the selected strategy's ParamSpecs", async () => {
    stubRoutes();
    render(<MemoryRouter><NewRun /></MemoryRouter>);
    await waitFor(() => expect(screen.getByLabelText("Strategy")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Strategy"), {
      target: { value: "sealed-accumulation" },
    });
    const takeProfit = await screen.findByLabelText("take_profit");
    expect(takeProfit).toHaveAttribute("min", "1.2");
    expect(takeProfit).toHaveAttribute("max", "2.5");
    expect(screen.getByLabelText("min_age_days")).toBeInTheDocument();
  });

  it("swaps the inputs when the strategy changes", async () => {
    stubRoutes();
    render(<MemoryRouter><NewRun /></MemoryRouter>);
    await waitFor(() => expect(screen.getByLabelText("Strategy")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Strategy"), {
      target: { value: "sealed-accumulation" },
    });
    await screen.findByLabelText("take_profit");
    fireEvent.change(screen.getByLabelText("Strategy"), { target: { value: "xs-momentum" } });
    await screen.findByLabelText("lookback_days");
    expect(screen.queryByLabelText("take_profit")).not.toBeInTheDocument();
  });

  it("shows no param inputs for buy-and-hold", async () => {
    stubRoutes();
    render(<MemoryRouter><NewRun /></MemoryRouter>);
    await waitFor(() => expect(screen.getByLabelText("Strategy")).toBeInTheDocument());
    expect(screen.getByText(/no tunable parameters/i)).toBeInTheDocument();
  });

  it("posts the assembled request", async () => {
    const calls = stubRoutes();
    render(<MemoryRouter><NewRun /></MemoryRouter>);
    await waitFor(() => expect(screen.getByLabelText("Strategy")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Start"), { target: { value: "2025-01-01" } });
    fireEvent.change(screen.getByLabelText("End"), { target: { value: "2025-01-08" } });
    fireEvent.change(screen.getByLabelText("Starting cash"), { target: { value: "500" } });
    fireEvent.click(screen.getByRole("button", { name: /run backtest/i }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/backtest")).toBe(true));
    const posted = JSON.parse(
      String(calls.find((c) => c.url === "/api/backtest")?.init?.body),
    );
    expect(posted).toMatchObject({
      strategy: "buy-and-hold", start: "2025-01-01", end: "2025-01-08", cash: 500,
      impact: true, holdings: [],
    });
  });

  it("surfaces a rejected submission without navigating", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.startsWith("/api/strategies"))
        return new Response(JSON.stringify(strategiesFixture), { status: 200 });
      return new Response(JSON.stringify({ detail: "start is after end" }), { status: 400 });
    }));
    render(<MemoryRouter><NewRun /></MemoryRouter>);
    await waitFor(() => expect(screen.getByLabelText("Strategy")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /run backtest/i }));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("start is after end"));
  });

  it("adds a holding row from a product search hit", async () => {
    stubRoutes();
    render(<MemoryRouter><NewRun /></MemoryRouter>);
    await waitFor(() => expect(screen.getByLabelText("Strategy")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Product search"), { target: { value: "box" } });
    const hit = await screen.findByRole("button", { name: /Alpha Booster Box/ });
    fireEvent.click(hit);
    expect(await screen.findByDisplayValue("Alpha Booster Box")).toBeInTheDocument();
    expect(screen.getByLabelText("Sub type")).toHaveValue("Normal");
  });
});
```

- [ ] **Step 4: Run to verify failure**

Run: `cd web && npx vitest run src/pages/NewRun.test.tsx`
Expected: FAIL — cannot resolve `./NewRun`.

- [ ] **Step 5: Build `HoldingsEditor`**

Create `web/src/components/HoldingsEditor.tsx`:

```tsx
import { useEffect, useState } from "react";
import { apiClient } from "../api/client";
import type { HoldingInput, ProductHit } from "../api/types";

interface Row extends HoldingInput { name: string; sub_types: string[]; last_priced_on: string; }

export function HoldingsEditor(
  { value, onChange }: { value: Row[]; onChange: (rows: Row[]) => void },
) {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<ProductHit[]>([]);

  useEffect(() => {
    if (query.trim().length < 2) { setHits([]); return; }
    let alive = true;
    const t = setTimeout(() => {
      apiClient.searchProducts(query).then(
        (h) => alive && setHits(h),
        () => alive && setHits([]),
      );
    }, 200);
    return () => { alive = false; clearTimeout(t); };
  }, [query]);

  const add = (p: ProductHit) => {
    onChange([...value, {
      product_id: p.product_id, sub_type: p.sub_types[0] ?? "Normal",
      quantity: 1, avg_cost: 0, opened_on: new Date().toISOString().slice(0, 10),
      name: p.name, sub_types: p.sub_types, last_priced_on: p.last_priced_on,
    }]);
    setQuery("");
    setHits([]);
  };

  const patch = (i: number, fields: Partial<Row>) =>
    onChange(value.map((r, j) => (j === i ? { ...r, ...fields } : r)));

  return (
    <fieldset>
      <legend>Starting holdings</legend>
      <label htmlFor="product-search">Product search</label>
      <input id="product-search" value={query} onChange={(e) => setQuery(e.target.value)}
        placeholder="Search the card catalog…" />
      {hits.length > 0 && (
        <ul>
          {hits.map((p) => (
            <li key={p.product_id}>
              <button type="button" onClick={() => add(p)}>
                {p.name} <small>({p.kind}, last priced {p.last_priced_on})</small>
              </button>
            </li>
          ))}
        </ul>
      )}
      {value.map((r, i) => (
        <div key={`${r.product_id}-${i}`}>
          <input aria-label="Product" readOnly value={r.name} />
          <label htmlFor={`sub-${i}`}>Sub type</label>
          <select id={`sub-${i}`} aria-label="Sub type" value={r.sub_type}
            onChange={(e) => patch(i, { sub_type: e.target.value })}>
            {r.sub_types.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <input aria-label="Quantity" type="number" min={1} value={r.quantity}
            onChange={(e) => patch(i, { quantity: Number(e.target.value) })} />
          <input aria-label="Avg cost" type="number" min={0} step="0.01" value={r.avg_cost}
            onChange={(e) => patch(i, { avg_cost: Number(e.target.value) })} />
          <input aria-label="Opened on" type="date" value={r.opened_on}
            onChange={(e) => patch(i, { opened_on: e.target.value })} />
          <button type="button" onClick={() => onChange(value.filter((_, j) => j !== i))}>
            Remove
          </button>
        </div>
      ))}
    </fieldset>
  );
}

export type { Row as HoldingRow };
```

- [ ] **Step 6: Build `NewRun`**

Create `web/src/pages/NewRun.tsx`:

```tsx
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { apiClient } from "../api/client";
import type { StrategyInfo } from "../api/types";
import { AsyncState } from "../components/AsyncState";
import { HoldingsEditor, type HoldingRow } from "../components/HoldingsEditor";
import { useAsync } from "../hooks/useAsync";

export function NewRun() {
  const { loading, error, data } = useAsync(() => apiClient.getStrategies());
  const navigate = useNavigate();
  const [strategy, setStrategy] = useState("buy-and-hold");
  const [params, setParams] = useState<Record<string, number>>({});
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [cash, setCash] = useState(10000);
  const [impact, setImpact] = useState(true);
  const [kind, setKind] = useState("sealed");
  const [holdings, setHoldings] = useState<HoldingRow[]>([]);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const submit = async (specs: StrategyInfo[]) => {
    setSubmitError(null);
    const current = specs.find((s) => s.name === strategy);
    const filled = Object.fromEntries(
      (current?.params ?? []).map((p) => [p.name, params[p.name] ?? p.default]),
    );
    try {
      await apiClient.triggerBacktest({
        strategy, start, end, params: filled, cash, impact, kind,
        holdings: holdings.map(({ product_id, sub_type, quantity, avg_cost, opened_on }) =>
          ({ product_id, sub_type, quantity, avg_cost, opened_on })),
      });
      navigate("/jobs");
    } catch (e) {
      setSubmitError((e as Error).message);
    }
  };

  return (
    <section>
      <h2>New backtest</h2>
      <AsyncState loading={loading} error={error} data={data}>
        {(specs) => {
          const current = specs.find((s) => s.name === strategy);
          return (
            <form onSubmit={(e) => { e.preventDefault(); void submit(specs); }}>
              <label htmlFor="strategy">Strategy</label>
              <select id="strategy" value={strategy}
                onChange={(e) => { setStrategy(e.target.value); setParams({}); }}>
                {specs.map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
              </select>
              <p>{current?.thesis}</p>

              <fieldset>
                <legend>Parameters</legend>
                {current && current.params.length === 0
                  ? <p>This strategy has no tunable parameters.</p>
                  : current?.params.map((p) => (
                    <div key={p.name}>
                      <label htmlFor={`p-${p.name}`}>{p.name}</label>
                      <input id={`p-${p.name}`} aria-label={p.name} type="number"
                        min={p.low} max={p.high} step={p.kind === "int" ? 1 : "any"}
                        placeholder={String(p.default)}
                        value={params[p.name] ?? ""}
                        onChange={(e) => setParams({
                          ...params,
                          [p.name]: Number(e.target.value),
                        })} />
                      <small>{p.low} – {p.high} (default {p.default})</small>
                    </div>
                  ))}
              </fieldset>

              <label htmlFor="start">Start</label>
              <input id="start" type="date" value={start}
                onChange={(e) => setStart(e.target.value)} />
              <label htmlFor="end">End</label>
              <input id="end" type="date" value={end}
                onChange={(e) => setEnd(e.target.value)} />
              <label htmlFor="cash">Starting cash</label>
              <input id="cash" type="number" min={0} value={cash}
                onChange={(e) => setCash(Number(e.target.value))} />
              <label htmlFor="impact">
                <input id="impact" type="checkbox" checked={impact}
                  onChange={(e) => setImpact(e.target.checked)} />
                Walk-the-spread market impact
              </label>
              {strategy === "buy-and-hold" && (
                <>
                  <label htmlFor="kind">Universe</label>
                  <select id="kind" value={kind} onChange={(e) => setKind(e.target.value)}>
                    <option value="sealed">sealed</option>
                    <option value="single">single</option>
                  </select>
                </>
              )}

              <HoldingsEditor value={holdings} onChange={setHoldings} />

              {submitError && <p role="alert">{submitError}</p>}
              <button type="submit">Run backtest</button>
            </form>
          );
        }}
      </AsyncState>
    </section>
  );
}
```

- [ ] **Step 7: Register the route and nav link**

In `web/src/App.tsx` add `<Route path="/new" element={<NewRun />} />` with its import. In `web/src/components/Layout.tsx` extend the nav:

```tsx
        <nav>
          <Link to="/">Runs</Link>{" "}
          <Link to="/new">New run</Link>{" "}
          <Link to="/jobs">Jobs</Link>
        </nav>
```

- [ ] **Step 8: Run the web tests**

Run: `cd web && npm run check`
Expected: PASS — tsc clean, all vitest suites green including the six NewRun cases.

- [ ] **Step 9: Build and commit**

Run: `cd web && npm run build`
Expected: PASS.

```bash
git add web/src web/tests
git commit -m "feat(web): New Run form with ParamSpec-driven inputs and holdings autocomplete"
```

---

### Task 7: Jobs view and runs-browser linking

**Files:**
- Create: `web/src/pages/JobsView.tsx`, `web/src/hooks/usePolling.ts`
- Create: `web/src/pages/JobsView.test.tsx`
- Modify: `web/src/App.tsx`, `web/src/pages/RunsBrowser.tsx`, `web/src/pages/RunsBrowser.test.tsx`

**Interfaces:**
- Consumes: `apiClient.listJobs` (Task 6).
- Produces: `usePolling(fn, intervalMs, shouldContinue)` → `{loading, error, data}`; `<JobsView />` at `/jobs`; backtest rows in `RunsBrowser` link to `/backtest/:runId`.

- [ ] **Step 1: Write the failing tests**

Create `web/src/pages/JobsView.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { JobsView } from "./JobsView";

const RUNNING = {
  job_id: "j1", status: "running", request: { strategy: "ml-ranker" },
  submitted_at: "2026-07-27T10:00:00+00:00", started_at: "2026-07-27T10:00:01+00:00",
  finished_at: null, run_id: null, benchmark_run_id: null, error: null,
};
const DONE = { ...RUNNING, status: "succeeded", finished_at: "2026-07-27T10:00:09+00:00",
  run_id: "20260727T100009Z-abc123", benchmark_run_id: "20260727T100010Z-def456" };
const FAILED = { ...RUNNING, status: "failed", finished_at: "2026-07-27T10:00:04+00:00",
  error: "holding asset Asset(product_id=2) has no price on/before window start 2025-01-01" };

beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

function stub(sequence: unknown[][]) {
  let i = 0;
  const fetchMock = vi.fn(async () => {
    const body = sequence[Math.min(i, sequence.length - 1)];
    i += 1;
    return new Response(JSON.stringify(body), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("JobsView", () => {
  it("links a succeeded job to its backtest detail", async () => {
    stub([[DONE]]);
    render(<MemoryRouter><JobsView /></MemoryRouter>);
    const link = await screen.findByRole("link", { name: /20260727T100009Z-abc123/ });
    expect(link).toHaveAttribute("href", "/backtest/20260727T100009Z-abc123");
  });

  it("shows the error text for a failed job", async () => {
    stub([[FAILED]]);
    render(<MemoryRouter><JobsView /></MemoryRouter>);
    expect(await screen.findByText(/no price on\/before window start/)).toBeInTheDocument();
  });

  it("keeps polling while a job is running, then stops when all are idle", async () => {
    const fetchMock = stub([[RUNNING], [RUNNING], [DONE]]);
    render(<MemoryRouter><JobsView /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText("running")).toBeInTheDocument());
    await vi.advanceTimersByTimeAsync(1000);
    await vi.advanceTimersByTimeAsync(1000);
    await waitFor(() => expect(screen.getByText("succeeded")).toBeInTheDocument());
    const afterSettled = fetchMock.mock.calls.length;
    await vi.advanceTimersByTimeAsync(5000);
    expect(fetchMock.mock.calls.length).toBe(afterSettled);
  });

  it("does not poll when the first response is already idle", async () => {
    const fetchMock = stub([[DONE]]);
    render(<MemoryRouter><JobsView /></MemoryRouter>);
    await screen.findByText("succeeded");
    await vi.advanceTimersByTimeAsync(5000);
    expect(fetchMock.mock.calls.length).toBe(1);
  });
});
```

Add to `web/src/pages/RunsBrowser.test.tsx`:

```tsx
  it("links a backtest run to its detail screen", async () => {
    mountWith([{
      run_id: "20260727T100009Z-abc123", recorded_at: "2026-07-27T10:00:09+00:00",
      command: "backtest", strategy: "sealed-accumulation", git_sha: "abc1234",
      git_dirty: false, results: { total_return: -0.02 },
    }]);
    const link = await screen.findByRole("link", { name: /20260727T100009Z-abc123/ });
    expect(link).toHaveAttribute("href", "/backtest/20260727T100009Z-abc123");
  });
```

- [ ] **Step 2: Run to verify failure**

Run: `cd web && npx vitest run src/pages/JobsView.test.tsx src/pages/RunsBrowser.test.tsx`
Expected: FAIL — cannot resolve `./JobsView`; the backtest row renders plain text, not a link.

- [ ] **Step 3: Implement `usePolling`**

Create `web/src/hooks/usePolling.ts`:

```ts
import { useEffect, useRef, useState } from "react";

/** Fetch immediately, then re-fetch every `intervalMs` for as long as
 *  `shouldContinue(data)` holds. Stops on unmount and when work goes idle,
 *  so an idle jobs page makes no requests. */
export function usePolling<T>(
  fn: () => Promise<T>,
  intervalMs: number,
  shouldContinue: (data: T) => boolean,
) {
  const [state, setState] = useState<{ loading: boolean; error: string | null; data: T | null }>(
    { loading: true, error: null, data: null },
  );
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const keepRef = useRef(shouldContinue);
  keepRef.current = shouldContinue;

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = () => {
      fnRef.current().then(
        (data) => {
          if (!alive) return;
          setState({ loading: false, error: null, data });
          if (keepRef.current(data)) timer = setTimeout(tick, intervalMs);
        },
        (e: Error) => alive && setState({ loading: false, error: e.message, data: null }),
      );
    };
    tick();
    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, [intervalMs]);

  return state;
}
```

- [ ] **Step 4: Implement `JobsView`**

Create `web/src/pages/JobsView.tsx`:

```tsx
import { Link } from "react-router-dom";
import { apiClient } from "../api/client";
import type { JobStatus } from "../api/types";
import { AsyncState } from "../components/AsyncState";
import { usePolling } from "../hooks/usePolling";

const ACTIVE = new Set(["queued", "running"]);

export function JobsView() {
  const { loading, error, data } = usePolling(
    () => apiClient.listJobs(),
    1000,
    (jobs: JobStatus[]) => jobs.some((j) => ACTIVE.has(j.status)),
  );
  return (
    <section>
      <h2>Jobs</h2>
      <AsyncState loading={loading} error={error} data={data} empty={(d) => d.length === 0}>
        {(jobs) => (
          <table>
            <thead>
              <tr><th>Job</th><th>Strategy</th><th>Status</th><th>Result</th><th>Submitted</th></tr>
            </thead>
            <tbody>
              {jobs.map((j) => (
                <tr key={j.job_id}>
                  <td>{j.job_id}</td>
                  <td>{String(j.request.strategy ?? "-")}</td>
                  <td>{j.status}</td>
                  <td>
                    {j.run_id
                      ? <Link to={`/backtest/${j.run_id}`}>{j.run_id}</Link>
                      : j.error
                        ? <span role="alert">{j.error}</span>
                        : "-"}
                  </td>
                  <td>{j.submitted_at.slice(0, 19).replace("T", " ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </AsyncState>
    </section>
  );
}
```

- [ ] **Step 5: Link backtest runs and register the route**

In `web/src/pages/RunsBrowser.tsx`, extend the map:

```tsx
const DETAIL_PATH: Record<string, string> = {
  walkforward: "walkforward",
  evaluate: "evaluate",
  backtest: "backtest",
};
```

In `web/src/App.tsx`, add `<Route path="/jobs" element={<JobsView />} />` with its import.

- [ ] **Step 6: Run the web tests**

Run: `cd web && npm run check`
Expected: PASS.

- [ ] **Step 7: Build and commit**

Run: `cd web && npm run build`
Expected: PASS.

```bash
git add web/src
git commit -m "feat(web): jobs view with self-stopping polling; link backtest runs to detail"
```

---

### Task 8: Backtest detail screen

**Files:**
- Create: `web/src/pages/BacktestDetail.tsx`
- Create: `web/src/pages/BacktestDetail.test.tsx`
- Modify: `web/src/components/EquityChart.tsx`, `web/src/App.tsx`

**Interfaces:**
- Consumes: `apiClient.getBacktest`, `apiClient.getFills` (Task 6).
- Produces: `<BacktestDetail />` at `/backtest/:runId`; `EquityChart` accepts `{curve, benchmark?, markers?}`.

- [ ] **Step 1: Write the failing tests**

Create `web/src/pages/BacktestDetail.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import detailFixture from "../../tests/fixtures/backtest-detail.json";
import fillsFixture from "../../tests/fixtures/fills.json";
import { BacktestDetail } from "./BacktestDetail";

afterEach(() => vi.restoreAllMocks());

function mount(detail: unknown = detailFixture, fills: unknown = fillsFixture) {
  const calls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    calls.push(url);
    const body = url.includes("/fills") ? fills : detail;
    return new Response(JSON.stringify(body), { status: 200 });
  }));
  render(
    <MemoryRouter initialEntries={["/backtest/run-1"]}>
      <Routes><Route path="/backtest/:runId" element={<BacktestDetail />} /></Routes>
    </MemoryRouter>,
  );
  return calls;
}

describe("BacktestDetail", () => {
  it("renders the metric tiles from the summary", async () => {
    mount();
    await waitFor(() => expect(screen.getByText(/total_return/)).toBeInTheDocument());
  });

  it("renders a fills table with seeded sales flagged", async () => {
    mount();
    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
    const seeded = (fillsFixture as { fills: { seeded: boolean }[] }).fills
      .filter((f) => f.seeded).length;
    expect(screen.getAllByText("seeded").length).toBe(seeded);
  });

  it("pages the fills table", async () => {
    const calls = mount();
    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /next/i }));
    await waitFor(() =>
      expect(calls.some((u) => u.includes("offset=100"))).toBe(true));
  });

  it("says so when a run has no benchmark", async () => {
    mount({ ...detailFixture, benchmark: null });
    await waitFor(() =>
      expect(screen.getByText(/no benchmark/i)).toBeInTheDocument());
  });

  it("surfaces a load error", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      new Response(JSON.stringify({ detail: "unknown run_id: run-1" }), { status: 404 })));
    render(
      <MemoryRouter initialEntries={["/backtest/run-1"]}>
        <Routes><Route path="/backtest/:runId" element={<BacktestDetail />} /></Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("unknown run_id"));
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd web && npx vitest run src/pages/BacktestDetail.test.tsx`
Expected: FAIL — cannot resolve `./BacktestDetail`.

- [ ] **Step 3: Extend `EquityChart`**

Replace `web/src/components/EquityChart.tsx`:

```tsx
import {
  Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import type { EquityPoint } from "../api/types";
import { palette } from "../theme";

interface Props {
  curve: EquityPoint[];
  benchmark?: EquityPoint[] | null;
  markers?: string[];
}

/** Both series share an x-axis of dates. The runs are computed over the same
 *  window from the same starting state, so the points align 1:1 by date and
 *  no rebasing is needed. */
export function EquityChart({ curve, benchmark, markers = [] }: Props) {
  const byDate = new Map(curve.map((p) => [p.date, { date: p.date, equity: p.equity }]));
  for (const p of benchmark ?? []) {
    const row = byDate.get(p.date) ?? { date: p.date, equity: Number.NaN };
    byDate.set(p.date, { ...row, benchmark: p.equity } as never);
  }
  const merged = [...byDate.values()].sort((a, b) => a.date.localeCompare(b.date));
  return (
    <ResponsiveContainer width="100%" height={320}>
      <LineChart data={merged} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
        <XAxis dataKey="date" stroke={palette.muted} tick={{ fontSize: 11 }} minTickGap={40} />
        <YAxis stroke={palette.muted} tick={{ fontSize: 11 }} domain={["auto", "auto"]} />
        <Tooltip />
        <Line type="monotone" dataKey="equity" name="strategy" stroke={palette.series[0]}
          strokeWidth={2} dot={false} isAnimationActive={false} />
        {benchmark && (
          <Line type="monotone" dataKey="benchmark" name="buy-and-hold"
            stroke={palette.series[1]} strokeWidth={2} strokeDasharray="4 3"
            dot={false} isAnimationActive={false} />
        )}
        {markers.map((d) => (
          <ReferenceLine key={d} x={d} stroke={palette.muted} strokeDasharray="2 2" />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
```

Update the existing call site in `web/src/pages/WalkForwardDetail.tsx` if it passes `rigor` — the prop is gone; pass only `curve`. Check `palette.series` has at least two entries in `web/src/theme.ts`; if not, add a second colour there.

- [ ] **Step 4: Implement the screen**

Create `web/src/pages/BacktestDetail.tsx`:

```tsx
import { useState } from "react";
import { useParams } from "react-router-dom";
import { apiClient } from "../api/client";
import { AsyncState } from "../components/AsyncState";
import { EquityChart } from "../components/EquityChart";
import { useAsync } from "../hooks/useAsync";

const PAGE = 100;

export function BacktestDetail() {
  const { runId = "" } = useParams();
  const detail = useAsync(() => apiClient.getBacktest(runId), [runId]);
  const [offset, setOffset] = useState(0);
  const fills = useAsync(() => apiClient.getFills(runId, offset, PAGE), [runId, offset]);

  return (
    <section>
      <h2>Backtest {runId}</h2>
      <AsyncState loading={detail.loading} error={detail.error} data={detail.data}>
        {(d) => (
          <>
            <dl>
              {Object.entries(d.summary).map(([k, v]) => (
                <div key={k}><dt>{k}</dt><dd>{v.toFixed(4)}</dd></div>
              ))}
            </dl>
            <EquityChart curve={d.equity_curve} benchmark={d.benchmark?.equity_curve ?? null}
              markers={d.seeded_exits} />
            {d.benchmark
              ? <p>Benchmark: {d.benchmark.strategy} ({d.benchmark.run_id})</p>
              : <p>No benchmark paired with this run.</p>}
            <h3>Configuration</h3>
            <pre>{JSON.stringify(d.config, null, 2)}</pre>
            <h3>Fills ({d.n_fills})</h3>
            <AsyncState loading={fills.loading} error={fills.error} data={fills.data}
              empty={(f) => f.n_fills === 0}>
              {(page) => (
                <>
                  <table>
                    <thead>
                      <tr>
                        <th>Day</th><th>Product</th><th>Sub type</th><th>Qty</th>
                        <th>Price</th><th>Fees</th><th>Impact</th><th></th>
                      </tr>
                    </thead>
                    <tbody>
                      {page.fills.map((f, i) => (
                        <tr key={`${f.day}-${f.product_id}-${i}`}>
                          <td>{f.day}</td><td>{f.product_id}</td><td>{f.sub_type}</td>
                          <td>{f.quantity}</td><td>{f.price.toFixed(2)}</td>
                          <td>{f.fees.toFixed(2)}</td><td>{f.impact.toFixed(2)}</td>
                          <td>{f.seeded ? "seeded" : ""}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <button type="button" disabled={offset === 0}
                    onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</button>
                  <button type="button" disabled={offset + PAGE >= page.n_fills}
                    onClick={() => setOffset(offset + PAGE)}>Next</button>
                </>
              )}
            </AsyncState>
          </>
        )}
      </AsyncState>
    </section>
  );
}
```

Register `<Route path="/backtest/:runId" element={<BacktestDetail />} />` in `web/src/App.tsx`.

- [ ] **Step 5: Run the web tests**

Run: `cd web && npm run check`
Expected: PASS — every suite, including `WalkForwardDetail.test.tsx` after the `EquityChart` prop change.

- [ ] **Step 6: Build and commit**

Run: `cd web && npm run build`
Expected: PASS.

```bash
git add web/src
git commit -m "feat(web): backtest detail screen with benchmark overlay and paginated fills"
```

---

### Task 9: Dashboard cleanup, docs, and full verification

**Files:**
- Modify: `app/dashboard.py`
- Modify: `tests/test_dashboard.py`
- Modify: `CLAUDE.md`, `README.md`

**Interfaces:**
- Consumes: everything from Tasks 1-8; `research.runs.load_runs`.
- Produces: `benchmark_runs()` driven by the run registry; docs describing the trigger flow.

- [ ] **Step 1: Fix `benchmark_runs()`**

The current implementation returns any non-`wf-` directory containing `equity.parquet`, so every triggered run and every paired benchmark appears in the overlay selector under an opaque run id. Drive it off the registry instead. In `app/dashboard.py`:

```python
def benchmark_runs() -> list[Path]:
    """Buy-and-hold benchmark artifacts, newest first.

    Registry-driven rather than directory-scanning: since Plan 2b every
    backtest mints its own `data/results/<run_id>/` directory, and since
    Plan 2c every triggered run also writes a paired benchmark, so a raw
    directory scan would fill this selector with opaque ids.
    """
    from pkmn_quant.research.runs import load_runs

    out: list[Path] = []
    for r in sorted(load_runs(ROOT), key=lambda r: r.recorded_at, reverse=True):
        if r.command != "backtest" or not r.strategy.startswith("buy-and-hold"):
            continue
        art = Path(r.artifact_path)
        if (art / "equity.parquet").exists():
            out.append(art)
    # legacy pre-Plan-2b directories, still on disk and still readable
    if RESULTS.exists():
        out.extend(
            p for p in sorted(RESULTS.iterdir())
            if p.is_dir()
            and p.name.startswith("buy-and-hold")
            and (p / "equity.parquet").exists()
            and p not in out
        )
    return out
```

- [ ] **Step 2: Add the dashboard test**

Add to `tests/test_dashboard.py`:

```python
def test_benchmark_runs_excludes_non_benchmark_backtests(tmp_path, monkeypatch) -> None:
    """A triggered sealed-accumulation run must not appear in the benchmark
    overlay selector just because it has an equity.parquet."""
    import app.dashboard as dash

    results = tmp_path / "data" / "results"
    for name in ("20260727T100000Z-aaa111", "20260727T100001Z-bbb222"):
        (results / name).mkdir(parents=True)
        (results / name / "equity.parquet").write_bytes(b"")
    reg = tmp_path / "data" / "runs" / "registry.jsonl"
    reg.parent.mkdir(parents=True)
    rows = [
        {"run_id": "20260727T100000Z-aaa111", "recorded_at": "2026-07-27T10:00:00+00:00",
         "command": "backtest", "strategy": "sealed-accumulation", "git_sha": None,
         "git_dirty": False, "config_hash": "x", "config": {}, "data_fingerprint": {},
         "results": {}, "artifact_path": str(results / "20260727T100000Z-aaa111")},
        {"run_id": "20260727T100001Z-bbb222", "recorded_at": "2026-07-27T10:00:01+00:00",
         "command": "backtest", "strategy": "buy-and-hold-sealed", "git_sha": None,
         "git_dirty": False, "config_hash": "y", "config": {}, "data_fingerprint": {},
         "results": {}, "artifact_path": str(results / "20260727T100001Z-bbb222")},
    ]
    reg.write_text("".join(json.dumps(r) + "\n" for r in rows))
    monkeypatch.setattr(dash, "ROOT", tmp_path)
    monkeypatch.setattr(dash, "RESULTS", results)

    names = [p.name for p in dash.benchmark_runs()]
    assert names == ["20260727T100001Z-bbb222"]
```

(Add `import json` to that test module if it is not already imported.)

- [ ] **Step 3: Run the dashboard tests**

Run: `uv run --group dashboard pytest tests/test_dashboard.py -v`
Expected: PASS — the three existing tests plus the new one.

- [ ] **Step 4: Update the docs**

In `CLAUDE.md`:
- Add a `## Status` bullet for Plan 2c matching the voice and density of the existing bullets: what shipped (job layer, five new endpoints, three screens, benchmark pairing, dashboard fix), the test counts from your own verification run, and the honest caveats — in-memory job state lost on restart, the product index cached per process so a new ingest needs a server restart, and holdings universe/mark validation surfacing as a failed job rather than a 400.
- In the `src/pkmn_quant/api/` layout bullet, note that the API is no longer read-only: `POST /api/backtest` plus the jobs endpoints, `jobs.py`'s single-worker `JobManager` with an injected executor, and that every triggered run is paired with an identical-state buy-and-hold benchmark linked by `config["benchmark_for"]`.
- In the `web/` layout bullet, replace "3 screens" with the six screens and name the new routes.
- In the `research/` bullet, note `run_with_benchmark` beside `run_single_backtest`.

In `README.md`, add the trigger flow to whatever section describes the web explorer, and check no line still calls the API read-only.

- [ ] **Step 5: Full verification sweep**

Run each and report real output:

```bash
uv sync --group api && uv run pytest
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run --group dashboard pytest tests/test_dashboard.py
cd web && npm run check && npm run build
```

Expected: all PASS.

- [ ] **Step 6: End-to-end check against the real warehouse**

The synthetic fixtures cannot catch a 4,687-product search or a real multi-year window. Start the API against the real data root and drive the actual flow:

```bash
uv run --group api uvicorn pkmn_quant.api:app --port 8000 &
sleep 3
curl -s "http://localhost:8000/api/products?q=surging" | head -c 400; echo
curl -s -X POST http://localhost:8000/api/backtest -H 'Content-Type: application/json' \
  -d '{"strategy":"sealed-accumulation","start":"2026-05-01","end":"2026-06-30","cash":5000}'
echo
sleep 20
curl -s http://localhost:8000/api/jobs | head -c 600; echo
kill %1
```

Expected: the product search returns real hits with `sub_types` and `last_priced_on`; the POST returns 202 with a job id; the job reaches `succeeded` with both `run_id` and `benchmark_run_id`; `GET /api/backtest/<run_id>` returns a detail payload whose `benchmark.equity_curve` has the same length as `equity_curve`. Report the actual numbers. **If the product search takes more than a few seconds, report it** — that is the cache-warming cost on real data and it belongs in the docs rather than being hidden.

- [ ] **Step 7: Commit**

```bash
git add app/dashboard.py tests/test_dashboard.py CLAUDE.md README.md
git commit -m "fix(dashboard): registry-driven benchmark discovery; docs for the web trigger (Plan 2c)"
```

---

## Self-review notes

- **Spec coverage:** job layer with injected executor + single worker (Task 2) ✓; two runs per job with identical starting state and `benchmark_for` linkage (Task 1) ✓; split validation, synchronous 400s vs deferred job failures (Task 3) ✓; restart semantics documented, in-memory only (Task 2) ✓; seeded-exit visibility with no engine change (Task 4) ✓; all three job endpoints (Task 3), both detail endpoints (Task 4), products search + widened strategies including `buy-and-hold` + `include_benchmarks` (Task 5) ✓; contract fixtures for every new model (Task 5) ✓; three new routes, self-generating form, self-stopping polling, benchmark overlay + markers, fills paging, runs-browser linking, nav (Tasks 6-8) ✓; dashboard `benchmark_runs()` cleanup (Task 9) ✓; docs + verification (Task 9) ✓. Out-of-scope items (cancellation, SSE, deletion, auth, walk-forward triggering, forced liquidation) appear in no task ✓.
- **Type consistency:** `JobResult = tuple[str | None, str | None]` is returned by Task 3's `work()` and consumed by Task 2's `JobManager._run`. `Job`'s field names match `JobStatus`'s one-for-one, which is what makes `JobStatus(**vars(job))` valid. `run_with_benchmark`'s signature in Task 1 matches its Task 3 call site exactly. `data.load_backtest`/`load_fills` return the dict keys Task 4's handlers splat into `BacktestDetail`/`FillsPage`. TypeScript `ParamSpecInfo`/`ProductHit`/`JobStatus`/`FillRow`/`BacktestDetail` mirror the Pydantic models field-for-field, which `tests/api/test_contract.py` enforces via the fixtures.
- **Fill sign convention** is stated once in the reference section and used consistently: a seeded exit is `quantity < 0` on a seeded asset (Task 4 data layer, Task 4 tests, Task 8 rendering).
- **Known deliberate tradeoffs, each documented in code where it bites:** the product index is process-cached (new ingest needs a restart); job state is in-memory (restart loses the list, not results); holdings universe/mark checks are deferred to the job.
- **`EquityChart`'s prop change** (dropping the unused `rigor`, adding `benchmark`/`markers`) touches `WalkForwardDetail.tsx`; Task 8 Step 3 names that call site so tsc does not fail in isolation.
