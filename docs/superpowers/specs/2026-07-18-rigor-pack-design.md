# Statistical rigor pack — design

**Goal:** Upgrade every headline number from a point estimate to a defensible
statistical claim: bootstrap confidence intervals per strategy, a deflated
Sharpe ratio across the strategy zoo, and White's Reality Check for the joint
"did anything really beat buy-and-hold" question. Pure-Python research-layer
work; no engine or C++ changes.

**Context:** This is the first of two sub-projects chosen in brainstorming
(2026-07-18): rigor pack first (this spec), then the ML-depth work (purged
CV, meta-labeling, cost-aware objective) judged by these metrics from day
one. A frontend brainstorm follows separately later.

## Components

### 1. `src/pkmn_quant/research/stats.py` — the statistics core

Pure, seeded functions; numpy for the engine, scipy (already a transitive
dependency of scikit-learn; becomes an explicit import here) only for
distribution helpers (normal CDF, skew/kurtosis).

- **Stationary block bootstrap** (Politis-Romano): resamples blocks of
  geometric random length (configurable mean block length, default 10
  trading days) with wraparound, preserving autocorrelation in daily
  returns. Seeded RNG (default seed 42), default 10,000 resamples. This is
  the shared engine for both the CIs and the Reality Check.
- **`bootstrap_ci`**: given a daily-return series and a metric (total
  return; Sharpe), returns point estimate plus a 95% percentile CI from the
  bootstrap distribution.
- **`deflated_sharpe`**: Bailey/Lopez de Prado formula. Inputs: candidate's
  observed Sharpe, the number of trials, the variance of the trials'
  Sharpes, the return series' skewness and kurtosis, and the observation
  count. Output: probability the true Sharpe exceeds zero after correcting
  for selection among the trials.
- **`whites_reality_check`**: White (2000). Given a matrix of daily excess
  returns (strategy minus benchmark, aligned days) for the whole zoo,
  bootstrap the max average excess return across strategies; p-value =
  fraction of bootstrap maxima exceeding the observed max. Reuses the same
  stationary bootstrap engine and seed discipline.

Daily returns are computed with the same conventions as the existing
`engine/metrics.py` (equity percent change, same annualization constants) so
every number stays consistent with current reports.

### 2. `pkmn evaluate` — the cross-strategy command

- **Discovery:** scans `data/results/wf-*/` for `walkforward.json` +
  `stitched_equity.parquet`; takes the benchmark equity curve from the
  buy-and-hold artifact (`--benchmark` path option, defaulting to the
  existing `buy-and-hold-sealed-<oos-window>` artifact naming). Explicit
  `--include`/`--exclude` strategy filters are out of scope for v1; it
  evaluates everything it finds.
- **Alignment:** inner-join all series on common dates. Clean `typer`
  errors when: no artifacts found; fewer than 2 strategies (Reality Check
  needs a zoo); date overlap below a minimum (60 days).
- **Computation:** per strategy, stitched OOS total return + bootstrap CI +
  Sharpe + deflated Sharpe (trial set = the discovered zoo; variance across
  the zoo's Sharpes); one joint Reality Check p-value for the zoo vs the
  benchmark.
- **Output:** `data/results/evaluate-<date>/report.md` and
  `evaluate.json`; a registry record appended via the existing
  `record_run` machinery (config hash covers input artifact paths + data
  fingerprints, seed, n_boot, block length), preserving the
  "every number reproducible from its config hash" property.

### 3. Walkforward report addition

Each walkforward run's `report.md` (and `walkforward.json` summary) gains a
bootstrap CI band on its stitched OOS total return. Deflated Sharpe is
deliberately NOT computed per-run: it would need per-trial Sharpes from the
optuna search, which the runner does not retain, and an approximation would
be exactly the kind of soft number this pack exists to eliminate. DSR and
the Reality Check live only in `pkmn evaluate`, where the trial set (the
strategy zoo) is real.

### 4. Honesty rules

- The existing mark-smoothing caveat extends to the new metrics: CIs and
  DSR inherit the same Sharpe inflation from carry-forward marks; every
  report that shows them says so.
- All randomness is seeded and recorded; same inputs + seed = identical
  numbers, byte for byte.
- Findings doc gets a new section with the evaluate run's table once the
  implementation lands (measured numbers only, no projections).

## Error handling

- Missing/unreadable artifacts: list every missing path, exit non-zero,
  no partial output.
- Degenerate series (constant equity, zero-variance returns): Sharpe-based
  metrics report as not-computable for that strategy rather than dividing
  by zero; the report prints the reason.

## Testing

- Deterministic seeded unit tests pinning exact outputs (golden-style: same
  seed, same numbers) for all four core functions.
- Analytic sanity tests on synthetic data: iid normal returns give CI
  coverage near the nominal 95% (tested over many seeded repetitions);
  a zero-edge zoo gives DSR near zero and a large Reality Check p-value;
  an injected genuine edge drives the p-value down.
- CLI test: `pkmn evaluate` end-to-end over synthetic artifacts in
  `tmp_path` (including the registry record shape) plus clean-error tests
  (no artifacts; single strategy; thin overlap).
- No engine/C++ changes; the four gates and existing suite must stay green
  and untouched.

## Out of scope (v1)

- Hansen's SPA refinement of the Reality Check.
- Per-run deflated Sharpe (see section 3).
- Strategy filters on `pkmn evaluate`.
- Dashboard surfacing (belongs to the later frontend sub-project).
