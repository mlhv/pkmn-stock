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
