# pkmn_quant Plan 4: Live Signals + Dashboard + README

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `pkmn signals` (live recommendations from the same Strategy interface the backtester uses, carrying each strategy's out-of-sample walk-forward record), a thin Streamlit dashboard for demos/screenshots, and a README written for a 90-second hiring-manager skim — closing out the v1 spec.

**Architecture:** `src/pkmn_quant/research/artifacts.py` is new: walk-forward runs gain a machine-readable `walkforward.json` (per-fold params + summaries) that bridges research → live. `src/pkmn_quant/live/` is new: `signals.py` builds a Context at the latest warehouse date (reusing `MarketData` with warm-up so lookbacks work) and maps the strategy's orders to `Recommendation`s; `report.py` renders markdown + JSON. CLI grows `pkmn signals`. `app/dashboard.py` (outside src/, streamlit in an optional dependency group, not mypy'd — spec: "exists for demos, not as a product") reads the `data/results/` artifacts. README lands last, quoting `docs/research-findings-2026-07.md` verbatim.

**Tech Stack:** existing stack + **streamlit** (dependency group `dashboard` only — never a runtime dep of the package; CI does not install it).

**Key design decisions:**
- Live signals REQUIRE a prior walk-forward run for the strategy: params come from the LAST fold's optimized params (most recent regime), and every recommendation carries the run's OOS summary. No walk-forward artifact → clean error telling the user to run `pkmn walkforward` first. (Registry factories require all space keys, so there is no "default params" path — by design.)
- Live mode = one `on_bar` at the latest warehouse date with empty positions and hypothetical cash. Stateful strategies are fresh (reset); DipBuyer's `_entries` and momentum's rebalance clock start empty, which for a single live bar means "what would you enter today" — exactly the question a recommendation answers. Position tracking across days is out of scope (spec: manual action).
- The engine is untouched in this plan except zero lines: `MarketData.from_warehouse(warehouse, start=latest, end=latest, warmup_days=...)` already provides everything live mode needs. Golden regression must stay byte-identical.
- Plan 3 final-review leftovers are Task 1: params formatting in the fold table and `--objective-metric` on the walkforward CLI.

---

### Task 1: Plan-3 review leftovers — params formatting + `--objective-metric`

**Files:**
- Modify: `src/pkmn_quant/research/report.py`
- Modify: `src/pkmn_quant/research/walkforward.py` (hoist valid-metric set to a module constant)
- Modify: `src/pkmn_quant/cli.py` (walkforward command)
- Modify: `tests/research/test_report.py`, `tests/test_cli_walkforward.py`

- [ ] **Step 1: Add failing tests.** Append to `tests/research/test_report.py`:

```python
def test_params_formatted_compactly() -> None:
    from pkmn_quant.research.report import format_params

    assert format_params({"min_drawdown": 0.3287900192959751, "min_age_days": 33}) == (
        "min_drawdown=0.3288, min_age_days=33"
    )
    assert format_params({}) == "-"
```

Append to `tests/test_cli_walkforward.py` (reuses the existing `seed_forty_days` helper in that file):

```python
def test_walkforward_unknown_objective_metric_clean_error(tmp_path: Path) -> None:
    seed_forty_days(tmp_path)
    result = CliRunner().invoke(
        app,
        ["walkforward", "--strategy", "sealed-accumulation", "--start", "2025-01-01",
         "--end", "2025-02-09", "--objective-metric", "sharpe_ratio",
         "--root", str(tmp_path)],
    )
    assert result.exit_code != 0
    assert "sharpe_ratio" in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/research/test_report.py tests/test_cli_walkforward.py -v` → ImportError (`format_params`), and the CLI test fails because `--objective-metric` is not a known option.

- [ ] **Step 3: Implement.**

In `src/pkmn_quant/research/walkforward.py`, hoist the inline `valid = {...}` set (currently inside `run_walkforward`, ~line 100) to a module constant just below the type aliases, and use it in the check:

```python
VALID_OBJECTIVE_METRICS = frozenset(
    {"total_return", "cagr", "sharpe", "sortino", "calmar", "max_drawdown"}
)
```

```python
    if objective_metric not in VALID_OBJECTIVE_METRICS:
        raise ValueError(
            f"unknown objective_metric {objective_metric!r};"
            f" choose from {sorted(VALID_OBJECTIVE_METRICS)}"
        )
```

In `src/pkmn_quant/research/report.py`, add above `render_markdown`:

```python
def format_params(params: dict[str, float | int]) -> str:
    """Compact one-line params: floats to 4 significant digits, ints as-is."""
    if not params:
        return "-"
    parts = [
        f"{k}={v:.4g}" if isinstance(v, float) else f"{k}={v}" for k, v in params.items()
    ]
    return ", ".join(parts)
```

and change the fold-row f-string from `f"| {f.params} "` to `f"| {format_params(f.params)} "`.

In `src/pkmn_quant/cli.py` `walkforward` command: add the option (after `warmup_days`):

```python
    objective_metric: str = typer.Option(
        "total_return", help="Metric optuna maximizes in-sample; see VALID_OBJECTIVE_METRICS."
    ),
```

add `VALID_OBJECTIVE_METRICS` to the deferred `from pkmn_quant.research.walkforward import ...` line, validate right after the strategy check:

```python
    if objective_metric not in VALID_OBJECTIVE_METRICS:
        raise typer.BadParameter(
            f"unknown objective metric {objective_metric!r};"
            f" choose from {sorted(VALID_OBJECTIVE_METRICS)}"
        )
```

