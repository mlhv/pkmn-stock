# pkmn_quant Plan 7: Paper Dashboard View + Honest Fill Counts + paper.py

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The dashboard's Portfolio tab grows a Real/Paper ledger toggle; `daily.json`'s `n_buys`/`n_sells` report recorded fills (not recommendations) in paper mode; the paper-fill loop moves out of `cli.py` into a pure, unit-tested `live/paper.py`. Spec: `docs/superpowers/specs/2026-07-11-paper-dashboard-cleanup-design.md`.

**Architecture:** `plan_paper_fills(recommendations, cash, day, costs)` is a pure function returning the batch of ledger event dicts — planning is separated from persistence (`append_events` stays in cli.py), so the honest count is just counting the batch. The dashboard toggle only changes which `ledger_path` feeds the existing replay → snapshot → chart pipeline. `ledger.py` gets public `load_events`/`replay` so the dashboard stops calling underscore functions.

**Tech Stack:** existing stack only (polars, typer, streamlit in the opt-in `dashboard` dependency group). No new dependencies.

**Prerequisite:** Plan 6 merged (done, `d365f47`). Work on a new branch `feat/paper-dashboard-cleanup` off `main`. No engine, strategy, or golden changes anywhere in this plan — if `tests/test_cli_backtest.py` fails, you broke something.

**Key facts you need (verified against the code, 2026-07-11):**
- `CostModel` defaults: `fee_rate=0.1275`, `shipping_per_line=1.0`, liquidity tiers `((5.0, 20), (50.0, 8), (200.0, 3))`, fallback 1. `max_daily_qty` uses strict `<`: mark 100 → cap **3** (100 < 200), mark 10 → cap 8, mark 3 → cap 20.
- `Recommendation` (in `live/signals.py`): `action, product_id, sub_type, name, quantity, market_price, notional, avg_cost=None, gain_pct=None`. `SignalReport`: `as_of, strategy, params, wf_summary, wf_run_dir, recommendations, portfolio_snapshot=None, paper=False`.
- `cli.py`'s `daily` imports `generate_signals` *inside* the function body, so `monkeypatch.setattr(pkmn_quant.live.signals, "generate_signals", ...)` takes effect.
- The dashboard (`app/dashboard.py`) is NOT covered by mypy (`files=["src"]`) and streamlit is NOT installed by default `uv sync` — dashboard tests must `pytest.importorskip` and are run explicitly with `uv run --group dashboard pytest`.
- Existing real-mode count tests (`tests/test_cli_daily.py`) keep their meaning — only paper-mode counts change semantics, and no existing test asserts paper-mode `n_buys`.

---

### Task 1: `live/paper.py` — pure fill planner

**Files:**
- Create: `src/pkmn_quant/live/paper.py`
- Create: `tests/live/test_paper.py`

- [ ] **Step 1: Write the failing tests** — `tests/live/test_paper.py`:

