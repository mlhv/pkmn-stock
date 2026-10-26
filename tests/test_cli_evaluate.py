"""pkmn evaluate: cross-strategy rigor over synthetic walkforward artifacts."""

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest
from typer.testing import CliRunner

from pkmn_quant.cli import app
from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA
from pkmn_quant.data.warehouse import Warehouse
from tests.helpers import price_row

START = date(2025, 1, 1)


def _write_curve(
    run_dir: Path,
    name: str,
    equity: list[float],
    as_wf: bool = True,
    execution: str | None = None,
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    days = [START + timedelta(days=i) for i in range(len(equity))]
    frame = pl.DataFrame({"date": days, "equity": equity})
    if as_wf:
        frame.write_parquet(run_dir / "stitched_equity.parquet")
        meta: dict[str, object] = {"strategy": name, "folds": [], "summary": {}}
        if execution is not None:  # None = a legacy artifact without the key
            meta["execution"] = execution
        (run_dir / "walkforward.json").write_text(json.dumps(meta))
    else:
        frame.write_parquet(run_dir / "equity.parquet")


BENCH_ID = "20260801T110000Z-bbb222"


def _record(
    root: Path,
    run_id: str,
    strategy: str,
    *,
    command: str = "backtest",
    config: dict[str, object] | None = None,
    recorded_at: str = "2026-08-01T11:00:00+00:00",
) -> dict[str, object]:
    return {
        "run_id": run_id,
        "recorded_at": recorded_at,
        "command": command,
        "strategy": strategy,
        "git_sha": "abc1234",
        "git_dirty": False,
        "config_hash": "deadbeef",
        "config": config or {},
        "data_fingerprint": {},
        "results": {},
        "artifact_path": str(root / "data" / "results" / run_id),
    }


def write_registry(root: Path, records: list[dict[str, object]]) -> None:
    reg = root / "data" / "runs" / "registry.jsonl"
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in records))


def seed_everything(
    root: Path, n_days: int = 200, extra_records: list[dict[str, object]] | None = None
) -> None:
    """Two walk-forward curves plus a REGISTERED buy-and-hold benchmark.

    The benchmark is a run-registry record pointing at data/results/<run_id>/,
    which is the only shape `pkmn evaluate` accepts. It used to be a
    hand-created `buy-and-hold-sealed-x/` directory picked up by a glob, and
    that glob is the bug this suite now guards against rather than depends on
    (see test_evaluate_ignores_a_hand_made_benchmark_directory).
    """
    # a real (tiny) warehouse so record_run's data fingerprint works
    w = Warehouse(Paths(root=root))
    w.write_prices(START, pl.DataFrame([price_row(START, 1, 10.0)], schema=PRICE_SCHEMA))
    rng = np.random.default_rng(0)
    results = root / "data" / "results"
    bench = list(100.0 * np.cumprod(1.0 + rng.normal(0.002, 0.01, n_days)))
    _write_curve(results / BENCH_ID, "buy-and-hold-sealed", bench, as_wf=False)
    write_registry(
        root,
        [_record(root, BENCH_ID, "buy-and-hold-sealed"), *(extra_records or [])],
    )
    for name, drift in [("alpha", 0.0), ("beta", -0.001)]:
        eq = list(100.0 * np.cumprod(1.0 + rng.normal(drift, 0.01, n_days)))
        _write_curve(results / f"wf-{name}-x", name, eq)


def run_eval(root: Path, *extra: str, execution: str | None = "formula") -> object:
    """seed_everything writes legacy artifacts and records (no execution key,
    i.e. formula), so the pre-Plan-B tests evaluate in formula mode and keep
    testing exactly what they tested. ``execution=None`` passes no flag and
    exercises the default (book)."""
    mode = [] if execution is None else ["--execution", execution]
    return CliRunner().invoke(
        app, ["evaluate", "--root", str(root), "--n-boot", "500", *mode, *extra]
    )


