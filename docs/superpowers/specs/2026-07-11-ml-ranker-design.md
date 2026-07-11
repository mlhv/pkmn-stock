# ML Cross-Sectional Ranker + Engine Perf Fix — Design

Date: 2026-07-11. Follows the short-horizon research spec
(`2026-07-06-short-horizon-research-design.md`, shipped as Plan 6) and the
paper/dashboard cleanup spec (`2026-07-11-paper-dashboard-cleanup-design.md`,
shipped as Plan 7). This is Plan 8.

## Goal

A gradient-boosted cross-sectional ranking strategy (`ml-ranker`) that
learns which products to hold from engineered features, evaluated under the
same walk-forward regime as every hand-built strategy, and deployable
through the identical live pipeline (`pkmn signals --portfolio`, paper
daily loop) from day one. Plus the engine performance fix that pays for the
added training cost.

**Stated goal (user, 2026-07-11):** get as close to buy-and-hold sealed
(+151.1% OOS span) as an active strategy can. Honest framing: the benchmark
is the ceiling, not the target — any strategy that trades pays more
round-trip cost (~15%) than buy-and-hold pays once. "Close" is realistic;
"beat" almost certainly is not in one bull regime. The deliverable that
survives either outcome is the leakage-safe ML evaluation pipeline and an
honest findings entry.

Success criteria: leakage guards implemented AND regression-tested; the
ranker walk-forwarded with the standard 11-fold protocol and the
overfitting gap reported; portfolio-safe and paper-tradeable; perf fix
golden-byte-identical with a measured speedup; findings/README/CLAUDE.md
current, negative results reported as negative.

## Decisions log

- Universe: ALL products (singles + sealed), `kind` as a model feature —
  revised from singles-only when the user set the closest-to-benchmark
  goal; the ranker must be allowed to learn "sealed trends" rather than
  having it hardcoded (user decision, 2026-07-11).
- Library: scikit-learn (`HistGradientBoostingRegressor`), new MAIN
  dependency — pure-install, trains in ~100ms at our scale; LightGBM
  rejected (native-lib install cost, no gain at ~10-30k rows); hand-rolled
  no-dep model rejected (weak model, tests the wrong thing) (user decision,
  2026-07-11).
- Live wiring: fully portfolio-safe from day one (user decision,
  2026-07-11).
- Training placement: INSIDE the strategy from `ctx.history` (approach 1 of
  3). Fold-level training in the research layer rejected (new machinery:
  model serialization, parallel runner; weakens the strategy-can't-tell-
  live-from-backtest invariant). Offline prediction tables rejected
  (feature precomputation over the full dataset is where lookahead bugs
  breed). Training placement has near-zero effect on returns; it was
  chosen on leakage-safety and machinery grounds.

## Task 0 (first): engine perf fix — vectorize the per-day hot path

Profiled ground truth (2026-07-11, one 180-day IS backtest with 120d
warm-up, sealed-accumulation): **5.0s total**, of which `marks_on` ≈ 2.2s
and `prices_on`/`_day_dict` ≈ 1.9s (~80%). `load_prices()` itself is 0.15s
for 3.0M rows — the old "each Backtest re-reads the parquet" theory named
the wrong culprit; the cost is per-day Python dict construction:
`marks_on` re-runs a group-by over all change-points every day, and
`prices_on` filters + row-iterates the frame every day.

Fix, inside `engine/data.py` (`MarketData`), API unchanged:

- `prices_on`: precompute `{day -> {Asset -> price}}` once per
  `from_warehouse` (single pass over the frame, grouped by date). Calls
  return a fresh shallow copy so callers still own their dicts.
- `marks_on`: incremental cursor. Keep `_marks_compact` (change-points
  sorted by asset, date); maintain a running marks dict plus a
  last-queried-day watermark; a query for day ≥ watermark applies only the
  change-points in (watermark, day] and advances; a query for an earlier
  day rebuilds from scratch (rare path: the event loop is monotone; the
  dashboard queries a single latest day). Same values, same API.
  `MarketData` is a frozen dataclass; the cursor is internal mutable cache
  (`object.__setattr__` or dropping frozen) — a plan-level detail; the
  contract is that observable behavior is identical.

Acceptance: golden test byte-identical; entire suite green untouched;
before/after timing on the profiling scenario recorded in the commit
message (expect roughly 3x on that scenario). No timing asserts in tests
(flaky); the measurement is manual and documented.

