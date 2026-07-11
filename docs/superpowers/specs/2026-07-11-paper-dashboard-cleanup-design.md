# Paper Portfolio View + Honest Fill Counts + paper.py Extraction — Design

Date: 2026-07-11. Follows the reinvest-loop spec
(`2026-07-06-reinvest-loop-design.md`, shipped as Plan 5) and the
short-horizon research spec (`2026-07-06-short-horizon-research-design.md`,
shipped as Plan 6). This is a small housekeeping plan (Plan 7): three
backlog items from the Plan 5/6 final reviews, no engine or strategy
changes.

## Goal

Three items, chosen together because they touch the same code paths:

1. **Dashboard paper portfolio view.** The Portfolio tab renders only the
   real ledger; the user actively paper-trades (`pkmn daily --paper`, all
   four strategies since Plan 6) and cannot see the paper equity curve or
   holdings anywhere.
2. **Honest fill counts.** `daily.json`'s `n_buys`/`n_sells` (and the
   macOS notification and dashboard alert strip built on them) count
   *recommendations*. In paper mode the fill loop clips quantities to
   liquidity and affordability — sometimes to zero — so the reported
   counts can claim buys that were never recorded.
3. **Extract the paper-fill loop.** ~70 lines of fill planning live inline
   in `cli.py`'s `daily` command (cli.py is 630 lines). The logic is pure
   arithmetic and deserves its own module and unit tests.

Out of scope: ML strategies (next research plan), equity-chart performance
(the O(days × events) day-by-day replay stays demo-grade, as its own
comment says), multiple named portfolios, CI changes (`.github/workflows/
ci.yml` already runs all four gates + coverage), any engine/strategy/golden
change.

## Core decision: plan/persist seam

Approach chosen (of three considered): `live/paper.py` exports one **pure
function**; persistence stays in the caller.

```python
def plan_paper_fills(
    recommendations: Sequence[Recommendation],
    cash: float,
    day: date,
    costs: CostModel,
) -> list[dict[str, object]]: ...
```

It performs, verbatim from today's cli.py loop: the in-order walk over
recommendations (strategies emit sells before buys; sell proceeds top up
`cash` before buys are sized), the liquidity cap (`costs.max_daily_qty`),
the affordability clip on buys (floor of remaining cash after shipping),
fee arithmetic, and event-dict construction dated `day`. Recommendations
that clip to zero quantity are dropped from the batch. No I/O and no
`date.today()` inside — the function is deterministic given its arguments.

Rejected alternatives: (a) moving the loop *including* the
`append_events` write into paper.py — thinner cli.py but welds planning to
persistence, so every clipping edge case needs a tmp ledger file to test;
(b) fixing n_buys in place without extracting — cheapest today, but the
loop stays untestable inside cli.py and the extraction lands on the
backlog a third time.

Why the seam pays twice: with the batch as a return value, the honest
count is `sum(1 for e in batch if e["kind"] == "buy")` — the n_buys fix
falls out of the extraction instead of being a separate change.

## cli.py changes

The `daily` command's paper block becomes:

```python
if paper and report.recommendations:
    batch = plan_paper_fills(report.recommendations, pf.cash, today, CostModel())
    if batch:
        append_events(lpath, batch, products)   # unchanged, atomic
```

Count semantics, by mode:

- `paper=True`: `n_buys`/`n_sells` = recorded fills, counted from `batch`.
- `paper=False`: unchanged, counted from recommendations (real mode
  records nothing; recommendations are the only meaningful count).

No schema change to `daily.json`; the field meanings become mode-dependent
and the `paper` field already in the JSON disambiguates. The notification
text and the dashboard alert strip inherit honest numbers with no changes
of their own.

## Dashboard: ledger toggle in the Portfolio tab

Chosen layout (of three mocked up: toggle, separate Paper tab,
side-by-side split): a horizontal `st.radio("Ledger", ["Real", "Paper"])`
at the top of the existing Portfolio tab. The only downstream change is
`ledger_path(ROOT, paper=...)`; the metrics row, positions table, and
equity-over-time chart render identically for whichever ledger is
selected. Rationale: the rendering pipeline (replay → snapshot → chart) is
ledger-agnostic already, so the toggle reuses all of it; a separate tab
would duplicate or force-refactor that code for no user benefit.

- The daily-runs alert strip stays where it is and unchanged — it already
  covers both modes (`daily-*` glob catches `-paper` dirs, labels carry a
  PAPER prefix).
- Empty states are mode-aware: real → existing hint
  (`pkmn portfolio buy ...`); paper → `pkmn portfolio deposit --paper`.
- Toggling to an empty or absent paper ledger must not crash the equity
  chart (the empty-events guard exists; a test pins it for the paper
  path).

### Targeted cleanup: public ledger API

The dashboard currently calls private `ledger._parse_lines` and
`ledger._replay`. While touching this tab, `ledger.py` renames the
underscore functions to public `load_events(path) -> list[Event]` and
`replay(events, products) -> Portfolio` (making their event type public
too if it is currently private), and the dashboard uses them. `load_portfolio` remains the
one-shot convenience used by the CLI. No behavior change.

## Error handling

Deliberately unchanged from Plan 5 semantics:

- `append_events` stays all-or-nothing; `LedgerError` still routes to
  `_fail(keep_artifacts=True)` (signals artifacts describe
  recommendations, not fills, so they stay consistent when no fills were
  recorded).
- `plan_paper_fills` raises nothing: pure arithmetic over already-validated
  inputs; unfillable recommendations drop out of the batch silently — that
  is the clipping behavior, now honestly counted.
- Dashboard: absent ledger → `st.info` hint; corrupt ledger →
  existing `st.error` path, both per selected mode.

## Testing

- **`tests/live/test_paper.py` (new):** unit tests for `plan_paper_fills`
  — sells-first cash accounting (sell proceeds fund later buys), liquidity
  cap clip, affordability clip to zero, empty recommendations → empty
  batch, fee arithmetic matches `CostModel` (sell fees = qty·price·fee_rate
  + shipping; buy fees = shipping).
- **`tests/test_cli_daily.py` (collateral):** paper-mode count assertions
  flip to fill semantics. New regression test: a recommendation that clips
  to zero fills yields `n_buys == 0` in `daily.json` — this test must be
  demonstrated to fail against pre-fix code (Plan 5 review rule).
- **Dashboard smoke** (`streamlit.testing.v1.AppTest`, as in Plan 4): the
  toggle renders; selecting Paper shows paper holdings; empty paper ledger
  shows the hint instead of crashing.
- Goldens untouched (no engine change anywhere in this plan). All four
  gates green per task.

## Decisions log

- Scope = all three backlog items together (user decision, 2026-07-11);
  CI dropped from scope after discovering `ci.yml` already exists.
- Dashboard layout: ledger toggle inside the Portfolio tab, chosen over a
  separate Paper tab and a side-by-side split (user picked from mockups,
  2026-07-11).
- Count semantics: redefine `n_buys`/`n_sells` per mode rather than adding
  `*_filled` fields or dual-count labels (user decision, 2026-07-11).
- Extraction shape: pure planner function returning the event batch;
  persistence stays in cli.py (user decision, 2026-07-11).
