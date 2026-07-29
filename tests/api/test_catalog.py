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
        json={
            "strategy": "sealed-accumulation",
            "start": "2025-01-01",
            "end": "2025-01-08",
            "cash": 500.0,
        },
    ).json()
    default = live_client.get("/api/runs").json()
    assert [r["run_id"] for r in default] == [job["run_id"]]
    everything = live_client.get("/api/runs", params={"include_benchmarks": True}).json()
    assert {r["run_id"] for r in everything} == {job["run_id"], job["benchmark_run_id"]}