def test_evaluate_end_to_end(tmp_path: Path) -> None:
    seed_everything(tmp_path)
    result = run_eval(tmp_path)
    assert result.exit_code == 0, result.output
    out = next((tmp_path / "data" / "results").glob("evaluate-*"))
    report = (out / "report.md").read_text()
    assert "Reality Check" in report and "deflated" in report.lower()
    assert "inherit" in report  # caveat present
    payload = json.loads((out / "evaluate.json").read_text())
    assert set(payload["strategies"]) == {"alpha", "beta"}
    for s in payload["strategies"].values():
        assert s["ci"]["lo"] <= s["ci"]["point"] <= s["ci"]["hi"]
        assert 0.0 <= s["dsr"] <= 1.0
    assert 0.0 <= payload["reality_check_p"] <= 1.0
    assert payload["params"] == {"n_boot": 500, "mean_block": 10.0, "seed": 42}


def test_evaluate_is_deterministic(tmp_path: Path) -> None:
    seed_everything(tmp_path)
    assert run_eval(tmp_path).exit_code == 0
    first = json.loads(
        (next((tmp_path / "data" / "results").glob("evaluate-*")) / "evaluate.json").read_text()
    )
    assert run_eval(tmp_path).exit_code == 0  # overwrites same-day dir
    second = json.loads(
        (next((tmp_path / "data" / "results").glob("evaluate-*")) / "evaluate.json").read_text()
    )
    assert first == second


def _seed_real_prices(root: Path, n_days: int) -> None:
    w = Warehouse(Paths(root=root))
    for i in range(n_days):
        day = START + timedelta(days=i)
        w.write_prices(day, pl.DataFrame([price_row(day, 1, 10.0 + i * 0.01)], schema=PRICE_SCHEMA))
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


def test_evaluate_finds_benchmark_via_run_registry(tmp_path: Path) -> None:
    """Finding 1 regression. Plan 2b rekeyed `pkmn backtest` artifact dirs
    from data/results/buy-and-hold-sealed-{start}-{end}/ to
    data/results/<run_id>/, so no command on this branch can produce the
    old glob pattern any more. Produce the benchmark with the real `pkmn
    backtest` CLI (not a hand-written registry record, unlike
    seed_everything above) and confirm `pkmn evaluate` locates it."""
    n_days = 90
    _seed_real_prices(tmp_path, n_days)

    rng = np.random.default_rng(0)
    # `pkmn backtest` defaults to book execution (Plan B), so the benchmark it
    # records is book; the walk-forward artifacts must be book too or evaluate
    # rightly refuses to pair them (see the no-mixing tests below).
    for name, drift in [("alpha", 0.0), ("beta", -0.001)]:
        eq = list(100.0 * np.cumprod(1.0 + rng.normal(drift, 0.01, n_days)))
        _write_curve(tmp_path / "data" / "results" / f"wf-{name}-x", name, eq, execution="book")

    backtest_result = CliRunner().invoke(
        app,
        [
            "backtest",
            "--start",
            START.isoformat(),
            "--end",
            (START + timedelta(days=n_days - 1)).isoformat(),
            "--cash",
            "1000",
            "--root",
            str(tmp_path),
        ],
    )
    assert backtest_result.exit_code == 0, backtest_result.output

    # sanity: the old glob pattern really is undiscoverable on this branch
    assert not list((tmp_path / "data" / "results").glob("buy-and-hold-sealed-*"))

    result = run_eval(tmp_path, execution=None)  # the default mode, book
    assert result.exit_code == 0, result.output
    out = next((tmp_path / "data" / "results").glob("evaluate-*"))
    payload = json.loads((out / "evaluate.json").read_text())
    assert set(payload["strategies"]) == {"alpha", "beta"}
    assert payload["execution"] == "book"