```python
"""plan_paper_fills is pure: recommendations + cash in, ledger event dicts out."""

from datetime import date

from pkmn_quant.engine.costs import CostModel
from pkmn_quant.live.paper import plan_paper_fills
from pkmn_quant.live.signals import Recommendation

DAY = date(2026, 7, 11)


def _buy(qty: int, mark: float) -> Recommendation:
    return Recommendation(
        action="BUY", product_id=1, sub_type="Normal", name="X",
        quantity=qty, market_price=mark, notional=qty * mark,
    )


def _sell(qty: int, mark: float) -> Recommendation:
    return Recommendation(
        action="SELL", product_id=2, sub_type="Normal", name="Y",
        quantity=qty, market_price=mark, notional=qty * mark, avg_cost=50.0,
    )


def test_sell_proceeds_fund_later_buys() -> None:
    """cash 0: the BUY is only affordable because the SELL lands first.
    Sell 2 @ 100: proceeds = 2*100*(1-0.1275) - 1 = 173.50.
    Buy 1 @ 150: affordable = floor((173.50 - 1) / 150) = 1 -> fills."""
    batch = plan_paper_fills([_sell(2, 100.0), _buy(1, 150.0)], 0.0, DAY, CostModel())
    assert [e["kind"] for e in batch] == ["sell", "buy"]
    assert batch[1]["qty"] == 1


def test_buy_dropped_without_prior_sell() -> None:
    """Same BUY, no SELL, cash 0: affordable = floor(-1/150) clamps to 0 -> dropped."""
    assert plan_paper_fills([_buy(1, 150.0)], 0.0, DAY, CostModel()) == []


def test_liquidity_cap_clips_buy() -> None:
    """Mark 100 sits in the (200.0, 3) tier -> cap 3, even with ample cash."""
    [event] = plan_paper_fills([_buy(10, 100.0)], 10_000.0, DAY, CostModel())
    assert event["qty"] == 3


def test_liquidity_cap_clips_sell() -> None:
    [event] = plan_paper_fills([_sell(10, 100.0)], 0.0, DAY, CostModel())
    assert event["qty"] == 3


def test_unaffordable_buy_clips_to_zero_and_drops() -> None:
    """cash 50 < 100 + shipping: floor((50-1)/100) = 0 -> no event at all.
    This is the case daily.json used to miscount as a recorded buy."""
    assert plan_paper_fills([_buy(1, 100.0)], 50.0, DAY, CostModel()) == []


def test_empty_recommendations_empty_batch() -> None:
    assert plan_paper_fills([], 1_000.0, DAY, CostModel()) == []


def test_event_shape_and_fee_arithmetic() -> None:
    """Buy fees = shipping only; sell fees = qty*mark*fee_rate + shipping.
    Sell 2 @ 100 -> fees = 2*100*0.1275 + 1 = 26.50. Dates = the run day."""
    batch = plan_paper_fills([_sell(2, 100.0), _buy(1, 10.0)], 0.0, DAY, CostModel())
    sell, buy = batch
    assert sell == {
        "date": "2026-07-11", "kind": "sell", "product_id": 2, "sub_type": "Normal",
        "qty": 2, "price": 100.0, "fees": 26.5,
    }
    assert buy["fees"] == 1.0
    assert buy["date"] == "2026-07-11"
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/live/test_paper.py -q` → ModuleNotFoundError: `pkmn_quant.live.paper`.

- [ ] **Step 3: Implement** — `src/pkmn_quant/live/paper.py`. The logic moves VERBATIM from `cli.py`'s `daily` paper block (cli.py is untouched until Task 2):

```python
"""Paper-trading fill planner: pure arithmetic, no I/O.

Turns a signal report's recommendations into the batch of ledger event
dicts that `append_events` can write. Mirrors the backtest executor's
clipping: sells capped by the liquidity tier; buys capped by liquidity
AND by what running cash affords after shipping is reserved.

Recommendations are walked in order (strategies emit sells before buys),
so sell proceeds top up cash before any buy is sized. Recommendations
that clip to zero quantity produce no event — counting the returned
batch is therefore the honest "what was actually recorded" number.

Fills are dated *day* (the run date), not the report's as_of: the ledger
is chronological in event time, and as_of marks can predate deposits,
which would sort fills before the deposit and fail replay.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date

from pkmn_quant.engine.costs import CostModel
from pkmn_quant.live.signals import Recommendation


def plan_paper_fills(
    recommendations: Sequence[Recommendation],
    cash: float,
    day: date,
    costs: CostModel,
) -> list[dict[str, object]]:
    cash_remaining = cash
    batch: list[dict[str, object]] = []
    for rec in recommendations:
        mark = rec.market_price
        cap = costs.max_daily_qty(mark)
        if rec.action == "SELL":
            # Clip to liquidity cap; rec.quantity already equals held qty.
            qty = min(rec.quantity, cap)
            if qty <= 0:
                continue
            fees = round(qty * mark * costs.fee_rate + costs.shipping_per_line, 2)
            cash_remaining += qty * mark * (1 - costs.fee_rate) - costs.shipping_per_line
        else:  # BUY
            # Mirror executor _fill_buy: clip to liquidity cap, then to
            # what cash_remaining can afford after shipping is reserved.
            affordable = math.floor((cash_remaining - costs.shipping_per_line) / mark)
            qty = min(rec.quantity, cap, max(affordable, 0))
            if qty <= 0:
                continue
            fees = costs.shipping_per_line
            cash_remaining -= qty * mark + costs.shipping_per_line
        batch.append(
            {
                "date": day.isoformat(),
                "kind": rec.action.lower(),
                "product_id": rec.product_id,
                "sub_type": rec.sub_type,
                "qty": qty,
                "price": mark,
                "fees": fees,
            }
        )
    return batch
```

- [ ] **Step 4: Run tests, then all four gates:**