Why first: the ranker retrains inside backtests; the walk-forward budget
(11 folds x 17 runs) only stays in tens-of-minutes territory with this
landed.

## Features and labels: `research/features.py` (pure polars)

Two pure functions, no I/O, fully deterministic:

```python
def build_features(history: pl.DataFrame, products: pl.DataFrame, as_of: date) -> pl.DataFrame
```

One row per (product_id, sub_type) that printed on `as_of`, columns:
trailing pct returns over 7/30/90 days, 30-day daily-return volatility,
distance from the 90-day high (1 - mark/high), log market price, `kind`
(categorical: single/sealed — HistGradientBoosting takes categoricals
natively), and days since `released_on`. Assets lacking enough history for
a window get null for that feature (the model handles nulls natively;
no imputation code). Feature set is deliberately small (~8): every extra
feature is overfitting surface against ~2.4 years of data.

```python
def build_training_frame(
    history: pl.DataFrame, products: pl.DataFrame, as_of: date,
    horizon_days: int, train_days: int, stride_days: int,
) -> pl.DataFrame
```

Feature rows for past dates D paired with the label = pct return from D to
D + horizon_days, subject to:

- **Label-legality bound (the leakage guard):** only dates
  `D <= as_of - horizon_days` produce rows. The label reads a price
  `horizon_days` ahead of D, which is still <= as_of — within history,
  never past it. Today's cross-section is predicted on, never trained on.
- **Recency bound:** D >= as_of - train_days (don't train on the whole
  archive; regimes drift).
- **Stride:** sample training dates every `stride_days` (default =
  horizon_days). Adjacent-day labels overlap almost completely at k=30 and
  would masquerade as independent samples; a stride of ~k gives
  near-non-overlapping labels and shrinks the frame to ~10-30k rows
  (~100ms train).

## The strategy: `strategies/ml_ranker.py`

Same skeleton as xs-momentum (post-Plan-6): stateless, derived rebalance
clock, rank-and-hold-top-N.

- **Rebalance due** when flat, or when
  `(today - newest opened_on).days >= rebalance_days` (identical derived
  clock; `ValueError` on `opened_on is None`, house pattern).
- **On a due bar:** `build_training_frame(ctx.history, ...)` → fit
  `HistGradientBoostingRegressor` (pinned `random_state=0`; tuned
  hyperparams below) → `build_features(ctx.history, ..., as_of=today)` →
  predict → rank descending. Target = top_n names with mark >= min_price.
  Sell holdings that dropped out of the target (full position); buy
  entrants equal-weighted from `cash * budget_frac` per name. Not-due bars
  return `[]`.
- **Degenerate-data guard:** if the training frame has fewer than
  `min_train_rows` rows (constructor param, default 200), no model is fit
  and `on_bar` returns `[]` — with no model there is no target, so neither
  buys nor drop-out sells fire that bar (conservative: hold). Documented in
  the docstring, covered by a test. Reachable in early-history folds and
  hand-built test contexts; not an error.
- **Determinism:** same Context in → same orders out, pinned by a test
  (two identical Contexts must yield identical orders). `random_state=0`
  is fixed; if thread-level float nondeterminism ever surfaces in
  HistGradientBoosting, the fix is pinning threads, and the test is what
  will catch it.
- **Nothing survives between invocations that cannot be rebuilt from
  Context** — the property that makes single-bar live invocation safe.
  The fitted model is a local variable of the due-bar path, not retained
  instance state.

Constructor params (defaults): `horizon_days=30`, `rebalance_days=30`,
`top_n=8`, `train_days=365`, `stride_days=None` (→ horizon_days),
`min_price=3.0`, `budget_frac=0.10`, `min_train_rows=200`,
`max_iter=100`, `learning_rate=0.1`, `min_samples_leaf=20`.

## Look-ahead guards (three layers, each enforced)

1. **Structural:** the strategy sees only `ctx.history`
   (`history_until(day)`, dates <= today). It cannot read a future price
   because the frame containing one never reaches it. This wall is why
   training lives inside the strategy.
2. **Label discipline:** `build_training_frame` enforces
   `D <= as_of - horizon_days` internally (the only place a forward-read
   exists, bounded to stay within history).
3. **Leakage regression test:** build features and the training frame as of
   day D; append future rows (> D) to the history; rebuild as of the same
   D; assert frame-identical output. Any leak — including subtle ones like
   full-frame normalization introduced by a future refactor — trips it.
   Plus a boundary test: a label row exists for `as_of - horizon_days` and
   does not exist for `as_of - horizon_days + 1`.

