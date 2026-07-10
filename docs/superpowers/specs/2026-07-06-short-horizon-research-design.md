# Plan 6 design: short-horizon research (opened_on + cost-aware strategies)

**Goal:** make the live loop useful for short-horizon trades (buy a dipped
card, sell in 1–6 months). Two legs: (1) the engine records when each
position was opened, so hold-day exit rules work identically in backtests
and against the real ledger; (2) a new cost-aware mean-reversion strategy
whose entries must clear the round-trip cost hurdle. Dip-buyer and
xs-momentum leave the portfolio-mode blocklist.

**Depends on:** Plan 5 (reinvest loop) merged — ledger, portfolio-mode
signals, `PORTFOLIO_SAFE_STRATEGIES`, `pkmn daily`.

**Success criterion (user's words):** a practical tool for short-term
flips. It does not need to beat buy-and-hold sealed long-term; the findings
doc reports whatever the walk-forwards say, honestly.

## Core decision: entry dates live in the engine, not in strategies

`Position` (mutable dataclass, `engine/portfolio.py`) gains
`opened_on: date | None = None`:

- `Portfolio._buy` sets `opened_on = fill.day` when it CREATES a position.
- Adding to an existing position keeps the original date (age = oldest
  continuous holding — conservative for time-based exits).
- Partial sells leave it unchanged; a full close removes the position, so
  re-opening records a fresh date.
- No accounting math changes. The golden regression test
  (`tests/test_cli_backtest.py`) must stay **byte-identical**; any drift is
  a bug, not a golden update.

Because the ledger stores real trade dates and replays through
`Portfolio.apply` (Plan 5 Task 1), live portfolios get authentic
`opened_on` with no new code. The signals trust-boundary copy (Plan 5
Task 3) must carry `opened_on` into the strategy's `Context`.

Strategies that need `opened_on` raise `ValueError` on `None` (loud, per
project style). `None` is only reachable from hand-built test portfolios;
engine fills and ledger replay always set it.

## Strategy retrofits

**dip-buyer** drops its `_entries: dict[Asset, date]` state. The hold-day
exit becomes `(ctx.today - pos.opened_on).days >= hold_days`. This fixes
two imprecisions documented in its own docstring:

- the hold clock now starts at the actual T+1 fill, not order emission;
- a partially-filled position keeps a real entry date instead of being
  treated as overdue and re-sold every bar.

Consequence: dip-buyer backtest numbers shift legitimately. Its
walk-forward is re-run and the change explained in the findings doc.

**xs-momentum** drops `_last_rebalance`. Its state is a portfolio-level
rebalance clock, not per-position dates, so the retrofit derives it:
rebalance is due when `(ctx.today - max(opened_on of held positions)).days
>= rebalance_days`, and immediately when flat. Rationale: the last buy date
is the last rebalance date — reconstructible from the ledger, so a single
live bar behaves like the backtest bar. Same consequence: numbers shift,
walk-forward re-run.

Both strategies become near-stateless (`reset()` trivial).

## New strategy: cost-aware-reversion

The user's use case: buy what's down, sell fast. Long-only, both singles
and sealed above `min_price`.

**Entry** (all must hold, and the round-trip cost hurdle is the point):

- `dip = 1 - price / window_high` over `dip_window_days` satisfies
  `dip >= dip_threshold`;
- expected gross rebound clears costs with margin:
  `window_high / price - 1 >= cost_hurdle(price) + min_edge`, where
  `cost_hurdle(price) = fee_rate + 2 * shipping_per_line / price`
  (conservative single-unit round trip);
- respects `max_positions` and `budget_frac` sizing, matching house
  strategy conventions.

The strategy holds a `CostModel` (constructor arg, default `CostModel()`)
rather than duplicating fee constants; `Context` is unchanged.

**Exit:** `mark >= avg_cost * take_profit`, or
`(ctx.today - opened_on).days >= max_hold_days`.

**Search space (optuna):** `dip_window_days`, `dip_threshold`, `min_edge`,
`take_profit`, `max_hold_days` (30–180 — the 1–6 month window),
`max_positions`, `budget_frac`. Registered in the research registry like
the existing strategies.

## Live wiring

`PORTFOLIO_SAFE_STRATEGIES` grows to all four strategies once retrofitted.
The mechanism (frozenset + clean rejection error) stays. Plan 5 tests that
assert dip-buyer is REJECTED in portfolio mode flip to assert it works —
a deliberate, called-out change, not a silent test edit. Paper mode records
dated fills, so hold-day exits work there unchanged.

## Research runs and reporting

Walk-forward all three changed/new strategies over the full ingested span
(2024-03 → 2026-06), same protocol as Plan 3/4 (seeded optuna, stitched
OOS, artifacts). Compare against buy-and-hold sealed and
sealed-accumulation. Update `docs/research-findings-2026-07.md` and the
README: what changed in dip-buyer/xs-momentum and why, how
cost-aware-reversion performed, with the standard mark-smoothing caveats.
Negative results are reported as such.

## Testing

- Engine: hand-derived `opened_on` lifecycle tests (open, add-to, partial
  sell, close/reopen) in `tests/engine/`; golden byte-identity.
- Strategies: hand-derived entry/exit scenarios; cost-hurdle arithmetic
  worked in docstrings (house convention).
- Live: end-to-end — a ledger position aged past `max_hold_days` makes
  `pkmn signals --portfolio` emit the time-based SELL.

## Out of scope

New data sources (eBay, PSA), ML strategies, tax lots, automated order
placement, multiple named portfolios. `Position.opened_on` migration for
old serialized artifacts (none store positions).

## Decisions log

- Engine-first (`opened_on` in `Position`) over live-side reconstruction:
  live and backtest must run the same exit code path — Plan 5's core
  promise. Reconstructing dates only in signals would fork the semantics.
- Age of an added-to position = first fill of the continuous holding
  (conservative: positions look older, exits fire sooner).
- xs-momentum rebalance clock from `max(opened_on)`: reconstructible from
  the ledger; behavior shift is acceptable and re-walk-forwarded.
- Strategy holds a `CostModel` instead of `Context` gaining one: smaller
  blast radius; Context stays a pure market/portfolio view.
- One new strategy, not three: the cost hurdle is the idea worth testing;
  more variants risk overfitting by iteration.
- `None` `opened_on` raises rather than guessing: silent "treat as fresh"
  would suppress exits; silent "treat as overdue" is the dip-buyer bug this
  plan removes.
