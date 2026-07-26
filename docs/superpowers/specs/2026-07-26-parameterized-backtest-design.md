# Parameterized single-strategy backtest — design (Plan 2b)

**Goal:** A real, reusable capability that runs a backtest of *any* registered
strategy with user-chosen hyperparameters, starting cash, and initial card
holdings over a date window — writing the same `equity.parquet` /
`fills.parquet` artifacts + registry record the read-only web explorer already
renders. Exposed through a widened `pkmn backtest` CLI now; called by the web
trigger endpoint later (Plan 2c).

**Context:** Second sub-plan of Plan 2 (run-triggering from the frontend), after
Plan 2a (native-engine initial-holdings seeding, merged). Umbrella design:
`2026-07-25-triggerable-backtests-design.md`. Brainstormed 2026-07-26.

## Strategic direction (reaffirmed)

The C++ engine is the destination; the Python engine is legacy. **This
capability runs entirely on the native engine — it never constructs the Python
`Backtest`.** `pkmn backtest` loses its `--engine python` option (native-only).
Actually *deleting* `engine/backtest.py` / `engine/portfolio.py` is the **next
plan after 2b** (a dedicated retire-python-engine plan), not part of this one,
because those modules are still the parity oracle (`test_native_parity.py`,
`scripts/parity_full.py`) and cannot be removed until the oracle is replaced.
Sequencing: **2b → retire-python-engine → 2c**.

## Strategy → engine path

The capability maps each strategy to a native-engine run:

| Strategy | Path |
| --- | --- |
| `buy-and-hold` | `NativeStrategySpec("buy-and-hold", {}, kind=...)` — native; `kind` (sealed/single) is its only knob, not a hyperparameter |
| `sealed-accumulation`, `dip-buyer`, `xs-momentum`, `cost-aware-reversion` | `NativeStrategySpec(name, params)` — native C++, parity-tested |
| `ml-ranker`, `ml-ranker-v2` | `REGISTRY[name].factory(params)` → Python `Strategy` instance, run through `NativeBacktest`'s callback bridge |

All seven run via `NativeBacktest(..., initial_holdings=holdings)`.

## Registry param metadata (single source of truth)

Strategy hyperparameters become declarative. Add:

```python
@dataclass(frozen=True)
class ParamSpec:
    name: str
    kind: Literal["int", "float"]
    default: float | int
    low: float | int
    high: float | int
    log: bool = False
```

Each `RegistryEntry` gains `params: tuple[ParamSpec, ...]`. This one list feeds
three consumers:

1. **The capability** — validates and coerces user-supplied params (reject
   unknown names, type-coerce per `kind`, reject out-of-`[low, high]`), and
   fills each omitted param with its `default`.
2. **Plan 2c's form** — the `/api/strategies` endpoint serves the specs so the
   UI renders the right fields, pre-filled with `default`, bounded by
   `[low, high]`.
3. **The optuna search space** — `space(trial)` is *derived* from the specs by
   a generic helper (`suggest_int`/`suggest_float` per `kind`, honoring `log`),
   replacing the six hand-written `_*_space` functions. Bounds live in one
   place.

`buy-and-hold` is not in `REGISTRY` (it is the benchmark, no hyperparameters);
its entry in the param-metadata surface is an empty `params` tuple, and `kind`
is handled separately.

### Reproducibility guard (non-negotiable)

