"""pkmn horizon: per-trade edge against the sealed index over each trip's own window.

The fixtures here hand-build what `run_single_backtest` produces (a run
directory with equity.parquet + fills.parquet, plus a registry record) so the
tests exercise the CLI's resolution/reporting logic without running the
engine.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import polars as pl
from typer.testing import CliRunner

from pkmn_quant.cli import app
from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA
from pkmn_quant.data.warehouse import Warehouse
from tests.helpers import price_row

START = date(2025, 1, 1)
# 26 weekly price/equity dates: 2025-01-01 .. 2025-06-25.
WEEKS = [START + timedelta(days=7 * i) for i in range(26)]
LAST_DAY = WEEKS[-1]
# Warehouse market price on WEEKS[i]; the mark on LAST_DAY closes the open lot.
MARKET = [10.0 + i for i in range(len(WEEKS))]
FINAL_MARK = MARKET[-1]  # 35.0

RUN_ID = "20260801T120000Z-aaa111"
BENCH_ID = "20260801T110000Z-bbb222"
# A second, OLDER, fully valid buy-and-hold benchmark. Its curve moves five
# times as fast as BENCH_ID's, so which one the resolver picks is visible in
# every trip's index_return -- not just in the recorded id.
OLDER_BENCH_ID = "20260801T090000Z-eee555"
OLDER_BENCH_EQUITY = [200.0 * (1.0 + 0.05 * i) for i in range(26)]

FILLS_SCHEMA = {
    "day": pl.Date,
    "product_id": pl.Int64,
    "sub_type": pl.Utf8,
    "quantity": pl.Int64,
    "price": pl.Float64,
    "fees": pl.Float64,
    "impact": pl.Float64,
}

# Two lots in, one sell out: FIFO closes the WEEKS[0] lot (70 days -> "1-3m")
# and leaves the WEEKS[5] lot open to LAST_DAY (140 days -> "3-6m").
FILLS: list[dict[str, Any]] = [
    {
        "day": WEEKS[0],
        "product_id": 1,
        "sub_type": "Normal",
        "quantity": 2,
        "price": 10.0,
        "fees": 0.5,
        "impact": 0.0,
    },
    {
        "day": WEEKS[5],
        "product_id": 1,
        "sub_type": "Normal",
        "quantity": 1,
        "price": 12.0,
        "fees": 0.0,
        "impact": 0.0,
    },
    {
        "day": WEEKS[10],
        "product_id": 1,
        "sub_type": "Normal",
        "quantity": -2,
        "price": 20.0,
        "fees": 1.0,
        "impact": 0.0,
    },
]

CLOSED_BASIS = 10.0 + 0.5 / 2  # 10.25
CLOSED_PROCEEDS = 20.0 - 1.0 / 2  # 19.5
OPEN_BASIS = 12.0
# The still-open lot (1 unit) exits at LAST_DAY's mark net of a MODELLED sale
# through the run's CostModel -- the registry config carries no "impact" key,
# so it defaults to on, matching `pkmn backtest`.
#   fees   = 1 * 35.00 * 0.1275 + 1.00 shipping = 5.4625
#   impact = sell walk from market 35.00 toward price_row's low of 1.00:
#            spread 34, q_cap for a $35 sale is 8 (tier <50)
#            -> 34 * 1 * (0 + 1) / (2 * 8) = 2.125
#   proceeds/unit = 35.00 - (5.4625 + 2.125) / 1 = 27.4125
OPEN_PROCEEDS = FINAL_MARK - (FINAL_MARK * 0.1275 + 1.0 + 34.0 / 16.0)


def seed_warehouse(root: Path) -> None:
    w = Warehouse(Paths(root=root))
    for day, market in zip(WEEKS, MARKET, strict=True):
        w.write_prices(day, pl.DataFrame([price_row(day, 1, market)], schema=PRICE_SCHEMA))
    w.write_products(
        pl.DataFrame(
            {
                "product_id": [1],
                "group_id": [1],
                "name": ["Box"],
                "rarity": [None],
                "kind": ["sealed"],
                "released_on": [START],
            }
        )
    )


def write_equity(run_dir: Path, equity: list[float]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"date": WEEKS, "equity": equity}).write_parquet(run_dir / "equity.parquet")


def write_fills(run_dir: Path, fills: list[dict[str, Any]]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(fills, schema=FILLS_SCHEMA).write_parquet(run_dir / "fills.parquet")


def record(
    root: Path,
    run_id: str,
    strategy: str,
    *,
    command: str = "backtest",
    config: dict[str, Any] | None = None,
    recorded_at: str | None = None,
    git_sha: str = "abc1234",
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        # recorded_at derives from the id's hour so "newest first" is readable
        # straight off the constants: BENCH_ID is 11:00, OLDER_BENCH_ID 09:00.
        "recorded_at": recorded_at or f"2026-08-01T{run_id[9:11]}:00:00+00:00",
        "command": command,
        "strategy": strategy,
        "git_sha": git_sha,
        "git_dirty": False,
        "config_hash": "deadbeef",
        "config": config or {},
        "data_fingerprint": {},
        "results": {},
        "artifact_path": str(root / "data" / "results" / run_id),
    }


def write_registry(root: Path, records: list[dict[str, Any]]) -> None:
    reg = root / "data" / "runs" / "registry.jsonl"
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in records))


def seed(
    root: Path,
    extra_records: list[dict[str, Any]] | None = None,
    bench_git_sha: str = "abc1234",
) -> None:
    """A strategy run (fills + equity) and a buy-and-hold benchmark run."""
    seed_warehouse(root)
    results = root / "data" / "results"
    write_fills(results / RUN_ID, FILLS)
    write_equity(results / RUN_ID, [1000.0 + 10.0 * i for i in range(len(WEEKS))])
    # Benchmark index: 100 * (1 + 0.01 * i) -- exactly computable returns.
    write_equity(results / BENCH_ID, [100.0 * (1.0 + 0.01 * i) for i in range(len(WEEKS))])
    window = {"start": START.isoformat(), "end": LAST_DAY.isoformat()}
    write_registry(
        root,
        [
            record(root, BENCH_ID, "buy-and-hold-sealed", config=window, git_sha=bench_git_sha),
            record(root, RUN_ID, "sealed-accumulation", config=window),
            *(extra_records or []),
        ],
    )


def run_horizon(root: Path, *extra: str) -> Any:
    return CliRunner().invoke(app, ["horizon", RUN_ID, "--root", str(root), *extra])


def read_payload(root: Path) -> dict[str, Any]:
    out = root / "data" / "results" / f"horizon-{RUN_ID}"
    payload: dict[str, Any] = json.loads((out / "horizon.json").read_text())
    return payload


def test_horizon_end_to_end(tmp_path: Path) -> None:
    seed(tmp_path)
    result = run_horizon(tmp_path, "--benchmark", BENCH_ID)
    assert result.exit_code == 0, result.output

    out = tmp_path / "data" / "results" / f"horizon-{RUN_ID}"
    payload = json.loads((out / "horizon.json").read_text())
    assert "median_edge" in payload
    assert "win_rate" in payload
    assert len(payload["trips"]) == 2

    closed, open_trip = sorted(payload["trips"], key=lambda t: t["entry"])
    assert closed["still_open"] is False
    assert closed["holding_days"] == 70
    assert closed["strategy_return"] == CLOSED_PROCEEDS / CLOSED_BASIS - 1.0
    # index over WEEKS[0] .. WEEKS[10]: 1.10 / 1.00 - 1
    assert closed["index_return"] == 1.10 - 1.0

    assert open_trip["still_open"] is True
    assert open_trip["exit"] == LAST_DAY.isoformat()
    assert open_trip["holding_days"] == 140
    # The still-open lot is closed at the WAREHOUSE mark on the last equity
    # day, NET of a modelled exit through the run's own CostModel -- a closed
    # trip's proceeds are already net, so scoring this one gross would have
    # handed it ~13 free points. See OPEN_PROCEEDS for the derivation.
    assert open_trip["strategy_return"] == OPEN_PROCEEDS / OPEN_BASIS - 1.0
    assert open_trip["strategy_return"] < FINAL_MARK / OPEN_BASIS - 1.0  # the gross number

    report = (out / "report.md").read_text()
    assert "1-3m" in report


def test_horizon_report_surfaces_the_1_3m_bucket(tmp_path: Path) -> None:
    """The 1-3m median edge is the number this command exists to produce."""
    seed(tmp_path)
    assert run_horizon(tmp_path, "--benchmark", BENCH_ID).exit_code == 0
    report = (tmp_path / "data" / "results" / f"horizon-{RUN_ID}" / "report.md").read_text()
    headline = next(line for line in report.splitlines() if "**1-3m" in line)
    # index over WEEKS[0] .. WEEKS[10] is 110.0/100.0 - 1; spell it that way so
    # this is an exact float comparison, not an approximate one.
    closed_edge = (CLOSED_PROCEEDS / CLOSED_BASIS - 1.0) - (1.10 - 1.0)
    assert f"{closed_edge:.2%}" in headline

    payload = read_payload(tmp_path)
    assert payload["edge_by_bucket"]["1-3m"] == closed_edge
    assert payload["holding_buckets"] == {"1-3m": 1, "3-6m": 1}


def test_horizon_bucket_names_match_the_horizon_module(tmp_path: Path) -> None:
    """The report's bucket labels must not drift from research/horizon.py."""
    from pkmn_quant.cli import HORIZON_BUCKETS
    from pkmn_quant.research.horizon import _BUCKET_ORDER

    assert HORIZON_BUCKETS == _BUCKET_ORDER