def test_evaluate_ignores_a_hand_made_benchmark_directory(tmp_path: Path) -> None:
    """I1 regression, hole 1: the directory glob outranked the registry.

    `pkmn evaluate` used to glob data/results/buy-and-hold-sealed-* FIRST and
    take the longest curve, so anything with that name -- a hand-made
    directory, a stale pre-swap artifact -- silently beat the registered
    benchmark. It did: a Reality Check p of 1.0000 was published where the
    correct value was 0.8038. A directory name carries no config, git sha or
    data fingerprint, so it cannot answer "is this bar comparable?".

    Here the decoy is LONGER than the registered benchmark and moves five
    times as fast, so picking it is visible in the result, not just in the
    recorded path.
    """
    seed_everything(tmp_path)
    decoy = tmp_path / "data" / "results" / "buy-and-hold-sealed-decoy"
    _write_curve(decoy, "buy-and-hold", [100.0 * (1.0 + 0.05 * i) for i in range(300)], as_wf=False)

    assert run_eval(tmp_path).exit_code == 0
    out = next((tmp_path / "data" / "results").glob("evaluate-*"))
    payload = json.loads((out / "evaluate.json").read_text())
    assert payload["benchmark"] == str(tmp_path / "data" / "results" / BENCH_ID)
    assert "decoy" not in payload["benchmark"]


def test_evaluate_skips_paired_sidecar_benchmarks(tmp_path: Path) -> None:
    """I1 regression, hole 2: the registry path did not filter `benchmark_for`.

    Every web-triggered run is paired with an identical-starting-state
    buy-and-hold tagged config["benchmark_for"], which covers SOMEBODY ELSE'S
    window (one on disk spans 61 days against 660-day strategy curves).
    /api/runs and the dashboard both drop them; this did not, and it sorts
    newest-first, so the newest sidecar won.
    """
    sidecar_id = "20260802T120000Z-ccc333"
    seed_everything(
        tmp_path,
        extra_records=[
            _record(
                tmp_path,
                sidecar_id,
                "buy-and-hold-sealed",
                config={"benchmark_for": "some-other-run"},
                recorded_at="2026-08-02T12:00:00+00:00",  # newer than BENCH_ID
            )
        ],
    )
    _write_curve(
        tmp_path / "data" / "results" / sidecar_id,
        "buy-and-hold-sealed",
        [100.0 * (1.0 + 0.05 * i) for i in range(300)],
        as_wf=False,
    )
    assert run_eval(tmp_path).exit_code == 0
    payload = json.loads(
        (next((tmp_path / "data" / "results").glob("evaluate-*")) / "evaluate.json").read_text()
    )
    assert payload["benchmark"] == str(tmp_path / "data" / "results" / BENCH_ID)


def test_evaluate_rejects_a_benchmark_that_does_not_span_the_strategies(tmp_path: Path) -> None:
    """I1 regression, hole 3: no window-span guard.

    A short buy-and-hold is a legitimate registry record (a 61-day ad-hoc run
    exists on disk). The date intersection is taken over the benchmark too, so
    a 61-day curve against 660-day strategies clears the >= 60 check and
    silently produces a Reality Check over 61 days. `pkmn horizon` already
    refuses this; so must evaluate.
    """
    short_id = "20260803T120000Z-ddd444"
    seed_everything(
        tmp_path,
        extra_records=[
            _record(
                tmp_path,
                short_id,
                "buy-and-hold-sealed",
                recorded_at="2026-08-03T12:00:00+00:00",  # newest -> selected
            )
        ],
    )
    _write_curve(
        tmp_path / "data" / "results" / short_id,
        "buy-and-hold-sealed",
        [100.0 + i for i in range(61)],
        as_wf=False,
    )
    result = run_eval(tmp_path)
    assert result.exit_code != 0
    assert "does not span" in result.output
    assert "Traceback" not in result.output


