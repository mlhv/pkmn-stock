# Plan 9 design: walk-the-spread impact costs + experiment tracking

Date: 2026-07-13
Status: approved design, pre-implementation

## Goal

Two production-grade upgrades to the backtester, in one plan because both must
be settled before the Plan 10 C++ engine port freezes engine semantics in a
second language:

1. **Walk-the-spread market-impact cost model** — order size moves your fill
   price against you, scaled by each product's observed spread. Makes every
   backtest number more honest; changes engine semantics and signatures.
2. **Experiment tracking** — an append-only run registry so every backtest and
   walk-forward number is reproducible from a config hash + data fingerprint.

Out of scope (later plans): parallel parameter search, point-in-time
warehouse, dashboard changes, any C++.

## Component 1: walk-the-spread impact model

### Market rationale

TCGplayer daily rows carry `low`/`mid`/`high` (lowest/median/highest listing
price) and `market` (recent-sales price). There is no volume column, so
liquidity must come from prices themselves:

- **Buys walk `market → mid`.** Buying eats the cheapest listings first; by
  the time you have taken the day's cap, you have bought through the cheap
  half of the book. `high` must NOT be used: real data shows it is troll
  listings ($9,999 on a $25 card; verified on 2026-06-30 rows).
- **Sells walk `market → low`.** To move more units you undercut the lowest
  listing progressively deeper. `low` is safe (lowball listings are
  competitive, not trolls).

### Formula

For an order of `q` units against a daily cap `Q` (the existing
`max_daily_qty` price-tier cap, unchanged):

- Buy average fill price: `market + max(mid − market, 0) · q / (2Q)`
- Sell average fill price: `market − max(market − low, 0) · q / (2Q)`

Properties (all unit-tested):

- Marginal price degrades linearly from `market` (front of book) to the far
  edge at `q = Q`; the `q/(2Q)` term is the average of that ramp.
- Impact ≥ 0 always (clamped); a crossed quote (`mid < market` or
  `low > market`) yields zero impact, never price improvement.
- `q = 0` ⇒ no impact; impact is monotonically increasing in `q`.
- Null `mid` (buys) or null `low` (sells) ⇒ impact 0. We do not invent
  numbers for missing data (same principle as the ml-ranker all-NaN fix).
- For products ≥ $200, `Q = 1`, so the single unit pays half the spread.
  Deliberate: in a one-sale-a-day market, assuming ideal market-price fills
  is exactly the mark-smoothing optimism the findings doc warns about.

### Where it lives

- `CostModel` (frozen dataclass, `engine/costs.py`) grows
  `impact_enabled: bool = False` and impact-aware pricing methods that accept
  the needed quote fields (`mid` for buys, `low` for sells). Existing
  flat-cost methods remain; with `impact_enabled=False` behavior is
  bit-identical to today.
- `CostModel.as_dict()` includes the new fields so every serialized Result
  states its cost assumptions (existing convention).

### Plumbing

- `ExecutionSimulator.execute()` currently receives
  `prices: dict[Asset, float]` (market only). It must also see `mid`/`low`
  for ordered assets. Implementation detail left to the plan, with one hard
  constraint: do not regress the Plan 8 marks-cursor perf win (~1.9x).
  Suggested approach: keep the bulk `prices` dict for marks/caps and resolve
  `mid`/`low` only for the handful of ordered assets.
- **`Fill.price` stays the observable market print** (auditable-ledger
  convention in `execution.py`). Impact is recorded as a new explicit
  `impact: float = 0.0` field on `Fill` — cash effect identical to a fee on
  buys / a haircut on sells, but reported separately so reports can say
  "impact cost you X".
- Ledger backward compatibility: existing JSONL ledgers (real + paper) have
  no `impact` key; replay treats a missing key as `0.0`. No migration.
- Live surfaces (`plan_paper_fills` in `live/paper.py`, `pkmn signals`
  sizing) use the same impact-aware CostModel methods so paper fills and
  recommendations reflect impact.

### Defaults and goldens (follows the `warmup_days` precedent)