## Overfitting guards

- Identical walk-forward protocol, zero new machinery: 11 folds, optuna on
  IS only (15 trials, seeded), params frozen, OOS-only stitched headline,
  overfitting gap reported. The gap is the headline honesty metric for
  this strategy — ML + hyperparameter search is a larger noise-fitting
  surface than any hand-built strategy, and the findings entry must read
  the gap first.
- Deliberately starved outer search: ~7 tuned params (below), same
  15-trial budget as the others.
- Deliberately small feature set (~8), stride-decorrelated labels,
  recency-bounded training window.

Optuna space (`research/registry.py`):
`horizon_days` int 14-60, `rebalance_days` int 21-90, `top_n` int 3-15,
`train_days` int 120-540, `max_iter` int 50-300 (log), `learning_rate`
float 0.03-0.3 (log), `min_samples_leaf` int 10-50. Factory maps them to
the constructor; sizing params stay at defaults (house pattern from
cost-aware-reversion).

## Wiring

- `REGISTRY["ml-ranker"]` entry + THESIS entry in `live/report.py` (the
  set-equality tests force both).
- `PORTFOLIO_SAFE_STRATEGIES` += "ml-ranker" (it satisfies the membership
  contract: exit logic reads only Context + opened_on).
- scikit-learn added to `[project] dependencies` in pyproject.toml (main
  dependency: the registry imports the strategy module at import time, and
  walkforward/signals must work under plain `uv sync`).

## Error handling

- `opened_on is None` → `ValueError` naming the strategy and asset (house
  pattern, byte-consistent with siblings).
- Too little trainable data → no orders that bar (documented, tested,
  reachable, not an error).
- sklearn import failures are ordinary dependency errors (main dep, no
  lazy-import gymnastics).
- No new failure modes in live signals: the existing SignalsError paths
  (missing artifact, params-incompatible) cover the ml-ranker like any
  registry strategy.

## Testing

- **Perf (Task 0):** golden byte-identity (`tests/test_cli_backtest.py`
  unmodified), full suite green, manual before/after timing in the commit
  message. A correctness test pins marks_on cursor behavior on
  out-of-order queries (monotone fast path vs rebuild path give identical
  answers).
- **Features:** hand-computed values on tiny frames (returns, vol,
  dip-from-high, age); null behavior for short-history assets; the
  leakage regression test; the label boundary test; stride test (row count
  and date spacing).
- **Strategy:** `_mk_ctx`-pattern unit tests — derived clock (29/30-day
  boundary), `ValueError` on None `opened_on`, statelessness across bars,
  determinism (two identical Contexts → identical orders), degenerate-data
  guard (thin history → no orders), and the synthetic-signal smoke test:
  a toy history where asset A rises monotonically and asset B falls — the
  trained ranker's buys must include A and not B. That test proves the ML
  plumbing (features → train → predict → rank → orders) end to end without
  pretending to validate the edge.
- **Registry/live:** the parametrized registry tests auto-cover the entry;
  a portfolio-mode end-to-end test through real `generate_signals` (seeded
  wf artifact, aged ledger position) mirrors the Plan 6 dip-buyer test.
- All four gates green per task; goldens byte-identical throughout (the
  golden strategy is buy-and-hold and never touches ML code, but the
  Task 0 engine change is exactly what goldens exist to police).

## Research runs + honest reporting (final task)

Standard protocol: `uv run pkmn walkforward --strategy ml-ranker --start
2024-03-01 --end 2026-06-30 --trials 15` after re-ingesting to current.
Findings doc gets a dated section: stitched OOS vs buy-and-hold sealed
(+151.1%) and sealed-accumulation (+13.6%); the overfitting gap read
first and honestly (Plan 6 precedent: xs-momentum's +12.9pt gap was called
noise-fitting in print); the mark-smoothing Sharpe caveat; explicit
statement of what the ranker allocated into (did it learn "hold sealed"?).
Live smoke: `pkmn signals --strategy ml-ranker --portfolio` and a paper
daily run. README (no em dashes in new sentences) + CLAUDE.md updates.
Negative results reported as negative; the pipeline is the deliverable.

## Out of scope

Fold-level model training/serialization; deep learning; feature stores or
offline prediction tables; multi-marketplace data; equity-chart perf;
sizing beyond the house equal-weight pattern; walkforward parallelism.