and pass `objective_metric=objective_metric` to `run_walkforward(...)`.

- [ ] **Step 4: Run tests, then all four gates:**

```bash
uv run pytest tests/research/test_report.py tests/test_cli_walkforward.py -q
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

Note: the existing `test_render_markdown_contains_fold_table_and_summary` asserts `"0.4" in md` (summary section) — unaffected. If it asserted on the raw dict repr it would need updating; it does not.

- [ ] **Step 5: Commit**

```bash
git add src/pkmn_quant/research/report.py src/pkmn_quant/research/walkforward.py src/pkmn_quant/cli.py tests/research/test_report.py tests/test_cli_walkforward.py
git commit -m "feat: compact fold-table params, --objective-metric flag"
```

---

### Task 2: Machine-readable walk-forward artifacts

**Files:**
- Create: `src/pkmn_quant/research/artifacts.py`
- Modify: `src/pkmn_quant/cli.py` (walkforward writes `walkforward.json`)
- Test: `tests/research/test_artifacts.py`; extend `tests/test_cli_walkforward.py`

The bridge between research and live: `walkforward.json` in each run dir holds strategy name, per-fold windows/params/summaries, and the overall summary.

- [ ] **Step 1: Write the failing tests** — `tests/research/test_artifacts.py`:

```python
import json
from datetime import date
from pathlib import Path

import polars as pl

from pkmn_quant.research.artifacts import (
    WalkForwardRun,
    find_latest_wf_run,
    load_walkforward_json,
    write_walkforward_json,
)
from pkmn_quant.research.folds import Fold
from pkmn_quant.research.walkforward import FoldResult, WalkForwardResult


def _result() -> WalkForwardResult:
    fold = Fold(date(2024, 1, 1), date(2024, 6, 28), date(2024, 6, 29), date(2024, 8, 27))
    fr = FoldResult(
        fold=fold,
        params={"hold_days": 30, "take_profit": 1.25},
        is_summary={"total_return": 0.5},
        oos_summary={"total_return": 0.1},
        oos_curve=pl.DataFrame({"date": [date(2024, 6, 29)], "equity": [1000.0]}),
    )
    return WalkForwardResult(
        folds=[fr],
        stitched_curve=pl.DataFrame({"date": [date(2024, 6, 29)], "equity": [1000.0]}),
        summary={"stitched_total_return": 0.1, "overfitting_gap": 0.4},
    )


def test_json_round_trip(tmp_path: Path) -> None:
    write_walkforward_json(tmp_path, _result(), strategy_name="dip-buyer")
    raw = json.loads((tmp_path / "walkforward.json").read_text())
    assert raw["strategy"] == "dip-buyer"

    run = load_walkforward_json(tmp_path)
    assert isinstance(run, WalkForwardRun)
    assert run.strategy == "dip-buyer"
    assert run.folds[0].params == {"hold_days": 30, "take_profit": 1.25}
    assert run.folds[0].oos_start == "2024-06-29"
    assert run.summary["overfitting_gap"] == 0.4


def test_find_latest_wf_run_picks_lexicographically_last(tmp_path: Path) -> None:
    for name in ["wf-dip-buyer-2024-01-01-2024-06-30", "wf-dip-buyer-2024-01-01-2025-06-30"]:
        d = tmp_path / name
        d.mkdir()
        write_walkforward_json(d, _result(), strategy_name="dip-buyer")
    (tmp_path / "wf-xs-momentum-2024-01-01-2026-06-30").mkdir()  # no json inside; other strategy

    found = find_latest_wf_run(tmp_path, "dip-buyer")
    assert found is not None and found.name == "wf-dip-buyer-2024-01-01-2025-06-30"
    assert find_latest_wf_run(tmp_path, "xs-momentum") is None
    assert find_latest_wf_run(tmp_path / "missing", "dip-buyer") is None
