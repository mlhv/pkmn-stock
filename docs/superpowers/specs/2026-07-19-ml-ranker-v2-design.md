# ml-ranker-v2 — design (ML depth, Plan A of two)

**Goal:** A second-generation ML ranking strategy that chases a positive
out-of-sample result with three upgrades over ml-ranker — an expanded
friction-aware feature set, net-of-cost training labels, and in-loop purged
validation — evaluated honestly through the rigor pack (`pkmn evaluate`)
with the original ml-ranker kept intact as the ablation baseline.

**Context:** Brainstormed 2026-07-19. ML depth was decomposed into two
plans: this spec (Plan A, ranker upgrades) and a later meta-labeling spec
(Plan B) that will reuse A's label and validation utilities. Stance chosen
by the user: chase a positive result, but every claim goes through the
deflated-Sharpe / Reality Check machinery — hunt aggressively, report
deflated.

## Components

### 1. Feature expansion (`research/features.py`)

A v2 feature set: the existing 8 (`FEATURE_COLS`) plus ~8 new
leakage-bounded features, all from data already in the warehouse.
Existing `build_features`/`build_training_frame`/`FEATURE_COLS` stay
byte-identical (v1 reproducibility); v2 gets `FEATURE_COLS_V2`,
`build_features_v2`, `build_training_frame_v2` sharing private helpers.

New features (exact formulas fixed at plan time; leakage rule for every
one: reads only rows dated <= as_of):

- `spread_frac` — (market − low) / market at as_of; friction proxy.
- `mid_gap` — (market − mid) / market at as_of.
- `spread_30d_mean` — mean of daily spread_frac over the 30 days ending
  as_of; stable-friction proxy.
- `ret_accel` — ret_7d − ret_30d (momentum acceleration).
- `drawdown_180d` — 1 − market / max(market over 180 days ending as_of).
- `vol_ratio` — vol_7d / vol_30d (volatility regime shift).
- `xs_rank_ret_30d` — cross-sectional percentile rank of ret_30d among
  assets printing on as_of.
- `days_priced` — count of print days for the asset at or before as_of
  (listing age, distinct from days_since_release).

Quote columns `low`/`mid` exist in PRICE_SCHEMA and `Context.history`
carries raw price rows; the plan verifies end-to-end that both columns
survive into `ctx.history` (if any code path projects them away, plumbing
them through must be parity-inert for both engines and covered by a test).
Null handling matches v1 (tree models tolerate NaN; all-null columns are
dropped from fit and predict by the strategy).

### 2. Net-of-cost labels

v2's training label is the horizon forward return minus a per-row
round-trip cost fraction, computed from the real `CostModel` at qty=1 and
that row's prices: buy-side cost (shipping, and impact toward mid when
quotes exist) plus sell-side cost (marketplace fee percentage, shipping,
impact toward low), expressed as a fraction of the entry price. A pure
function in the labels code (exact signature at plan time) so Plan B can
reuse it. Rows lacking quotes fall back to the fee/shipping-only cost
(never a fake impact number). The label builder's leakage bound is
unchanged: training dates end at as_of − horizon_days.

### 3. In-loop purged validation (`research/purged.py` or within features)

At each rebalance, inside `on_bar`:

1. Split the training frame's dates chronologically: the most recent ~15%
   of distinct training dates form the validation set, with an embargo of
   `horizon_days` between the last train date and the first validation
   date (no label window spans the boundary).
2. Fit each configuration from a small fixed grid (exact grid fixed at
   plan time; on the order of 3-4 combos of `max_iter`/`learning_rate`)
   on the train split; score by mean Spearman rank correlation between
   predictions and labels across validation-date cross-sections
   (scipy.stats.spearmanr; the strategy ranks, so rank quality is the
   right metric).
3. Refit the winning configuration on the full training frame and use it
   for today's prediction. Ties and degenerate scores resolve to the
   first grid entry (deterministic).
4. `early_stopping=False` is set explicitly on every fit, closing
   sklearn's silent random-split early stopping (auto-activates above
   10,000 samples) — a live leak channel under time-correlated labels.

Too-few validation dates (thin history) => skip selection, use the grid's
first entry on the full frame; never crash. Everything remains stateless
and live-safe: the split and selection are pure functions of Context.

### 4. Strategy `strategies/ml_ranker_v2.py`

The v1 trading skeleton verbatim (rebalance clock on `opened_on`,
sells-first ordering, equity/len(target) sizing, min_price trade filter,
deterministic tie-breaks, `random_state=0`), swapping in v2 features, net
labels, and purged config selection. Registered as `ml-ranker-v2` with its
own optuna search space (fold-level search still tunes strategy-level
params: horizon, rebalance, top_n, min_price, and the label/validation
knobs the plan fixes as searchable); added to PORTFOLIO_SAFE_STRATEGIES;
runs on the C++ engine via the existing callback bridge (no engine
changes).

### 5. Evaluation protocol and honesty rules

- Pre-declared budget: ONE impact-on walkforward at the standard settings
  (2024-03-01..2026-06-30, is=180, oos=60, trials=15, seed 42), recorded
  in the registry like every run.
- Then `pkmn evaluate` over the full zoo including BOTH ml-ranker (v1)
  and ml-ranker-v2: the findings section reports the v1-vs-v2 ablation
  and the deflated verdict over the enlarged zoo.
- If v2 is negative, that is the reported result. Any additional research
  runs beyond the declared budget are allowed but must appear in the
  registry and be counted in the findings' trial accounting (no silent
  re-rolls).
- Mark-smoothing caveat applies to every Sharpe-derived figure, as
  everywhere.

## Error handling

- Thin history: v2 degrades exactly like v1 (min_train_rows gate, hold on
  no-model bars); validation-selection additionally degrades to
  first-grid-entry before that gate is reached.
- Missing quote columns in a row: cost function falls back to
  fee/shipping-only; never NaN labels from missing quotes alone.
- All-null new features in early folds: dropped from fit/predict like v1.

## Testing

- Leakage regression tests for every new feature (the existing
  features-test pattern: shifting future rows must not change features).
- Hand-derived net-label test: one row, known prices/quotes, cost fraction
  computed by hand in the docstring.
- Purged-split property tests: no validation date within embargo of any
  train date; chronological ordering; deterministic selection on ties;
  early_stopping explicitly False (asserted via the fitted model's param).
- Strategy tests: determinism (two runs identical), degenerate-data holds,
  bridge smoke on the C++ engine, PORTFOLIO_SAFE inclusion.
- No existing test may change; v1 code paths byte-identical.

## Out of scope (Plan B and beyond)

- Meta-labeling (Plan B; will reuse the net-cost label function and the
  purged splitter).
- Engine/C++ changes; new data ingestion; dashboard surfacing.
- Offline hyperparameter tuning commands.
