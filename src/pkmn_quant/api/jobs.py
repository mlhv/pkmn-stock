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
from collections.abc import Callable
from concurrent.futures import Executor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

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
    _jobs: OrderedDict[str, Job] = field(default_factory=OrderedDict, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def submit(self, request: dict[str, Any], work: Callable[[], JobResult]) -> Job:
        """Register a queued job and hand it to the executor. The returned
        Job is a snapshot copy — safe to serialize without holding the lock."""
        job = Job(
            job_id=secrets.token_hex(8),
            status="queued",
            request=dict(request),  # defensive copy: don't alias the caller's dict
            submitted_at=_now(),
        )
        with self._lock:
            self._jobs[job.job_id] = job
            while len(self._jobs) > self.max_history:
                self._jobs.popitem(last=False)
            snapshot = self._snapshot(job)
        self.executor.submit(self._run, job.job_id, work)
        return snapshot

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return self._snapshot(job) if job is not None else None

    def list_jobs(self) -> list[Job]:
        with self._lock:
            return [self._snapshot(j) for j in reversed(self._jobs.values())]

    @staticmethod
    def _snapshot(job: Job) -> Job:
        """A copy safe to hand outside the lock: a shallow `dict(...)` on
        `request` is enough since it holds only JSON-scalar / small values
        (typically a Pydantic `model_dump()`), not nested mutable structures
        that would need `copy.deepcopy`."""
        return Job(**{**vars(job), "request": dict(job.request)})

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
