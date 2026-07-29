"""GET /api/backtest/{run_id} and its fills page."""

from fastapi.testclient import TestClient

WINDOW = {"start": "2025-01-01", "end": "2025-01-08", "cash": 500.0}
HOLDING = {
    "product_id": 1,
    "sub_type": "Normal",
    "quantity": 2,
    "avg_cost": 9.0,
    "opened_on": "2024-12-01",
}


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