```

- [ ] **Step 2: verify failure** (ModuleNotFoundError), then **Step 3: Implement** — `src/pkmn_quant/research/artifacts.py`:

```python
"""Machine-readable walk-forward artifacts: the bridge from research to live.

walkforward.json schema:
{"strategy": str,
 "folds": [{"is_start": "YYYY-MM-DD", "is_end": ..., "oos_start": ..., "oos_end": ...,
            "params": {name: number}, "is_summary": {...}, "oos_summary": {...}}],
 "summary": {metric: number}}
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pkmn_quant.research.walkforward import WalkForwardResult

Params = dict[str, float | int]


@dataclass(frozen=True)
class FoldRecord:
    is_start: str
    is_end: str
    oos_start: str
    oos_end: str
    params: Params
    is_summary: dict[str, float]
    oos_summary: dict[str, float]


@dataclass(frozen=True)
class WalkForwardRun:
    strategy: str
    folds: list[FoldRecord]
    summary: dict[str, float]


def write_walkforward_json(
    run_dir: Path, result: WalkForwardResult, strategy_name: str
) -> None:
    payload = {
        "strategy": strategy_name,
        "folds": [
            {
                "is_start": f.fold.is_start.isoformat(),
                "is_end": f.fold.is_end.isoformat(),
                "oos_start": f.fold.oos_start.isoformat(),
                "oos_end": f.fold.oos_end.isoformat(),
                "params": f.params,
                "is_summary": f.is_summary,
                "oos_summary": f.oos_summary,
            }
            for f in result.folds
        ],
        "summary": result.summary,
    }
    (run_dir / "walkforward.json").write_text(json.dumps(payload, indent=2) + "\n")


def load_walkforward_json(run_dir: Path) -> WalkForwardRun:
    raw = json.loads((run_dir / "walkforward.json").read_text())
    return WalkForwardRun(
        strategy=str(raw["strategy"]),
        folds=[FoldRecord(**f) for f in raw["folds"]],
        summary={str(k): float(v) for k, v in raw["summary"].items()},
    )


def find_latest_wf_run(results_dir: Path, strategy: str) -> Path | None:
    """Latest run dir for a strategy: lexicographically last wf-{strategy}-* dir
    containing walkforward.json. Run dirs embed ISO dates, so lexicographic ==
    chronological for a fixed strategy prefix."""
    if not results_dir.exists():
        return None
    candidates = sorted(
        p
        for p in results_dir.iterdir()
        if p.is_dir()
        and p.name.startswith(f"wf-{strategy}-")
        and (p / "walkforward.json").exists()
    )
    return candidates[-1] if candidates else None
```

mypy note: `FoldRecord(**f)` from `json.loads` output needs `f` typed — if mypy complains, build explicitly:

```python
        folds=[
            FoldRecord(
                is_start=str(f["is_start"]),
                is_end=str(f["is_end"]),
                oos_start=str(f["oos_start"]),
                oos_end=str(f["oos_end"]),
                params=dict(f["params"]),
                is_summary={str(k): float(v) for k, v in f["is_summary"].items()},
                oos_summary={str(k): float(v) for k, v in f["oos_summary"].items()},
            )
            for f in raw["folds"]
        ],
```

- [ ] **Step 4: Wire into the CLI.** In `cli.py` `walkforward`, add `from pkmn_quant.research.artifacts import write_walkforward_json` to the deferred imports and, next to the existing parquet/report writes:

```python
    write_walkforward_json(run_dir, result, strategy_name=strategy)
```

Extend `tests/test_cli_walkforward.py::test_walkforward_cli_runs_and_writes_report` with one assertion:

```python
    assert (run_dir / "walkforward.json").exists()
```

- [ ] **Step 5: Run tests, all four gates, commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
git add src/pkmn_quant/research/artifacts.py src/pkmn_quant/cli.py tests/research/test_artifacts.py tests/test_cli_walkforward.py
git commit -m "feat: machine-readable walkforward.json artifact"
```

---

### Task 3: Live signal generation

**Files:**
- Create: `src/pkmn_quant/live/__init__.py` (empty)
- Create: `src/pkmn_quant/live/signals.py`
- Test: `tests/live/__init__.py` (empty), `tests/live/test_signals.py`

- [ ] **Step 1: Write the failing tests** — `tests/live/test_signals.py`:

```python
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA
from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.live.signals import SignalsError, generate_signals
from pkmn_quant.research.artifacts import write_walkforward_json
from pkmn_quant.research.folds import Fold
from pkmn_quant.research.walkforward import FoldResult, WalkForwardResult
from tests.helpers import price_row

START = date(2025, 1, 1)
LATEST = START + timedelta(days=120)


@pytest.fixture
def warehouse(tmp_path: Path) -> Warehouse:
    """A sealed product that peaked at 200 then fell to 100 (50% drawdown),
    aged 121 days at LATEST: qualifies for sealed-accumulation entry."""
    w = Warehouse(Paths(root=tmp_path))
    for i in range(121):
        d = START + timedelta(days=i)
        price = 200.0 if i < 30 else 100.0
        w.write_prices(d, pl.DataFrame([price_row(d, 1, price)], schema=PRICE_SCHEMA))
    w.write_products(
        pl.DataFrame(
            {
                "product_id": [1],
                "group_id": [1],
                "name": ["Crashed Box"],
                "rarity": [None],
                "kind": ["sealed"],
                "released_on": [START],
            }
        )
    )
    return w


def seed_wf_artifact(results_dir: Path) -> None:
    run_dir = results_dir / "wf-sealed-accumulation-2025-01-01-2025-04-01"
    run_dir.mkdir(parents=True)
    fold = Fold(date(2025, 1, 1), date(2025, 2, 1), date(2025, 2, 2), date(2025, 3, 1))
    fr = FoldResult(
        fold=fold,
        params={"min_drawdown": 0.25, "take_profit": 1.5, "min_age_days": 60},
        is_summary={"total_return": 0.05},
        oos_summary={"total_return": 0.01},
        oos_curve=pl.DataFrame({"date": [date(2025, 2, 2)], "equity": [1000.0]}),
    )
    result = WalkForwardResult(
        folds=[fr],
        stitched_curve=pl.DataFrame({"date": [date(2025, 2, 2)], "equity": [1000.0]}),
        summary={"stitched_total_return": 0.01, "overfitting_gap": 0.04},
    )
    write_walkforward_json(run_dir, result, strategy_name="sealed-accumulation")


def test_generates_buy_recommendation(warehouse: Warehouse, tmp_path: Path) -> None:
    results_dir = tmp_path / "data" / "results"
    seed_wf_artifact(results_dir)
    report = generate_signals(
        warehouse=warehouse,
        strategy_name="sealed-accumulation",
        cash=1000.0,
        results_dir=results_dir,
    )
    assert report.as_of == LATEST
    assert report.strategy == "sealed-accumulation"
    assert report.params == {"min_drawdown": 0.25, "take_profit": 1.5, "min_age_days": 60}
    assert report.wf_summary["overfitting_gap"] == 0.04
    [rec] = report.recommendations
    assert rec.action == "BUY"
    assert rec.product_id == 1
    assert rec.name == "Crashed Box"
    assert rec.market_price == 100.0
    assert rec.quantity == 1  # floor(1000 * 0.10 budget_frac / 100)
    assert rec.notional == 100.0


def test_no_artifact_raises_clean_error(warehouse: Warehouse, tmp_path: Path) -> None:
    with pytest.raises(SignalsError, match="pkmn walkforward"):
        generate_signals(
            warehouse=warehouse,
            strategy_name="sealed-accumulation",
            cash=1000.0,
            results_dir=tmp_path / "data" / "results",
        )


def test_unknown_strategy_raises(warehouse: Warehouse, tmp_path: Path) -> None:
    with pytest.raises(SignalsError, match="unknown strategy"):
        generate_signals(
            warehouse=warehouse, strategy_name="nope", cash=1000.0,
            results_dir=tmp_path / "data" / "results",
        )
```

- [ ] **Step 2: verify failure**, then **Step 3: Implement** — `src/pkmn_quant/live/signals.py`:

```python
"""Live mode: one on_bar at the latest warehouse date -> recommendations.

