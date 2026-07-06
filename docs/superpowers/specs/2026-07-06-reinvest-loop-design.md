# Reinvest Loop (Portfolio + Daily Signals) — Design

Date: 2026-07-06. Follows the v1 spec
(`2026-06-09-pkmn-quant-design.md`), which shipped as Plans 1-4.

## Goal

Close the loop that v1 deliberately left open: `pkmn signals` answers "what
would I enter today" but forgets everything the moment you act. This project
adds a real position ledger, exit recommendations against those positions,
a scheduled daily run with notifications, and a dashboard portfolio view —
so the workflow becomes buy → hold 1-6 months → take profit → reinvest,
with the user's actual holdings driving every recommendation.

Explicitly not this project: new strategies or engine changes. A follow-up
research plan covers 1-6 month strategy hypotheses and the
`Position.opened_on` engine extension that dip-buyer/xs-momentum need for
live exits (their hold-day clocks live in strategy-internal state that a
single live bar cannot reconstruct).

## Core decision: the ledger materializes into Context

Live mode with positions is Approach 1 of three considered (separate exit
engine and full-history replay were rejected — the first drifts from the
backtested rules, breaking the "recommendation carries its honest record"
guarantee; the second is heavy and incoherent when the user trades
off-script). The ledger replays through the existing `Portfolio` class
(average-cost basis, cash, realized P&L — already tested) and the resulting
`positions` dict + cash go into the same `Context` the backtester builds.
The strategy cannot tell live from backtest — v1's central invariant —
so its own backtested exit rule (`mark >= avg_cost * take_profit` for
sealed-accumulation) emits SELL orders that become SELL recommendations.

Scope consequence: exits work for strategies whose exit rules read only
`Context`. Today that is sealed-accumulation (also the only OOS-positive
strategy). `pkmn signals --portfolio` for dip-buyer/xs-momentum must fail
with a clean error naming the limitation, not silently emit wrong exits
(dip-buyer treats unknown entries as overdue and would dump every holding).

## Ledger

`data/portfolio/ledger.jsonl` — append-only JSON Lines, gitignored,
human-readable/editable, one event per line:

```jsonl
{"date": "2026-07-01", "kind": "deposit", "amount": 2000.0}
{"date": "2026-07-03", "kind": "buy", "product_id": 619875, "sub_type": "Normal", "qty": 2, "price": 18.94, "fees": 5.20}
{"date": "2026-09-15", "kind": "sell", "product_id": 619875, "sub_type": "Normal", "qty": 2, "price": 31.00, "fees": 9.05}
{"date": "2026-09-20", "kind": "withdraw", "amount": 500.0}
```

