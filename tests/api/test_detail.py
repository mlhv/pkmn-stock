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