The strategy cannot tell it is live (same Context as backtests) — the
project's central design invariant. Params come from the LAST fold of the
latest walk-forward run (the most recently optimized regime); every report
carries that run's OOS summary so a recommendation is never separated from
its honest track record. Positions are empty and cash is hypothetical:
recommendations answer "what would this strategy enter today".
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

import polars as pl

from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.data import MarketData
from pkmn_quant.engine.strategy import Context
from pkmn_quant.research.artifacts import find_latest_wf_run, load_walkforward_json
from pkmn_quant.research.registry import REGISTRY

Params = dict[str, float | int]

DEFAULT_WARMUP_DAYS = 365


class SignalsError(Exception):
    """User-facing signal-generation failure (clean CLI message)."""


@dataclass(frozen=True)
class Recommendation:
    action: str  # "BUY" | "SELL"
    product_id: int
    sub_type: str
    name: str
    quantity: int
    market_price: float
    notional: float


@dataclass(frozen=True)
class SignalReport:
    as_of: date
    strategy: str
    params: Params
    wf_summary: dict[str, float]
    wf_run_dir: str
    recommendations: list[Recommendation]


def generate_signals(
    warehouse: Warehouse,
    strategy_name: str,
    cash: float,
    results_dir: Path,
    warmup_days: int = DEFAULT_WARMUP_DAYS,
) -> SignalReport:
    entry = REGISTRY.get(strategy_name)
    if entry is None:
        raise SignalsError(f"unknown strategy {strategy_name!r}; known: {sorted(REGISTRY)}")

    run_dir = find_latest_wf_run(results_dir, strategy_name)
    if run_dir is None:
        raise SignalsError(
            f"no walk-forward run found for {strategy_name!r} in {results_dir};"
            f" run `pkmn walkforward --strategy {strategy_name} ...` first"
        )
    run = load_walkforward_json(run_dir)
    if not run.folds:
        raise SignalsError(f"walk-forward run {run_dir} has no folds")
    params = run.folds[-1].params

    prices = warehouse.load_prices()
    if prices.height == 0:
        raise SignalsError("warehouse has no price data; run `pkmn ingest` first")
    latest = prices["date"].max()
    assert isinstance(latest, date)

    market = MarketData.from_warehouse(warehouse, latest, latest, warmup_days=warmup_days)
    strategy = entry.factory(params)
    strategy.reset()
    ctx = Context(
        today=latest,
        history=market.history_until(latest),
        products=warehouse.load_products(),
        positions={},
        cash=cash,
        marks=market.marks_on(latest),
    )
    orders = strategy.on_bar(ctx)

    names = {
        int(r["product_id"]): str(r["name"])
        for r in ctx.products.select("product_id", "name").iter_rows(named=True)
    }
    marks = ctx.marks
    recommendations: list[Recommendation] = []
    for order in orders:
        mark = marks.get(order.asset)
        if mark is None:  # unreachable: strategies only order marked assets
            continue
        qty = abs(order.quantity)
        recommendations.append(
            Recommendation(
                action="BUY" if order.quantity > 0 else "SELL",
                product_id=order.asset.product_id,
                sub_type=order.asset.sub_type,
                name=names.get(order.asset.product_id, f"product {order.asset.product_id}"),
                quantity=qty,
                market_price=mark,
                notional=round(qty * mark, 2),
            )
        )

    return SignalReport(
        as_of=latest,
        strategy=strategy_name,
        params=params,
        wf_summary=run.summary,
        wf_run_dir=str(run_dir),
        recommendations=recommendations,
    )
```

Notes for the implementer: `replace` and `pl` imports are unused as written — drop whatever ruff flags. Verify against the real `Context` field names. `prices["date"].max()` returns a Python date via polars; the `assert isinstance` narrows for mypy — if polars returns a different scalar type, convert explicitly.

- [ ] **Step 4: Run tests (3 PASSED), all four gates, commit**

```bash
git add src/pkmn_quant/live/ tests/live/
git commit -m "feat: live signal generation from latest warehouse date"
```

---

### Task 4: Signal report rendering (markdown + JSON)

**Files:**
- Create: `src/pkmn_quant/live/report.py`
- Test: `tests/live/test_report.py`

- [ ] **Step 1: Write the failing tests** — `tests/live/test_report.py`:

```python
import json
from datetime import date

from pkmn_quant.live.report import render_signals_markdown, signals_to_json
from pkmn_quant.live.signals import Recommendation, SignalReport