def test_horizon_resolves_benchmark_through_the_registry(tmp_path: Path) -> None:
    """Finding-1 regression, transplanted.

    `pkmn evaluate` once auto-discovered its benchmark by globbing
    data/results/ and taking the longest curve; the directory it found was a
    stale pre-swap artifact and a wrong Reality Check p-value was published.
    This test plants exactly that trap: a stale directory whose curve is
    LONGER than the real benchmark's and whose index returns are different,
    a newer non-benchmark backtest, a newer buy-and-hold run tagged
    `benchmark_for`, and an OLDER valid buy-and-hold run. Only the registry
    rule -- filters plus newest-first -- picks the right one.
    """
    other_id = "20260801T130000Z-ccc333"  # newer, but not buy-and-hold
    paired_id = "20260801T140000Z-ddd444"  # newer buy-and-hold, but a sidecar
    older_id = OLDER_BENCH_ID  # valid buy-and-hold, but older than BENCH_ID
    seed(
        tmp_path,
        extra_records=[
            record(tmp_path, other_id, "sealed-accumulation"),
            record(tmp_path, paired_id, "buy-and-hold-sealed", config={"benchmark_for": other_id}),
            record(tmp_path, older_id, "buy-and-hold-sealed"),
        ],
    )
    results = tmp_path / "data" / "results"
    write_equity(results / other_id, [500.0] * len(WEEKS))
    write_equity(results / paired_id, [700.0 + i for i in range(len(WEEKS))])
    write_equity(results / older_id, OLDER_BENCH_EQUITY)

    # The glob bait: a longer, flat-named curve with a DIFFERENT index return.
    stale = results / "buy-and-hold-sealed-2024-03-01-2026-06-30"
    stale.mkdir(parents=True, exist_ok=True)
    long_dates = [START + timedelta(days=i) for i in range(400)]
    pl.DataFrame({"date": long_dates, "equity": [50.0 + i for i in range(400)]}).write_parquet(
        stale / "equity.parquet"
    )

    result = run_horizon(tmp_path)  # no --benchmark: must resolve via registry
    assert result.exit_code == 0, result.output

    payload = read_payload(tmp_path)
    assert payload["benchmark_run_id"] == BENCH_ID
    assert payload["benchmark_strategy"] == "buy-and-hold-sealed"
    closed = min(payload["trips"], key=lambda t: t["entry"])
    # The registry benchmark's index return, not the stale directory's and not
    # the paired sidecar's.
    assert closed["index_return"] == 1.10 - 1.0


