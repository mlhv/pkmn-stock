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
    bad = {
        "product_id": 1,
        "sub_type": "Normal",
        "quantity": 0,
        "avg_cost": 1.0,
        "opened_on": "2024-12-01",
    }
    resp = live_client.post("/api/backtest", json={**BASE, "holdings": [bad]})
    assert resp.status_code == 400 and "quantity" in resp.json()["detail"]


def test_holding_opened_after_window_start_is_400(live_client: TestClient) -> None:
    """A STARTING holding cannot have been opened after the window starts.

    Beyond being incoherent, a future opened_on is silently corrosive: five
    strategies gate on ``(ctx.today - pos.opened_on).days``, which goes
    negative, so the gate never fires and the run trades nothing while still
    reporting success. Needs no market data, so it is a synchronous 400.
    """
    holding = {
        "product_id": 1,
        "sub_type": "Normal",
        "quantity": 1,
        "avg_cost": 1.0,
        "opened_on": "2026-07-29",  # BASE window starts 2025-01-01
    }
    resp = live_client.post("/api/backtest", json={**BASE, "holdings": [holding]})
    assert resp.status_code == 400, resp.text
    assert "opened_on" in resp.json()["detail"]
    assert live_client.get("/api/jobs").json() == []  # nothing was queued


def test_holding_opened_on_the_window_start_is_accepted(live_client: TestClient) -> None:
    """The boundary: opened_on == start is a legitimate starting holding."""
    holding = {
        "product_id": 1,
        "sub_type": "Normal",
        "quantity": 1,
        "avg_cost": 1.0,
        "opened_on": BASE["start"],
    }
    resp = live_client.post("/api/backtest", json={**BASE, "holdings": [holding]})
    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] == "succeeded"


def test_unknown_field_is_422(live_client: TestClient) -> None:
    assert live_client.post("/api/backtest", json={**BASE, "bogus": 1}).status_code == 422


# --- deferred validation: surfaces as a failed job, not a 400 -------------


def test_holding_without_day_one_mark_fails_the_job(live_client: TestClient) -> None:
    holding = {
        "product_id": 2,
        "sub_type": "Normal",
        "quantity": 1,
        "avg_cost": 1.0,
        "opened_on": "2024-12-01",
    }
    resp = live_client.post("/api/backtest", json={**BASE, "holdings": [holding]})
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "failed"
    assert "no price on/before window start" in body["error"]
    assert body["run_id"] is None