def _report(recs: list[Recommendation]) -> SignalReport:
    return SignalReport(
        as_of=date(2026, 6, 30),
        strategy="sealed-accumulation",
        params={"min_drawdown": 0.25, "take_profit": 1.5, "min_age_days": 60},
        wf_summary={"stitched_total_return": 0.136, "overfitting_gap": 0.0476},
        wf_run_dir="data/results/wf-sealed-accumulation-2024-03-01-2026-06-30",
        recommendations=recs,
    )


REC = Recommendation(
    action="BUY", product_id=1, sub_type="Normal", name="Crashed Box",
    quantity=2, market_price=100.0, notional=200.0,
)


def test_markdown_contains_recommendation_and_wf_record() -> None:
    md = render_signals_markdown(_report([REC]))
    assert "sealed-accumulation" in md
    assert "2026-06-30" in md
    assert "Crashed Box" in md and "BUY" in md and "$200.00" in md
    assert "stitched_total_return" in md  # OOS record travels with the signal
    assert "min_drawdown=0.25" in md
    assert "Thesis:" in md and "supply dries" in md  # strategy reasoning line


def test_markdown_no_recommendations() -> None:
    md = render_signals_markdown(_report([]))
    assert "No recommendations" in md


def test_json_round_trips() -> None:
    raw = json.loads(signals_to_json(_report([REC])))
    assert raw["as_of"] == "2026-06-30"
    assert raw["strategy"] == "sealed-accumulation"
    assert raw["recommendations"][0]["name"] == "Crashed Box"
    assert raw["recommendations"][0]["notional"] == 200.0
    assert raw["wf_summary"]["overfitting_gap"] == 0.0476
```

- [ ] **Step 2: verify failure**, then **Step 3: Implement** — `src/pkmn_quant/live/report.py`:

```python
"""Render a SignalReport as markdown (for stdout) and JSON (artifact)."""

from __future__ import annotations

import json
from dataclasses import asdict

from pkmn_quant.live.signals import SignalReport
from pkmn_quant.research.report import format_params

# One-line thesis per strategy (the "strategy reasoning" the spec attaches to
# recommendations; strategies themselves don't emit per-order rationales).
THESIS = {
    "sealed-accumulation": (
        "Sealed products crash post-release then grind up as supply dries;"
        " buy aged drawdowns, sell at a target multiple."
    ),
    "dip-buyer": (
        "Sharp one-week dips in singles may mean-revert; buy the dip, exit on"
        " time or profit target."
    ),
    "xs-momentum": (
        "Winners keep winning: hold the top trailing performers among singles,"
        " rebalance periodically."
    ),
}


def render_signals_markdown(report: SignalReport) -> str:
    lines = [
        f"# Signals: {report.strategy} — {report.as_of}",
        "",
        f"Thesis: {THESIS.get(report.strategy, 'n/a')}",
        f"Params (last walk-forward fold): {format_params(report.params)}",
        f"Walk-forward record ({report.wf_run_dir}):",
    ]
    lines += [f"- {k}: {v:.4f}" for k, v in report.wf_summary.items()]
    lines.append("")
    if not report.recommendations:
        lines.append("No recommendations today.")
    else:
        lines += [
            "| action | product | sub_type | qty | market | notional |",
            "|--------|---------|----------|-----|--------|----------|",
        ]
        lines += [
            f"| {r.action} | {r.name} | {r.sub_type} | {r.quantity}"
            f" | ${r.market_price:.2f} | ${r.notional:.2f} |"
            for r in report.recommendations
        ]
    lines += [
        "",
        "Not financial advice; thin-market marks, ~12-15% round-trip costs.",
        "See docs/research-findings-2026-07.md for the honest track record.",
    ]
    return "\n".join(lines) + "\n"


def signals_to_json(report: SignalReport) -> str:
    payload = asdict(report)
    payload["as_of"] = report.as_of.isoformat()
    return json.dumps(payload, indent=2) + "\n"
```

- [ ] **Step 4: Run tests (3 PASSED), all four gates, commit**

```bash
git add src/pkmn_quant/live/report.py tests/live/test_report.py
git commit -m "feat: signal report rendering (markdown + JSON)"
```

---

### Task 5: `pkmn signals` CLI

**Files:**
- Modify: `src/pkmn_quant/cli.py`
- Test: `tests/test_cli_signals.py`

- [ ] **Step 1: Write the failing tests** — `tests/test_cli_signals.py`:

```python
from datetime import date, timedelta
from pathlib import Path

import polars as pl
from typer.testing import CliRunner

from pkmn_quant.cli import app
from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA
from pkmn_quant.data.warehouse import Warehouse
from tests.helpers import price_row


def seed(root: Path) -> None:
    w = Warehouse(Paths(root=root))
    start = date(2025, 1, 1)
    for i in range(121):
        d = start + timedelta(days=i)
        price = 200.0 if i < 30 else 100.0
        w.write_prices(d, pl.DataFrame([price_row(d, 1, price)], schema=PRICE_SCHEMA))
    w.write_products(pl.DataFrame({
        "product_id": [1], "group_id": [1], "name": ["Crashed Box"],
        "rarity": [None], "kind": ["sealed"], "released_on": [start],
    }))