def test_evaluate_records_run(tmp_path: Path) -> None:
    from pkmn_quant.research.runs import load_runs

    seed_everything(tmp_path)
    assert run_eval(tmp_path).exit_code == 0
    # the seeded benchmark record is also in the registry; evaluate appends
    (record,) = [r for r in load_runs(tmp_path) if r.command == "evaluate"]
    assert "reality_check_p" in record.results
    assert record.config["n_boot"] == 500


def test_evaluate_needs_two_strategies(tmp_path: Path) -> None:
    seed_everything(tmp_path)
    import shutil

    shutil.rmtree(tmp_path / "data" / "results" / "wf-beta-x")
    result = run_eval(tmp_path)
    assert result.exit_code != 0
    assert "2 strategies" in result.output
    assert "Traceback" not in result.output


def test_evaluate_clean_error_without_artifacts(tmp_path: Path) -> None:
    (tmp_path / "data" / "results").mkdir(parents=True)
    result = run_eval(tmp_path)
    assert result.exit_code != 0
    assert "no walk-forward artifacts" in result.output
    assert "Traceback" not in result.output


def test_evaluate_thin_overlap_errors(tmp_path: Path) -> None:
    seed_everything(tmp_path, n_days=30)  # < 60 common days
    result = run_eval(tmp_path)
    assert result.exit_code != 0
    assert "overlap" in result.output


def test_evaluate_rejects_zero_n_boot(tmp_path: Path) -> None:
    seed_everything(tmp_path)
    result = CliRunner().invoke(app, ["evaluate", "--root", str(tmp_path), "--n-boot", "0"])
    assert result.exit_code != 0
    assert "Traceback" not in result.output


# -- no mixing: book vs formula execution (Plan B Task 4) --


def _rewrite_wf_execution(root: Path, modes: dict[str, str]) -> None:
    """Stamp `execution` onto the seeded wf-<name>-x artifacts."""
    for name, mode in modes.items():
        meta_path = root / "data" / "results" / f"wf-{name}-x" / "walkforward.json"
        meta = json.loads(meta_path.read_text())
        meta["execution"] = mode
        meta_path.write_text(json.dumps(meta))


def _seed_book_beside_legacy(root: Path) -> str:
    """seed_everything's legacy (formula) alpha/beta artifacts and benchmark,
    plus book gamma/delta artifacts and an OLDER book benchmark record."""
    book_id = "20260731T110000Z-bbb999"
    seed_everything(
        root,
        extra_records=[
            _record(
                root,
                book_id,
                "buy-and-hold-sealed",
                config={"execution": "book"},
                recorded_at="2026-07-31T11:00:00+00:00",
            )
        ],
    )
    results = root / "data" / "results"
    _write_curve(
        results / book_id, "buy-and-hold-sealed", [100.0 + 0.1 * i for i in range(200)], False
    )
    rng = np.random.default_rng(7)
    for name in ("gamma", "delta"):
        eq = list(100.0 * np.cumprod(1.0 + rng.normal(0.0, 0.01, 200)))
        _write_curve(results / f"wf-{name}-x-book", name, eq, execution="book")
    return book_id


def _eval_payload(root: Path) -> dict[str, object]:
    out = next((root / "data" / "results").glob("evaluate-*"))
    payload: dict[str, object] = json.loads((out / "evaluate.json").read_text())
    return payload


def test_evaluate_default_book_ignores_legacy_formula_artifacts(tmp_path: Path) -> None:
    """Book artifacts beside legacy formula ones: the default evaluation reads
    only the book ones, against the book benchmark, and does not refuse."""
    book_id = _seed_book_beside_legacy(tmp_path)
    result = run_eval(tmp_path, execution=None)
    assert result.exit_code == 0, result.output
    payload = _eval_payload(tmp_path)
    assert set(payload["strategies"]) == {"gamma", "delta"}  # type: ignore[arg-type]
    assert payload["execution"] == "book"
    assert payload["benchmark"] == str(tmp_path / "data" / "results" / book_id)