- Engine-level default: `impact_enabled=False` → all existing goldens and
  tests pass untouched.
- CLI default (**on**) for `pkmn backtest`, `pkmn walkforward`,
  `pkmn signals`, `pkmn daily`; `--no-impact` opt-out flag on each.
- `tests/test_cli_backtest.py`: the existing golden case passes `--no-impact`
  and keeps its pinned numbers; a NEW golden case runs with impact on against
  seed data extended with `mid`/`low`, with the hand-derived arithmetic in
  its docstring (repo convention).

### Research deliverable

Re-run the headline walk-forwards (buy-and-hold sealed benchmark,
sealed-accumulation, ml-ranker at minimum) with impact on; add a Plan 9
section to `docs/research-findings-2026-07.md`. Hypothesis to check: impact
hurts high-turnover strategies more, widening buy-and-hold's lead.

## Component 2: experiment tracking

### Naming note

`research/registry.py` already exists (the *strategy* registry). The run
tracker is `src/pkmn_quant/research/runs.py`, matching the `pkmn runs` CLI.

### Storage

Append-only JSONL at `data/runs/registry.jsonl` (gitignored; same pattern as
the trade ledger). One record per completed `pkmn backtest` or
`pkmn walkforward` run:

- `run_id` — UTC timestamp + short random suffix, e.g.
  `20260713T140502Z-a1b2c3`; also printed to the console at run end.
- `recorded_at` — ISO-8601 UTC timestamp.
- `command` — `backtest` | `walkforward`.
- `git_sha` + `git_dirty` — from `git rev-parse HEAD` / `git status
  --porcelain`; `null` + `true` if git is unavailable.
- `config_hash` — SHA-256 of the canonically serialized resolved config
  (sorted keys, no whitespace): strategy name, date range, cash, warmup,
  `CostModel.as_dict()` (includes impact fields), optuna trials/seed, fold
  parameters. Same hash + same data fingerprint ⇒ identical results (optuna
  is already seeded).
- `config` — the resolved config itself (the hash's preimage, for humans).
- `data_fingerprint` — warehouse min date, max date, total row count (one
  cheap DuckDB query).
- `results` — headline metrics: total return, CAGR, max drawdown, trade
  count; for walkforward, stitched OOS return and per-fold count.
- `artifact_path` — the run's results dir / `walkforward.json` path.

### Behavior

- Recording happens at successful run completion. A tracking failure (e.g.
  unwritable dir) prints a warning and never fails the run — research
  results must not be lost to bookkeeping errors.
- `pkmn runs list` — newest-first table (run_id, command, strategy, dates,
  headline metric, git_sha short, dirty flag); `--strategy` filter.
- `pkmn runs show <run_id>` — pretty-printed full record; prefix match on
  run_id accepted.
- Public API kept small: `record_run(...)`, `load_runs(...)` — mirrors the
  ledger's `load_events`/`replay` style.

## Testing

- Impact math unit tests: zero at q=0, monotone in q, clamps, null
  fallbacks, equals flat model when disabled, Q=1 half-spread case.
- Engine integration test: a multi-day backtest with impact on, hand-derived.
- Existing golden untouched (via `--no-impact`); new impact-on golden with
  hand-derivation in the docstring.
- Ledger compat test: replay a ledger line without `impact`.
- Paper-fill test: `plan_paper_fills` applies impact.
- Runs tests: record → load round-trip; config-hash stability (same config
  ⇒ same hash, key-order independent); CLI list/show smoke; tracking failure
  does not fail the run.
- All four gates (pytest, ruff check, ruff format, mypy) before every commit.

## Error handling summary

- Missing/crossed quote fields ⇒ impact 0 (never a crash, never negative).
- Tracking I/O errors ⇒ warn and continue.
- `pkmn runs show` with unknown/ambiguous id ⇒ clean error listing matches.

## Workflow

Feature branch `feat/impact-and-runs`; two-stage review per task; STOP after
each completed task and explain at intern level; wait for explicit green
light (repo convention).
