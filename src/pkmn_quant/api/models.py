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