def test_horizon_picks_the_most_recent_benchmark(tmp_path: Path) -> None:
    """Two equally valid buy-and-hold benchmarks: the newer one must win.

    The filters alone cannot decide this -- both records pass every one of
    them -- so this is the only test that observes the newest-first sort.
    Resolving to the older record IS "resolve to a stale benchmark", the
    failure this command was written to prevent, so the assertion checks the
    resulting index_return and not merely the recorded id.
    """
    seed(tmp_path, extra_records=[record(tmp_path, OLDER_BENCH_ID, "buy-and-hold-sealed")])
    write_equity(tmp_path / "data" / "results" / OLDER_BENCH_ID, OLDER_BENCH_EQUITY)

    result = run_horizon(tmp_path)  # no --benchmark
    assert result.exit_code == 0, result.output

    payload = read_payload(tmp_path)
    assert payload["benchmark_run_id"] == BENCH_ID
    closed = min(payload["trips"], key=lambda t: t["entry"])
    # BENCH_ID's index over WEEKS[0] .. WEEKS[10] is 110/100 - 1 = 0.10;
    # OLDER_BENCH_ID's is 300/200 - 1 = 0.50.
    assert closed["index_return"] == 1.10 - 1.0
    assert closed["index_return"] != 300.0 / 200.0 - 1.0