def test_signals_cli_end_to_end(tmp_path: Path) -> None:
    seed(tmp_path)
    runner = CliRunner()
    wf = runner.invoke(app, [
        "walkforward", "--strategy", "sealed-accumulation",
        "--start", "2025-01-01", "--end", "2025-04-11",
        "--is-days", "30", "--oos-days", "30", "--trials", "2",
        "--cash", "1000", "--root", str(tmp_path),
    ])
    assert wf.exit_code == 0, wf.output

    result = runner.invoke(app, [
        "signals", "--strategy", "sealed-accumulation",
        "--cash", "1000", "--root", str(tmp_path),
    ])
    assert result.exit_code == 0, result.output
    assert "sealed-accumulation" in result.output
    out_dirs = [p for p in (tmp_path / "data" / "results").iterdir() if p.name.startswith("signals-")]
    assert len(out_dirs) == 1
    assert (out_dirs[0] / "signals.md").exists()
    assert (out_dirs[0] / "signals.json").exists()


def test_signals_cli_without_walkforward_clean_error(tmp_path: Path) -> None:
    seed(tmp_path)
    result = CliRunner().invoke(
        app, ["signals", "--strategy", "sealed-accumulation", "--root", str(tmp_path)]
    )
    assert result.exit_code != 0
    assert "pkmn walkforward" in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)
```

- [ ] **Step 2: verify failure**, then **Step 3: Implement** — add to `cli.py` (matching house style: deferred imports, `dt` alias unused here):

```python
@app.command()
def signals(
    strategy: str = typer.Option(..., help="Strategy name: see pkmn_quant.research.registry."),
    cash: float = typer.Option(10_000.0, help="Hypothetical cash for position sizing."),
    warmup_days: int = typer.Option(
        365, help="History days loaded before the latest date for signal lookbacks."
    ),
    root: Path = typer.Option(Path("."), help="Project root holding the data/ directory."),
) -> None:
    """Run a strategy in live mode against the latest ingested prices."""
    from pkmn_quant.data.warehouse import Warehouse
    from pkmn_quant.live.report import render_signals_markdown, signals_to_json
    from pkmn_quant.live.signals import SignalsError, generate_signals

    results_dir = root / "data" / "results"
    try:
        report = generate_signals(
            warehouse=Warehouse(Paths(root=root)),
            strategy_name=strategy,
            cash=cash,
            results_dir=results_dir,
            warmup_days=warmup_days,
        )
    except SignalsError as exc:
        raise typer.BadParameter(str(exc)) from exc

    markdown = render_signals_markdown(report)
    out_dir = results_dir / f"signals-{strategy}-{report.as_of.isoformat()}"
    if out_dir.exists():
        typer.echo(f"warning: overwriting existing results in {out_dir}", err=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "signals.md").write_text(markdown)
    (out_dir / "signals.json").write_text(signals_to_json(report))

    typer.echo(markdown)
    typer.echo(f"artifacts written to {out_dir}", err=True)