def test_evaluate_formula_flag_reads_only_legacy_artifacts(tmp_path: Path) -> None:
    _seed_book_beside_legacy(tmp_path)
    result = run_eval(tmp_path, execution="formula")
    assert result.exit_code == 0, result.output
    payload = _eval_payload(tmp_path)
    assert set(payload["strategies"]) == {"alpha", "beta"}  # type: ignore[arg-type]
    assert payload["execution"] == "formula"
    assert payload["benchmark"] == str(tmp_path / "data" / "results" / BENCH_ID)


def test_evaluate_no_artifacts_in_mode_names_the_mode(tmp_path: Path) -> None:
    seed_everything(tmp_path)  # legacy (formula) only
    result = run_eval(tmp_path, execution=None)  # default book
    assert result.exit_code == 1
    assert "no walk-forward artifacts in book execution" in result.output
    assert "--execution formula" in result.output  # points at the skipped ones
    assert "Traceback" not in result.output


def test_evaluate_rejects_unknown_execution(tmp_path: Path) -> None:
    seed_everything(tmp_path)
    result = run_eval(tmp_path, execution="fast")
    assert result.exit_code == 1
    assert "unknown execution 'fast'" in result.output
    assert "Traceback" not in result.output


def test_evaluate_picks_benchmark_in_mode(tmp_path: Path) -> None:
    """The newer buy-and-hold is formula, the older one book: with book
    artifacts the bar must be the book one, never the newer formula run."""
    book_id = "20260731T110000Z-bbb999"  # older than BENCH_ID
    seed_everything(
        tmp_path,
        extra_records=[
            _record(
                tmp_path,
                book_id,
                "buy-and-hold-sealed",
                config={"execution": "book"},
                recorded_at="2026-07-31T11:00:00+00:00",
            )
        ],
    )
    _write_curve(
        tmp_path / "data" / "results" / book_id,
        "buy-and-hold-sealed",
        [100.0 + 0.1 * i for i in range(200)],
        as_wf=False,
    )
    _rewrite_wf_execution(tmp_path, {"alpha": "book", "beta": "book"})
    result = run_eval(tmp_path, execution="book")
    assert result.exit_code == 0, result.output
    payload = json.loads(
        (next((tmp_path / "data" / "results").glob("evaluate-*")) / "evaluate.json").read_text()
    )
    assert payload["benchmark"] == str(tmp_path / "data" / "results" / book_id)
    assert payload["execution"] == "book"

    from pkmn_quant.research.runs import load_runs

    (rec,) = [r for r in load_runs(tmp_path) if r.command == "evaluate"]
    assert rec.config["execution"] == "book"


def test_evaluate_legacy_artifacts_are_formula(tmp_path: Path) -> None:
    """No `execution` key anywhere (every pre-Plan-B artifact and record):
    everything reads as formula and evaluates as before."""
    seed_everything(tmp_path)
    result = run_eval(tmp_path)
    assert result.exit_code == 0, result.output
    payload = json.loads(
        (next((tmp_path / "data" / "results").glob("evaluate-*")) / "evaluate.json").read_text()
    )
    assert payload["execution"] == "formula"
    assert payload["benchmark"] == str(tmp_path / "data" / "results" / BENCH_ID)


def test_evaluate_refuses_explicit_benchmark_in_other_mode(tmp_path: Path) -> None:
    """--benchmark <dir> is looked up in the registry by artifact_path; BENCH_ID
    has no execution key (formula), the artifacts are book."""
    seed_everything(tmp_path)
    _rewrite_wf_execution(tmp_path, {"alpha": "book", "beta": "book"})
    bench_dir = tmp_path / "data" / "results" / BENCH_ID
    result = run_eval(tmp_path, "--benchmark", str(bench_dir), execution="book")
    assert result.exit_code == 1
    assert "formula" in result.output and "book" in result.output
    assert "Traceback" not in result.output