```bash
uv run pytest tests/live/test_paper.py -q
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

- [ ] **Step 5: Commit**

```bash
git add src/pkmn_quant/live/paper.py tests/live/test_paper.py
git commit -m "feat: plan_paper_fills — pure paper-fill planner in live/paper.py"
```

---

### Task 2: cli.py uses the planner; paper counts = recorded fills

**Files:**
- Modify: `src/pkmn_quant/cli.py` (the `daily` command's paper block, ~lines 537-600)
- Test: extend `tests/test_cli_paper.py`

- [ ] **Step 1: Write the failing test.** Append to `tests/test_cli_paper.py` (it already imports `json`, `Path`, `pytest`, `CliRunner`, `notify`, `app`, and `seed`/`run_walkforward` from `tests.test_cli_daily` — add `from datetime import date` and the signals imports below):

```python
def test_paper_n_buys_counts_recorded_fills_not_recommendations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A BUY recommendation the paper cash cannot afford records ZERO fills;
    daily.json must say n_buys == 0 and no notification may fire.

    Fails on pre-fix code, which counted recommendations (n_buys == 1, one
    notification). generate_signals is monkeypatched with a fabricated
    report so the unaffordable recommendation is deterministic (no optuna).
    daily() imports generate_signals at call time, so patching the module
    attribute works."""
    import pkmn_quant.live.signals as signals_mod
    from pkmn_quant.live.signals import Recommendation, SignalReport

    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(notify, "send_notification", lambda t, b: sent.append((t, b)))
    seed(tmp_path)
    runner = CliRunner()
    r = runner.invoke(
        app,
        ["portfolio", "deposit", "--amount", "100", "--date", "2025-01-02",
         "--paper", "--root", str(tmp_path)],
    )
    assert r.exit_code == 0, r.output

    def fake_generate_signals(**kwargs: object) -> SignalReport:
        return SignalReport(
            as_of=date(2025, 5, 1),
            strategy="sealed-accumulation",
            params={},
            wf_summary={},
            wf_run_dir="wf-fake",
            recommendations=[
                Recommendation(
                    action="BUY", product_id=1, sub_type="Normal", name="Crashed Box",
                    quantity=1, market_price=5000.0, notional=5000.0,
                )
            ],
            paper=True,
        )

    monkeypatch.setattr(signals_mod, "generate_signals", fake_generate_signals)
    result = runner.invoke(app, ["daily", "--skip-ingest", "--paper", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output

    meta = json.loads(
        next((tmp_path / "data" / "results").glob("daily-*-paper/daily.json")).read_text()
    )
    assert meta["n_buys"] == 0 and meta["n_sells"] == 0
    assert sent == []  # nothing recorded -> nothing to announce
    # Ledger still holds only the deposit line.
    paper = tmp_path / "data" / "portfolio" / "paper.jsonl"
    assert len(paper.read_text().strip().splitlines()) == 1
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_cli_paper.py -q` → the new test fails with `n_buys == 1` and one notification (pre-fix recommendation counting).

- [ ] **Step 3: Implement.** In `src/pkmn_quant/cli.py`, replace the entire paper auto-record block (from the `# Paper mode: auto-record recommended fills...` comment through the `except LedgerError` clause) with:

```python
    # Paper mode: auto-record recommended fills through the pure planner
    # (live/paper.py) and one atomic append_events write. A mid-batch
    # validation failure leaves the ledger completely unchanged. In paper
    # mode n_buys/n_sells are re-counted from the recorded batch: the
    # planner clips to liquidity and affordability (sometimes to zero),
    # and daily.json must report what actually happened, not what the
    # strategy wished for.
    if paper and report.recommendations:
        from pkmn_quant.engine.costs import CostModel
        from pkmn_quant.live.ledger import append_events
        from pkmn_quant.live.paper import plan_paper_fills

        batch = plan_paper_fills(report.recommendations, pf.cash, today, CostModel())
        if batch:
            try:
                append_events(lpath, batch, products)
            except LedgerError as exc:
                _fail(f"paper auto-record failed: {exc}", keep_artifacts=True)
                return  # unreachable
        n_buys = sum(1 for e in batch if e["kind"] == "buy")
        n_sells = sum(1 for e in batch if e["kind"] == "sell")
```

Then remove the now-unused pieces of `daily`: the `import math` at the top of the function body (ruff flags it if nothing else uses it) — `CostModel` and `append_events` imports move into the block above. The pre-existing `n_buys = sum(... report.recommendations ...)` lines above the block stay: they remain the real-mode (and paper-no-recommendations) counts; the paper block overwrites them.

- [ ] **Step 4: Collateral check + all four gates.** Existing tests must pass unchanged: `tests/test_cli_daily.py` counts are real-mode (semantics unchanged); `tests/test_cli_paper.py`'s two Plan-5 tests assert recorded events and fee formulas, which the verbatim planner reproduces. Then:

```bash
uv run pytest tests/test_cli_paper.py tests/test_cli_daily.py -q
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

- [ ] **Step 5: Commit**

```bash
git add src/pkmn_quant/cli.py tests/test_cli_paper.py
git commit -m "feat: paper daily counts recorded fills, not recommendations"
```

---

### Task 3: public ledger API — `load_events` / `replay`

**Files:**
- Modify: `src/pkmn_quant/live/ledger.py`
- Test: extend `tests/live/test_ledger.py`

- [ ] **Step 1: Write the failing tests.** Append to `tests/live/test_ledger.py` (uses its existing `write_lines` + `PRODUCTS` helpers; check the exact helper names in the file first and match):

```python
def test_load_events_missing_file_is_empty(tmp_path: Path) -> None:
    assert load_events(tmp_path / "nope.jsonl") == []


def test_load_events_then_replay_matches_load_portfolio(tmp_path: Path) -> None:
    """The public two-step API is exactly load_portfolio, decomposed —
    the dashboard needs the events list for its day-by-day equity chart."""
    path = tmp_path / "ledger.jsonl"
    write_lines(
        path,
        [
            '{"date": "2026-07-01", "kind": "deposit", "amount": 1000.0}',
            '{"date": "2026-07-03", "kind": "buy", "product_id": 1, "sub_type": "Normal",'
            ' "qty": 2, "price": 100.0, "fees": 1.0}',
        ],
    )
    events = load_events(path)
    assert [e.kind for e in events] == ["deposit", "buy"]
    pf = replay(events, PRODUCTS)
    ref = load_portfolio(path, PRODUCTS)
    assert pf.cash == ref.cash and pf.positions == ref.positions
```

Add `load_events`, `replay` to the test file's `ledger` imports.

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/live/test_ledger.py -q` → ImportError.

- [ ] **Step 3: Implement.** In `ledger.py`:

1. Rename `_replay` → `replay` (public; docstring: "Replay parsed events into a Portfolio. Raises LedgerError, naming the offending line, on invalid sequences."). Update its three internal callers (`load_portfolio`, `append_event`, `append_events`).
2. Add:

```python
def load_events(path: Path) -> list[LedgerEvent]:
    """Parse the ledger file into date-sorted events. Missing file = []."""
    if not path.exists():
        return []
    return _parse_lines(path.read_text().splitlines())
```

3. Simplify `load_portfolio` to `return replay(load_events(path), products)` (behavior identical: `replay([])` is an empty portfolio).
4. `_parse_lines` stays private — `append_event`/`append_events` still use it on candidate line lists that exist only in memory.

- [ ] **Step 4: Run tests, all four gates:**

```bash
uv run pytest tests/live/test_ledger.py -q
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

- [ ] **Step 5: Commit**

```bash
git add src/pkmn_quant/live/ledger.py tests/live/test_ledger.py
git commit -m "feat: public ledger API — load_events + replay (dashboard stops using privates)"
```

---

### Task 4: dashboard ledger toggle + smoke tests

**Files:**
- Modify: `app/dashboard.py` (Portfolio tab, ~lines 199-272)
- Create: `tests/test_dashboard.py`

- [ ] **Step 1: Write the failing tests** — `tests/test_dashboard.py`. Streamlit is an opt-in dependency group, so the whole module skips when it is absent (this keeps `uv run pytest` and CI green without the group):

```python
"""Headless dashboard smoke tests (streamlit.testing.v1.AppTest).