- `price` is per-unit; `fees` is the total non-price cost of the event
  (shipping on buys; TCGplayer's cut + shipping on sells) — mirroring the
  engine's `Fill` semantics so replay is a direct translation.
- Module: `src/pkmn_quant/live/ledger.py`. Load = parse lines → sort by
  date, then file order within a date → apply deposits/withdrawals to cash
  and translate trades to `Fill`s applied to a `Portfolio`. No new
  accounting code.
- Validation on load fails loudly with the line number: malformed JSON,
  unknown kind, unknown product_id/sub_type (checked against the
  warehouse), sell quantity exceeding the position, trade dated before any
  cash exists, cash going negative (error, not warning — a ledger that
  spends money it doesn't have is mis-entered).
- The ledger is the single source of truth. Marks/valuations are never
  stored; they are always computed against the warehouse at read time.

## CLI: `pkmn portfolio`

Subcommands (typer sub-app), all appending one validated line or reading:

- `pkmn portfolio deposit --amount 2000 [--date ...]` (and `withdraw`)
- `pkmn portfolio buy --product-id N --sub-type Normal --qty 2
  --price 18.94 [--fees 0] [--date ...]` (and `sell`)
- `pkmn portfolio show` — table of positions (qty, avg cost, latest mark,
  unrealized P&L), cash, realized P&L, total equity. Dates default to
  today; entries are validated by running the full load after appending
  (append is rolled back if the resulting ledger is invalid).

## Signals with real positions

`pkmn signals --portfolio` replaces hypothetical cash + empty positions
with ledger cash + ledger positions. `--cash` combined with `--portfolio`
is a usage error. The report gains:

- a SELL section: asset, qty, avg cost, mark, gain %, and which rule fired
  (implicit: the strategy's take-profit);
- a portfolio snapshot (same numbers as `portfolio show`) so the artifact
  is self-contained.

Without `--portfolio`, behavior is byte-identical to v1 — the flag is the
only entry point to the new code.

## `pkmn daily` + scheduling + notifications

One command per morning:

1. Ingest missing days: `stored_days()[-1] + 1` .. yesterday (skip if
   current). `--skip-ingest` bypasses (tests; offline).
2. Run signals `--portfolio` for `--strategy` (default sealed-accumulation).
3. Write `data/results/daily-YYYY-MM-DD/` containing the signals artifacts
   plus `daily.json`: `{date, strategy, status: ok|error, error: str|null,
   n_buys, n_sells, as_of}`.
4. Notify via `osascript -e 'display notification ...'` only when
   actionable (n_buys + n_sells > 0) or on failure. Silence = nothing to
   do. Ingest failure still attempts signals on existing data, reports
   status error, exits nonzero.

Scheduling: `scripts/com.pkmn-quant.daily.plist` (launchd agent template,
absolute paths documented) + README/CLAUDE.md install instructions
(`launchctl load`). GitHub Actions later = the same command in a workflow.

## Dashboard

- New **Portfolio** tab: positions table, cash, realized/unrealized P&L,
  and an equity-over-time line chart (ledger events + historical marks —
  computed on the fly, not stored).
- Alerts strip at the top of the Portfolio tab: recent
  `daily.json` files, newest first; actionable days badged with buy/sell
  counts, errors flagged red; the day's signals.md expandable inline.

## Paper trading (stretch — build only if the core lands easily)

`--paper` on `portfolio`, `signals`, and `daily` redirects every read/write
to `data/portfolio/paper.jsonl`; on `signals` and `daily` it implies
`--portfolio`. `pkmn daily --paper` additionally
auto-appends the day's recommendations as fills at the mark with the
engine's `CostModel` spread/fees applied, and labels every surface
(reports, notifications, dashboard) PAPER. Known, documented optimism vs
the backtester: fills are same-day at mark rather than T+1. Same code
path, different file — no second implementation.

## Error handling

Every failure a scheduled run can hit produces a clean message and, from
`daily`, a notification: ledger validation errors (with line numbers),
missing walk-forward artifact (existing SignalsError), warehouse missing,
tcgcsv unreachable, unsupported strategy with `--portfolio`. The loop
never dies silently.

## Testing

- Ledger: round-trip, replay math vs hand-derived avg-cost/P&L numbers,
  every validation error path.
- `portfolio` CLI: append + rollback-on-invalid, `show` against seeded
  warehouse marks.
- End-to-end exit: seeded warehouse + walkforward artifact + ledger buy →
  raise the mark past `avg_cost * take_profit` → `signals --portfolio`
  emits the SELL with correct gain.
- `daily --skip-ingest` end-to-end: artifacts + daily.json written,
  actionable flag correct; notification layer stubbed (the osascript call
  is injected/patchable — never executed in tests).
- Engine untouched; golden regression byte-identical. All four gates.

## Out of scope

New strategies; `Position.opened_on` and dip-buyer/momentum live exits
(research plan); GitHub Actions scheduling; email notifications; multiple
named portfolios (one real + one paper is enough); tax lot accounting
(average cost only, matching the engine).

## Decisions log

- Ledger is manual-entry only; paper mode is the only auto-fill path
  (user decision, 2026-07-06).
- Local launchd over GitHub Actions for cadence; Actions later
  (user decision, 2026-07-06).
- macOS notifications + dashboard surfacing; email later if needed
  (user decision, 2026-07-06).
- Exits via strategy `on_bar` against materialized positions, not a
  separate exit engine (Approach 1; user approved 2026-07-06).
- Dip-buyer/momentum live-exit engine change deferred to research plan
  (user decision, 2026-07-06).
