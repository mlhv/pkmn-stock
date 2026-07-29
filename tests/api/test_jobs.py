"""JobManager unit tests. An inline executor makes every transition
observable without sleeping — no polling, no flakes."""

import pytest

from pkmn_quant.api.jobs import JobManager
from tests.api.conftest import InlineExecutor


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


def test_request_dict_is_not_aliased_with_stored_state() -> None:
    """The `request` field is a dict — a mutable container. Neither
    mutating the snapshot handed back by submit() nor mutating the caller's
    original dict afterwards may reach the manager's stored copy."""
    mgr = JobManager(InlineExecutor())
    original = {"strategy": "xs-momentum"}
    returned = mgr.submit(original, lambda: ("run-2", None))

    returned.request["strategy"] = "tampered-via-returned"
    original["strategy"] = "tampered-via-caller-dict"

    stored = mgr.get(returned.job_id)
    assert stored is not None
    assert stored.request == {"strategy": "xs-momentum"}

    # get()/list_jobs() must also hand out copies, not aliases of each other.
    stored.request["strategy"] = "tampered-via-get"
    stored_again = mgr.get(returned.job_id)
    assert stored_again is not None
    assert stored_again.request == {"strategy": "xs-momentum"}


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