```

- [ ] **Step 4: Run tests, all four gates, commit**

```bash
git add src/pkmn_quant/cli.py tests/test_cli_signals.py
git commit -m "feat: pkmn signals CLI"
```

---

### Task 6: Streamlit dashboard

**Files:**
- Modify: `pyproject.toml` + `uv.lock` (streamlit in dependency group `dashboard`)
- Create: `app/dashboard.py`

No unit tests for the streamlit script itself (spec: demo tool, not a product; mypy covers src/ only). The data plumbing it uses (`artifacts.py`, warehouse) is already tested. Gate: ruff must pass on `app/`, and the manual smoke step below.

- [ ] **Step 1: Add the dependency group** — `uv add --group dashboard "streamlit>=1.40"`. Commit pyproject.toml and uv.lock together (CI `uv sync --frozen` rule). CI does not install the group; verify `uv run pytest` still passes without streamlit imported anywhere under src/ or tests/.

- [ ] **Step 2: Implement** — `app/dashboard.py`:

```python
"""Streamlit results explorer for pkmn_quant.

Run from the repo root (after at least one `pkmn walkforward` run):

    uv run --group dashboard streamlit run app/dashboard.py

Reads data/results/ artifacts and the Parquet warehouse. Demo tool only:
not type-checked, not imported by the package or tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import streamlit as st

ROOT = Path(".")
RESULTS = ROOT / "data" / "results"


def wf_runs() -> list[Path]:
    if not RESULTS.exists():
        return []
    return sorted(
        p for p in RESULTS.iterdir() if p.is_dir() and (p / "walkforward.json").exists()
    )


def benchmark_runs() -> list[Path]:
    if not RESULTS.exists():
        return []
    return sorted(
        p
        for p in RESULTS.iterdir()
        if p.is_dir() and (p / "equity.parquet").exists() and not p.name.startswith("wf-")
    )


def signal_runs() -> list[Path]:
    if not RESULTS.exists():
        return []
    return sorted(p for p in RESULTS.iterdir() if p.name.startswith("signals-"))


@st.cache_data
def load_prices() -> pl.DataFrame:
    return pl.read_parquet("data/warehouse/prices/**/*.parquet")


@st.cache_data
def load_products() -> pl.DataFrame:
    return pl.read_parquet("data/warehouse/products.parquet")


st.set_page_config(page_title="pkmn_quant", layout="wide")
st.title("pkmn_quant — results explorer")
st.caption(
    "Sharpe/Sortino inflated by mark smoothing (thin markets, carry-forward marks). "
    "Compare against the buy-and-hold benchmark, not equities."
)

tab_wf, tab_signals, tab_prices, tab_trades = st.tabs(
    ["Walk-forward", "Signals", "Prices", "Trades"]
)

with tab_wf:
    runs = wf_runs()
    if not runs:
        st.info("No walk-forward runs found. Run `uv run pkmn walkforward ...` first.")
    else:
        run_dir = st.selectbox("Run", runs, format_func=lambda p: p.name)
        wf = json.loads((run_dir / "walkforward.json").read_text())

        stitched = pl.read_parquet(run_dir / "stitched_equity.parquet")
        curve = stitched.rename({"equity": wf["strategy"]})
        bench = benchmark_runs()
        if bench:
            bench_dir = st.selectbox(
                "Benchmark overlay", bench, format_func=lambda p: p.name
            )
            b = pl.read_parquet(bench_dir / "equity.parquet")
            # Rescale benchmark to the stitched curve's starting level and
            # restrict to the stitched date range for a fair visual overlay.
            lo, hi = stitched["date"].min(), stitched["date"].max()
            b = b.filter((pl.col("date") >= lo) & (pl.col("date") <= hi))
            if b.height > 0:
                scale = float(stitched["equity"][0]) / float(b["equity"][0])
                b = b.with_columns((pl.col("equity") * scale).alias("benchmark"))
                curve = curve.join(b.select("date", "benchmark"), on="date", how="left")
        st.line_chart(curve.to_pandas().set_index("date"))

        st.subheader("Summary (stitched OOS)")
        st.dataframe(
            pl.DataFrame(
                {"metric": list(wf["summary"]), "value": list(wf["summary"].values())}
            ).to_pandas(),
            hide_index=True,
        )

        st.subheader("Folds")
        fold_rows = [
            {
                "IS": f"{f['is_start']} .. {f['is_end']}",
                "OOS": f"{f['oos_start']} .. {f['oos_end']}",
                "params": ", ".join(f"{k}={v:.4g}" for k, v in f["params"].items()),
                "IS ret": f["is_summary"]["total_return"],
                "OOS ret": f["oos_summary"]["total_return"],
            }
            for f in wf["folds"]
        ]
        st.dataframe(pl.DataFrame(fold_rows).to_pandas(), hide_index=True)

with tab_signals:
    sruns = signal_runs()
    if not sruns:
        st.info("No signal runs found. Run `uv run pkmn signals ...` first.")
    else:
        sdir = st.selectbox("Signal run", sruns, format_func=lambda p: p.name)
        st.markdown((sdir / "signals.md").read_text())

with tab_prices:
    try:
        products = load_products()
        prices = load_prices()
    except FileNotFoundError:
        st.info("No warehouse found. Run `uv run pkmn ingest ...` first.")
    else:
        kind = st.radio("Kind", ["sealed", "single"], horizontal=True)
        subset = products.filter(pl.col("kind") == kind).sort("name")
        name = st.selectbox("Product", subset["name"].to_list())
        pid = int(subset.filter(pl.col("name") == name)["product_id"][0])
        history = (
            prices.filter(pl.col("product_id") == pid)
            .sort("date")
            .select("date", "sub_type", "market")
        )
        chart = history.pivot(on="sub_type", index="date", values="market")
        st.line_chart(chart.to_pandas().set_index("date"))

with tab_trades:
    ledgers = [p for p in benchmark_runs() if (p / "fills.parquet").exists()]
    if not ledgers:
        st.info("No trade ledgers found. `uv run pkmn backtest ...` writes fills.parquet.")
    else:
        ldir = st.selectbox("Run with fills", ledgers, format_func=lambda p: p.name)
        fills = pl.read_parquet(ldir / "fills.parquet").sort("day")
        st.dataframe(fills.to_pandas(), hide_index=True)
        st.caption(f"{fills.height} fills")
```

Adapt the two `load_*` paths to the REAL warehouse layout — read `src/pkmn_quant/data/warehouse.py` (or reuse `Warehouse(Paths(root=ROOT))` directly, which is cleaner if its API loads both frames; prefer `Warehouse` over raw paths if it works under `st.cache_data` — if the instance isn't hashable, keep the module-level raw-parquet reads but copy the actual glob paths from warehouse.py). If `pivot` signature differs in the installed polars, use `history.pivot(index="date", columns="sub_type", values="market")` (older API).

- [ ] **Step 3: Gates** — `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest` (mypy ignores app/ by config; ruff must pass on it).

- [ ] **Step 4: Manual smoke test** — from the repo root: `uv run --group dashboard streamlit run app/dashboard.py --server.headless true`, open the URL, check all three tabs render against the real `data/results/` artifacts (wf runs exist from Plan 3 Task 10; run `pkmn signals` once first if the Signals tab should show content). Optionally save a screenshot to `docs/img/dashboard.png` (create the directory) for the README. STOP and report if any tab errors.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock app/dashboard.py
git commit -m "feat: streamlit results dashboard"
```

---

### Task 7: README

**Files:**
- Create: `README.md` (repo root; replaces any stub — check `git status` for an existing one first and merge its content if present)

- [ ] **Step 1: Write README.md.** Use exactly this structure; numbers come from `docs/research-findings-2026-07.md` and must match it:

```markdown
# pkmn_quant — an event-driven backtester for Pokemon card markets

A quant research system for TCGplayer card prices: custom event-driven
backtest engine with realistic card-market execution costs, three
parameterized strategies, optuna walk-forward validation, live signal
generation, and a Streamlit results explorer. Python 3.12, polars,
strict mypy, 150+ tests, CI.

**The honest headline:** across 2024-08 → 2026-06, none of the three active
strategies beat buy-and-hold sealed product (+151% out-of-sample). The
system's value is that it can prove that honestly — walk-forward
out-of-sample testing, transaction-cost realism, and an explicit
overfitting measurement.

## Results (walk-forward, out-of-sample only)

| Strategy            | Stitched OOS total | Mean OOS CAGR | Overfitting gap |
|---------------------|-------------------:|--------------:|----------------:|
| buy-and-hold sealed | **+151.1%**        | —             | —               |
| sealed-accumulation | +13.6%             | +8.7%         | +4.8 pts        |
| xs-momentum         | −11.0%             | −4.1%         | +4.7 pts        |
| dip-buyer           | −9.3%              | −5.0%         | +0.3 pts        |

11 folds each: optimize 180 days in-sample, freeze params, test 60 days
out-of-sample, roll, stitch the OOS segments. The overfitting gap
(mean IS CAGR − mean OOS CAGR) is reported on every run. Full findings and
caveats: [docs/research-findings-2026-07.md](docs/research-findings-2026-07.md).

## Why the numbers are believable

- **No look-ahead by construction:** strategies receive a `Context` (history
  up to today, positions, cash) and cannot tell backtest from live mode.
- **Card-market execution realism:** T+1 fills, ~12.75% sell fees + shipping,
  integer quantities, per-day liquidity caps tiered by price, no shorting.
  Round-trip friction is ~15% — most naive strategies lose to it, and the
  reports say so.
- **Walk-forward only:** the headline equity curve contains zero in-sample
  days. Parameters are chosen by seeded optuna on each in-sample window and
  frozen before touching out-of-sample data.
- **Stated limitations:** ~2.4 years of data, one bull regime for sealed;
  Sharpe/Sortino inflated by thin-market mark smoothing (documented in every
  report); stitched seams assume mark-value carryover without liquidation
  costs. Methodology over significance.

## Architecture

    tcgcsv.com daily archives
        │  pkmn ingest (quality gates -> quarantine, never silent drops)
        ▼
    Parquet warehouse (DuckDB-queryable)          src/pkmn_quant/data/
        │
        ▼
    Event-driven engine: daily bars -> Context    src/pkmn_quant/engine/
    -> Strategy.on_bar -> orders -> T+1 fill
    simulator -> portfolio -> metrics
        │
        ├── strategies/  sealed_accumulation, dip_buyer, momentum, buy_and_hold
        ├── research/    folds -> seeded optuna search -> walk-forward
        │                runner/stitcher -> registry -> reports + artifacts
        └── live/        pkmn signals: same Strategy, latest data,
                         recommendations with the strategy's OOS record

## Quickstart

    uv sync
    uv run pytest                                        # 150+ tests
    uv run pkmn ingest --start 2024-02-08 --end 2026-06-30   # ~40 min, ~2.9M rows
    uv run pkmn backtest --start 2024-03-01 --end 2026-06-30 # benchmark
    uv run pkmn walkforward --strategy sealed-accumulation \
        --start 2024-03-01 --end 2026-06-30 --trials 15      # minutes
    uv run pkmn signals --strategy sealed-accumulation       # today's entries
    uv run --group dashboard streamlit run app/dashboard.py  # explorer

## Engineering

- `uv` everything; `ruff` lint+format; `mypy --strict` on `src/`; pytest.
- Golden regression test pins exact engine numbers; CI (`uv sync --frozen`)
  fails loudly on any drift.
- Frozen dataclasses for value objects; the cost model is serialized into
  every result so each report states its own assumptions.

## Future work

Scheduled ingestion + signals (cron/Actions), position tracking in live mode,
multi-marketplace data (eBay, PSA-graded), ML strategies, Docker.
```

Replace "150+ tests" with the real `uv run pytest` count at time of writing (run it; if under 150, state the exact number). Verify every results number against `docs/research-findings-2026-07.md` character by character. If `docs/img/dashboard.png` was captured in Task 6, add `![dashboard](docs/img/dashboard.png)` under the Results table.

- [ ] **Step 2: Gates** (README is prose but run the four gates anyway — ruff format checks markdown code fences are left alone; pytest confirms the quoted test count).

- [ ] **Step 3: Commit**

```bash
git add README.md docs/img/ 2>/dev/null; git add README.md
git commit -m "docs: README for the 90-second skim"
```

---

### Task 8: Real-data verification + status updates (manual)

- [ ] **Step 1:** Real-data smoke of the new surface, from the repo root:

```bash
uv run pkmn signals --strategy sealed-accumulation --cash 10000
uv run pkmn signals --strategy dip-buyer --cash 10000
uv run pkmn signals --strategy xs-momentum --cash 10000
```

Expected: exit 0 for all three (Plan 3's wf runs provide the artifacts — BUT note those runs predate `walkforward.json`; if signals errors with "no walk-forward run found", re-run the three `pkmn walkforward` commands from Plan 3 Task 10 first to regenerate artifacts WITH the json, ~15-25 min total, then retry). Sanity-check the emitted recommendations: prices should match current warehouse marks; quantities consistent with budget_frac/cash; no SELLs (positions are empty).

- [ ] **Step 2:** Sanity-check one `signals.json` by hand (open it; confirm wf_summary matches the strategy's report.md summary).

- [ ] **Step 3:** Update `CLAUDE.md`: status section → "Plans 1-4 merged" with new test count, add `pkmn signals` + dashboard commands to the Commands block, add `src/pkmn_quant/live/` and `app/dashboard.py` to Layout. Commit:

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md status for Plan 4 completion"
```

**Done criteria (Plan 4):** all gates green; `pkmn signals` produces markdown + JSON with the strategy's OOS record attached on real data; dashboard renders walk-forward, signals, and price tabs against real artifacts; README quotes the findings verbatim and survives a 90-second skim; CLAUDE.md current.
