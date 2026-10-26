"""Final-review Important 2: every API response that carries results names
its execution mode, and a missing key (every pre-Plan-B record and artifact)
reads as formula."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from pkmn_quant.api import create_app

WF_ID = "20260701T000000Z-aaa111"
EVAL_ID = "20260702T000000Z-bbb222"


def test_legacy_records_and_artifacts_read_as_formula(client: TestClient) -> None:
    runs = client.get("/api/runs").json()
    assert {r["execution"] for r in runs} == {"formula"}
    assert client.get(f"/api/runs/{WF_ID}").json()["execution"] == "formula"
    assert client.get(f"/api/walkforward/{WF_ID}").json()["execution"] == "formula"
    assert client.get(f"/api/evaluate/{EVAL_ID}").json()["execution"] == "formula"


def _stamp_book(root: Path) -> None:
    reg = root / "data" / "runs" / "registry.jsonl"
    records = [json.loads(line) for line in reg.read_text().splitlines()]
    for r in records:
        r["config"]["execution"] = "book"
        art = Path(r["artifact_path"])
        name = "walkforward.json" if r["command"] == "walkforward" else "evaluate.json"
        payload = json.loads((art / name).read_text())
        payload["execution"] = "book"
        (art / name).write_text(json.dumps(payload))
    reg.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in records))


def test_book_records_and_artifacts_read_as_book(seeded_root: Path) -> None:
    _stamp_book(seeded_root)
    client = TestClient(create_app(seeded_root))
    runs = client.get("/api/runs").json()
    assert {r["execution"] for r in runs} == {"book"}
    assert client.get(f"/api/runs/{EVAL_ID}").json()["execution"] == "book"
    assert client.get(f"/api/walkforward/{WF_ID}").json()["execution"] == "book"
    assert client.get(f"/api/evaluate/{EVAL_ID}").json()["execution"] == "book"