def test_horizon_rejects_benchmark_that_ends_before_the_run(tmp_path: Path) -> None:
    """A benchmark truncated inside the run's window scores every later trip
    against a FLAT index: horizon.py's _index_at carries the last value
    forward past the curve's end, so a 140-day trip gets index_return 0.0 and
    the reported edge is inflated by the whole index move. Refuse it."""
    seed(tmp_path)
    short = tmp_path / "data" / "results" / BENCH_ID
    pl.DataFrame(
        {"date": WEEKS[:5], "equity": [100.0 * (1.0 + 0.01 * i) for i in range(5)]}
    ).write_parquet(short / "equity.parquet")

    result = run_horizon(tmp_path, "--benchmark", BENCH_ID)
    assert result.exit_code != 0, result.output
    assert "does not span" in result.output
    assert str(WEEKS[4]) in result.output  # the benchmark's own window
    assert str(LAST_DAY) in result.output  # the run's window
    assert "Traceback" not in result.output
    assert not (tmp_path / "data" / "results" / f"horizon-{RUN_ID}").exists()


def test_horizon_rejects_benchmark_that_starts_after_the_run(tmp_path: Path) -> None:
    seed(tmp_path)
    late = tmp_path / "data" / "results" / BENCH_ID
    pl.DataFrame(
        {"date": WEEKS[3:], "equity": [100.0 + i for i in range(len(WEEKS) - 3)]}
    ).write_parquet(late / "equity.parquet")

    result = run_horizon(tmp_path, "--benchmark", BENCH_ID)
    assert result.exit_code != 0, result.output
    assert "does not span" in result.output
    assert "Traceback" not in result.output


def test_horizon_rejects_short_benchmark_on_the_default_path(tmp_path: Path) -> None:
    """The coverage guard must hold without --benchmark too: the newest
    buy-and-hold backtest is routinely a short ad-hoc run (CLAUDE.md's own
    smoke test is a two-month window)."""
    seed(tmp_path)
    pl.DataFrame({"date": WEEKS[:5], "equity": [100.0] * 5}).write_parquet(
        tmp_path / "data" / "results" / BENCH_ID / "equity.parquet"
    )
    result = run_horizon(tmp_path)
    assert result.exit_code != 0, result.output
    assert "does not span" in result.output


def test_horizon_records_benchmark_provenance(tmp_path: Path) -> None:
    """`buy-and-hold-sealed` does not identify a benchmark: Task 1 of this
    branch rewrote the allocation rule, so the artifact must say WHICH build
    produced the bar."""
    seed(tmp_path)
    assert run_horizon(tmp_path, "--benchmark", BENCH_ID).exit_code == 0
    payload = read_payload(tmp_path)
    assert payload["benchmark_git_sha"] == "abc1234"
    assert payload["benchmark_git_dirty"] is False
    assert payload["benchmark_recorded_at"] == "2026-08-01T11:00:00+00:00"
    assert payload["benchmark_config_start"] == START.isoformat()
    assert payload["benchmark_config_end"] == LAST_DAY.isoformat()
    assert payload["benchmark_index_start"] == START.isoformat()
    assert payload["benchmark_index_end"] == LAST_DAY.isoformat()

    report = (tmp_path / "data" / "results" / f"horizon-{RUN_ID}" / "report.md").read_text()
    assert "abc1234" in report


def test_horizon_warns_when_benchmark_git_sha_differs(tmp_path: Path) -> None:
    seed(tmp_path, bench_git_sha="0000dead")
    result = run_horizon(tmp_path, "--benchmark", BENCH_ID)
    assert result.exit_code == 0, result.output
    assert "0000dead" in result.output
    report = (tmp_path / "data" / "results" / f"horizon-{RUN_ID}" / "report.md").read_text()
    assert "Warning" in report and "0000dead" in report