def test_evaluate_explicit_unregistered_benchmark_is_formula(tmp_path: Path) -> None:
    """A --benchmark directory no registry record points at reads as formula:
    fine against formula artifacts, refused against book ones."""
    seed_everything(tmp_path)
    loose = tmp_path / "data" / "results" / "loose-bench"
    _write_curve(loose, "buy-and-hold", [100.0 + 0.1 * i for i in range(200)], as_wf=False)
    ok = run_eval(tmp_path, "--benchmark", str(loose))
    assert ok.exit_code == 0, ok.output
    _rewrite_wf_execution(tmp_path, {"alpha": "book", "beta": "book"})
    bad = run_eval(tmp_path, "--benchmark", str(loose), execution="book")
    assert bad.exit_code == 1


def test_evaluate_book_and_formula_same_day_keep_separate_artifacts(tmp_path: Path) -> None:
    """Final-review Important 1: both modes evaluated on one day used to share
    evaluate-<date>/, so the second overwrote the first and the first run's
    registry record (served by /api/evaluate/{run_id}) pointed at the other
    mode's numbers. Book now writes evaluate-<date>-book/; formula keeps the
    legacy name. Each report and stdout also states the mode (Important 2)."""
    from pkmn_quant.research.execution import BOOK_LABEL
    from pkmn_quant.research.runs import load_runs

    _seed_book_beside_legacy(tmp_path)
    book = run_eval(tmp_path, execution="book")
    assert book.exit_code == 0, book.output
    formula = run_eval(tmp_path, execution="formula")
    assert formula.exit_code == 0, formula.output
    assert f"Execution: {BOOK_LABEL}" in book.output
    assert "Execution: formula" in formula.output

    results = tmp_path / "data" / "results"
    dirs = sorted(d.name for d in results.glob("evaluate-*"))
    assert len(dirs) == 2
    formula_dir, book_dir = dirs  # "...-book" sorts after the bare date
    assert book_dir == f"{formula_dir}-book"
    for name, mode, strategies in [
        (book_dir, "book", {"gamma", "delta"}),
        (formula_dir, "formula", {"alpha", "beta"}),
    ]:
        payload = json.loads((results / name / "evaluate.json").read_text())
        assert payload["execution"] == mode
        assert set(payload["strategies"]) == strategies
        report = (results / name / "report.md").read_text()
        expected = f"Execution: {BOOK_LABEL}" if mode == "book" else "Execution: formula"
        assert expected in report
    records = {r.config["execution"]: r for r in load_runs(tmp_path) if r.command == "evaluate"}
    assert Path(records["book"].artifact_path).name == book_dir
    assert Path(records["formula"].artifact_path).name == formula_dir


def test_evaluate_explicit_benchmark_resolves_relative_record_against_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Final-review M7: a registry record written with the default --root "."
    stores a root-relative artifact_path. `pkmn evaluate --root <repo>
    --benchmark <dir>` run from another cwd used to resolve it against cwd,
    miss the record, read the benchmark as formula and refuse a book bar."""
    repo = tmp_path / "repo"
    book_id = "20260731T110000Z-bbb999"
    rel = _record(repo, book_id, "buy-and-hold-sealed", config={"execution": "book"})
    rel["artifact_path"] = f"data/results/{book_id}"  # as written under --root "."
    seed_everything(repo, extra_records=[rel])
    bench_dir = repo / "data" / "results" / book_id
    _write_curve(bench_dir, "buy-and-hold-sealed", [100.0 + 0.1 * i for i in range(200)], False)
    _rewrite_wf_execution(repo, {"alpha": "book", "beta": "book"})
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    result = run_eval(repo, "--benchmark", str(bench_dir), execution="book")
    assert result.exit_code == 0, result.output
    out = next((repo / "data" / "results").glob("evaluate-*-book"))
    assert json.loads((out / "evaluate.json").read_text())["execution"] == "book"