Deriving `space` from specs must not change any past walk-forward number. A
seeded regression test asserts, for each of the six strategies, that the
derived space produces **bit-identical** suggestions to the current
hand-written `_*_space` — same parameter names, same `(low, high, log)`, and
**same suggestion order** (optuna's seeded sampler is order-sensitive). The
`ParamSpec` order in each `params` tuple therefore matches the current dict
order of its `_*_space`. If any strategy cannot be made bit-identical, that one
keeps its hand-written space and the test documents the exception — but all six
are expected to convert cleanly. This test is written and passing *before* the
hand-written space functions are removed.

## The capability

New module `src/pkmn_quant/research/backtest_run.py`, one public function:

```python
def run_single_backtest(
    root: Path,
    strategy_name: str,
    params: dict[str, float | int],   # partial or full; omitted -> defaults
    cash: float,
    holdings: list[SeededHolding],
    start: date,
    end: date,
    *,
    impact: bool = True,
    warmup_days: int = 0,
) -> BacktestRunResult
```

`BacktestRunResult` is a frozen dataclass: `run_id: str | None`, `result:
Result` (engine result — equity curve, fills, summary), `artifact_dir: Path`.

Flow:
1. Resolve the strategy → engine spec (table above). Unknown `strategy_name`
   → `ValueError`.
2. Validate + fill params against the strategy's `ParamSpec`s. Unknown key,
   wrong type that won't coerce, or out-of-bounds value → `ValueError`.
3. Build the `PreparedMarket` for `[start, end]` (+ warmup). Validate holdings:
   each holding's asset must resolve to a **mark on the first trading day** of
   the window (a seeded asset whose first price is after `start` would make the
   engine's `equity()` raise opaquely on day one). Missing universe membership
   or missing day-one mark → `ValueError` naming the offending asset. (These
   are the two checks Plan 2a's spec explicitly deferred to this layer.)
4. Run `NativeBacktest(..., initial_holdings=holdings, prepared=<the built
   market>)`.
5. Write artifacts to a **collision-free** directory keyed on `run_id`:
   `data/results/{run_id}/{equity,fills}.parquet`. (Triggered runs repeat the
   same strategy+dates constantly; today's `{strategy}-{start}-{end}` dir
   warns-and-overwrites, which is wrong for this use case.)
6. `record_run(command="backtest", strategy=strategy_name, config={...,
   "strategy": strategy_name, "params": <resolved params>, "holdings":
   <full holdings as a sorted list of dicts: product_id, sub_type, quantity,
   avg_cost, opened_on>, "cash", "start", "end", "impact", "kind"?,
   "cost_model"}, results=result.summary, artifact_path=<dir>, warehouse)`.
   Holdings are part of the run's identity, so they belong in `config` (which
   feeds `config_hash`), serialized deterministically (sorted, ISO dates) so
   the same run hashes identically.

The current inline artifact-writing + `record_run` logic in `cli.py`'s
`backtest` moves into this function so the CLI and (later) the web endpoint call
one code path.

### run_id-before-artifacts refactor

`record_run` currently generates the `run_id` internally, after artifacts are
written — so the dir can't be keyed on it. Refactor: extract a `new_run_id()
-> str` helper in `research/runs.py` and let `record_run` accept an optional
`run_id` (generate one if `None`, preserving all existing callers). The
capability calls `new_run_id()`, writes artifacts under it, then records with
it. `record_run` still never raises (bookkeeping failure warns and returns
`None`); the artifact dir is already keyed on the id the capability generated,
so artifacts survive a bookkeeping failure.

## CLI surface

Widen `pkmn backtest`:

- `--strategy <name>` (default `buy-and-hold`) — any registered strategy or the
  benchmark.
- `--param k=v` (repeatable) — override individual hyperparameters; omitted ones
  use registry defaults. Values parse per the strategy's `ParamSpec.kind`.
  (A single repeatable flag is chosen over a `--params '<json>'` blob for CLI
  ergonomics; the web endpoint takes structured JSON directly, not this flag.)
- `--holdings <file.csv>` — optional. Columns: `product_id` (int), `sub_type`
  (str), `quantity` (int), `avg_cost` (float), `opened_on` (ISO date). Omitted
  = cash-only (today's behavior, unchanged).
- `--cash`, `--start`, `--end`, `--impact/--no-impact` unchanged.
- `--kind sealed|single` retained (buy-and-hold universe selector); ignored for
  other strategies with a clear message if set.
- `--engine` **removed** (native-only). A `--strategy python`-style escape hatch
  is not provided.

Output: unchanged shape (strategy, fills count, summary metrics, `run recorded:
<id>`, results dir) — now reporting the run_id-keyed dir.

## Testing

- **Registry:** the seeded space-equivalence regression test (bit-identical
  suggestions, all six strategies); every `ParamSpec.default` lies within
  `[low, high]`; param names match what each factory consumes.
- **Capability** (`research/backtest_run.py`) on a synthetic warehouse: a rule
  strategy and an ML strategy each construct + run; `--param`-style override
  applied and defaults filled for omitted params; unknown-param / out-of-bounds
  → `ValueError`; holdings validation raises on out-of-universe asset and on an
  asset with no day-one mark; artifacts written under `data/results/{run_id}/`;
  registry record carries `strategy`, resolved `params`, and `holdings`.
- **CLI** (`tests/test_cli_backtest.py`): buy-and-hold default path still
  reproduces the existing golden numbers (dir assertion updated to the
  run_id-keyed path; numeric goldens unchanged with a hand-derivation note if
  anything shifts — nothing should); `--strategy sealed-accumulation` with a
  `--param` override runs and records; `--holdings` file parsed into a seeded
  run; `--engine` no longer accepted.
- All four gates green; existing `test_native_parity.py` untouched and passing
  (the Python engine still exists as the oracle until the next plan).

## Out of scope

- Web trigger endpoint, background jobs, and the backtest detail screen (Plan
  2c).
- Deleting the Python engine modules and dual-path branching (the dedicated
  retire-python-engine plan, sequenced before 2c).
- Walk-forward triggering, param *tuning* from the UI (this runs one backtest
  at fixed params; tuning is what `pkmn walkforward` already does).
- Capability-layer changes to the C++ engine (2a is complete and sufficient).