def test_horizon_unknown_run_id(tmp_path: Path) -> None:
    seed(tmp_path)
    result = CliRunner().invoke(app, ["horizon", "nope-not-a-run", "--root", str(tmp_path)])
    assert result.exit_code != 0
    assert "nope-not-a-run" in result.output
    assert "Traceback" not in result.output


def test_horizon_unknown_benchmark_id(tmp_path: Path) -> None:
    seed(tmp_path)
    result = run_horizon(tmp_path, "--benchmark", "nope-not-a-bench")
    assert result.exit_code != 0
    assert "nope-not-a-bench" in result.output
    assert "Traceback" not in result.output


def test_horizon_no_benchmark_in_registry(tmp_path: Path) -> None:
    """Only the strategy run is recorded: refuse rather than fall back to disk."""
    seed_warehouse(tmp_path)
    results = tmp_path / "data" / "results"
    write_fills(results / RUN_ID, FILLS)
    write_equity(results / RUN_ID, [1000.0] * len(WEEKS))
    write_registry(tmp_path, [record(tmp_path, RUN_ID, "sealed-accumulation")])
    # A directory a glob would happily find.
    write_equity(results / "buy-and-hold-sealed-2024-03-01-2026-06-30", [1.0] * len(WEEKS))

    result = run_horizon(tmp_path)
    assert result.exit_code != 0
    assert "benchmark" in result.output
    assert "Traceback" not in result.output


def test_horizon_run_with_no_fills(tmp_path: Path) -> None:
    seed(tmp_path)
    write_fills(tmp_path / "data" / "results" / RUN_ID, [])
    result = run_horizon(tmp_path, "--benchmark", BENCH_ID)
    assert result.exit_code != 0
    assert "no fills" in result.output
    assert "Traceback" not in result.output


def test_horizon_rejects_non_backtest_run(tmp_path: Path) -> None:
    seed(tmp_path)
    write_registry(
        tmp_path,
        [
            record(tmp_path, BENCH_ID, "buy-and-hold-sealed"),
            record(tmp_path, RUN_ID, "sealed-accumulation", command="walkforward"),
        ],
    )
    result = run_horizon(tmp_path, "--benchmark", BENCH_ID)
    assert result.exit_code != 0
    assert "walkforward" in result.output
    assert "Traceback" not in result.output


def test_horizon_unmatched_sell_is_a_clean_error(tmp_path: Path) -> None:
    seed(tmp_path)
    write_fills(tmp_path / "data" / "results" / RUN_ID, [FILLS[2]])
    result = run_horizon(tmp_path, "--benchmark", BENCH_ID)
    assert result.exit_code != 0
    assert "no matching buy" in result.output
    assert "Traceback" not in result.output


def test_horizon_missing_final_mark_is_a_clean_error(tmp_path: Path) -> None:
    """An open lot on a product the warehouse never priced cannot be closed."""
    seed(tmp_path)
    unpriced = dict(FILLS[0], product_id=999)
    write_fills(tmp_path / "data" / "results" / RUN_ID, [unpriced])
    result = run_horizon(tmp_path, "--benchmark", BENCH_ID)
    assert result.exit_code != 0
    assert "999" in result.output
    assert "Traceback" not in result.output


def test_horizon_records_run(tmp_path: Path) -> None:
    from pkmn_quant.research.runs import load_runs

    seed(tmp_path)
    assert run_horizon(tmp_path, "--benchmark", BENCH_ID).exit_code == 0
    records = [r for r in load_runs(tmp_path) if r.command == "horizon"]
    assert len(records) == 1
    rec = records[0]
    assert rec.strategy == "sealed-accumulation"
    assert rec.config["backtest_run_id"] == RUN_ID
    assert rec.config["benchmark_run_id"] == BENCH_ID
    # Provenance lands in the registry too, not only in horizon.json.
    assert rec.config["benchmark_git_sha"] == "abc1234"
    assert rec.config["benchmark_recorded_at"] == "2026-08-01T11:00:00+00:00"
    assert rec.config["benchmark_index_end"] == LAST_DAY.isoformat()
    assert rec.results["n_trips"] == 2.0
    assert "median_edge" in rec.results
    assert "median_edge_1_3m" in rec.results
    assert rec.artifact_path.endswith(f"horizon-{RUN_ID}")