Run with the dashboard group: uv run --group dashboard pytest tests/test_dashboard.py
Under plain `uv run pytest` these skip (streamlit not installed).
"""

from pathlib import Path

import pytest

from tests.test_cli_daily import seed

apptest_mod = pytest.importorskip("streamlit.testing.v1", reason="dashboard group not installed")
AppTest = apptest_mod.AppTest

DASHBOARD = str(Path(__file__).resolve().parents[1] / "app" / "dashboard.py")


def _write_paper_ledger(root: Path) -> None:
    p = root / "data" / "portfolio" / "paper.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        '{"date": "2025-01-02", "kind": "deposit", "amount": 1000.0}\n'
        '{"date": "2025-01-03", "kind": "buy", "product_id": 1, "sub_type": "Normal",'
        ' "qty": 2, "price": 35.0, "fees": 1.0}\n'
    )


def test_paper_toggle_renders_paper_holdings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Toggle to Paper: the seeded paper position renders without error."""
    seed(tmp_path)  # warehouse: product 1 "Crashed Box", prices through 2025-05-01
    _write_paper_ledger(tmp_path)
    monkeypatch.chdir(tmp_path)  # dashboard resolves ROOT = Path(".") against cwd
    at = AppTest.from_file(DASHBOARD, default_timeout=30).run()
    assert not at.exception
    [ledger_radio] = [r for r in at.radio if r.label == "Ledger"]
    at = ledger_radio.set_value("Paper").run()
    assert not at.exception
    assert any("Crashed Box" in str(df.value) for df in at.dataframe)


def test_paper_empty_state_shows_deposit_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No paper ledger: toggling to Paper shows the --paper hint, no crash."""
    seed(tmp_path)
    monkeypatch.chdir(tmp_path)
    at = AppTest.from_file(DASHBOARD, default_timeout=30).run()
    [ledger_radio] = [r for r in at.radio if r.label == "Ledger"]
    at = ledger_radio.set_value("Paper").run()
    assert not at.exception
    assert any("--paper" in str(i.value) for i in at.info)
```

- [ ] **Step 2: Run to verify failure** — `uv run --group dashboard pytest tests/test_dashboard.py -q` → fails: no radio labeled "Ledger" exists yet. Also confirm `uv run pytest tests/test_dashboard.py -q` reports the tests as SKIPPED (not errored) without the group.

- [ ] **Step 3: Implement.** In `app/dashboard.py`, inside `with tab_portfolio:` — the alerts strip stays untouched; the ledger section below it changes:

1. Replace `lp = ledger_path(ROOT)` with:

```python
    use_paper = st.radio("Ledger", ["Real", "Paper"], horizontal=True) == "Paper"
    lp = ledger_path(ROOT, paper=use_paper)
```

2. Make the empty state mode-aware — replace the `st.info("No ledger yet. ...")` line with:

```python
        st.info(
            "No paper ledger yet. Start one with "
            "`uv run pkmn portfolio deposit --paper --amount ...`."
            if use_paper
            else "No ledger yet. Record trades with `uv run pkmn portfolio buy ...`."
        )
```

3. Swap the private calls for the Task 3 public API:
   - `events = ledger_mod._parse_lines(lp.read_text().splitlines())` → `events = ledger_mod.load_events(lp)`
   - both `ledger_mod._replay(...)` calls → `ledger_mod.replay(...)` (snapshot build and the day-by-day equity loop).

Nothing else in the tab changes — metrics, positions table, and equity chart already operate on whatever `lp`/`events` provide.

- [ ] **Step 4: Run tests, all four gates plus the dashboard group:**

```bash
uv run --group dashboard pytest tests/test_dashboard.py -q
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

(Plain `uv run pytest` must show the two dashboard tests as skipped.)

- [ ] **Step 5: Commit**

```bash
git add app/dashboard.py tests/test_dashboard.py
git commit -m "feat: dashboard Portfolio tab gains Real/Paper ledger toggle"
```

---

### Task 5: docs

**Files:**
- Modify: `README.md`, `CLAUDE.md`

- [ ] **Step 1: README.** In the paper-trading/dashboard paragraphs: note that the dashboard's Portfolio tab now shows either ledger via a Real/Paper toggle, and that paper daily runs report recorded fills (honest counts after liquidity/affordability clipping). Style rule for any new README sentence: no em dashes; use commas, colons, or parentheses.

- [ ] **Step 2: CLAUDE.md.** Status: Plan 7 complete; update the test count to the real number from the final `uv run pytest` (count it, do not guess); add `live/paper.py` to the Layout section; note dashboard tests live in `tests/test_dashboard.py` and run via `uv run --group dashboard pytest`.

- [ ] **Step 3: Gates, then commit:**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
git add README.md CLAUDE.md
git commit -m "docs: Plan 7 — paper dashboard view, honest fill counts, live/paper.py"
```

---

**Done criteria (Plan 7):** all four gates green and goldens untouched; `plan_paper_fills` pure and unit-tested (clip-to-zero case covered); paper-mode `daily.json` counts recorded fills, proven by a regression test that fails on pre-fix code; real-mode counts unchanged; dashboard Portfolio tab toggles Real/Paper with mode-aware empty states and no private ledger calls; dashboard smoke tests pass under `--group dashboard` and skip cleanly without it; README/CLAUDE.md current.
