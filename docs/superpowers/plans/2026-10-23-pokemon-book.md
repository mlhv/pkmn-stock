# Pokemon Order Book (Plan B of 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Plan A order book becomes the C++ backtester's default
execution path for Pokemon cards. Each asset-day's resting orders are a
labelled model built from that day's tcgcsv quote. The book is proven
equivalent to the formula in its continuous limit. It is also fed by an
ITCH-style wire protocol, and both paths provably build identical books.

**Architecture:** `cpp/src/pkmn_engine/ladder.{hpp,cpp}` is the single
ladder builder. It turns one asset-day's quote plus the `CostModel` into
one-unit resting orders on a `pkmn::book::OrderBook`.

- `execution_book.cpp` fills strategy orders against those books as
  aggressive orders. It sits behind an execution-mode switch on the C++
  `CostModel`. The formula path is left untouched, so parity holds.
- `cpp/src/pkmn_wire/` holds the wire codec: an encoder from warehouse
  arrays and a decoder that emits wire events.
- A replay driver turns those events into ladders through the same builder.
- `pkmn_book` and `pkmn_wire` are pure C++ with no zlib, so both link into
  the `_engine` Python module.
- The Python side threads `execution` through the CLI, the registry, the
  API and the web form, and refuses any comparison that mixes modes.

**Tech Stack:** C++20, CMake >= 3.26, nanobind (existing), Catch2 v3.7.1,
Python 3.12 + polars + typer, React/TS for one form field, and the
OrbStack Ubuntu 24.04 arm64 VM for the headline measurements.

**Spec:** `docs/superpowers/specs/2026-10-22-market-data-and-clob-design.md`.
This plan implements the sections "The Pokemon book", "Engines", "Pokemon
wire protocol", "Future trade feed", the "Pokemon replay throughput" and
"Backtest cost" parts of "Measurement", and the Pokemon success criterion.

**Plan series:**
- A (IEX book) is done.
- B is this plan.
- C is UDP multicast transport and gap recovery over the wire format built
  here.
- D is the research re-run, book vs formula. No research numbers are
  produced or claimed in B.

## Global Constraints

- **Branching.** Branch `feat/pokemon-book` from `feat/instrument-repair-horizon`
  (HEAD 1e5c2a8). This repeats the owner's Plan A ruling: the spec says to
  branch from main after that branch merges, but it has not merged.
  Commit this plan as the branch's first commit.
- **No fast-math.** No `-ffast-math`, `-ffp-contract=fast` or `/fp:fast`
  anywhere. Every new library compiles with `-ffp-contract=off`, PUBLIC,
  like `pkmn_engine_core`.
- **The formula path stays bit-for-bit.**
  - `execute()` in `cpp/src/pkmn_engine/execution.cpp` and
    `CostModel::buy_impact`/`sell_impact`/`impact_` are not edited.
  - `tests/test_native_parity.py` must still pass, and so must
    `scripts/parity_full.py` (run once, in Task 7).
  - The library default stays formula: `NativeBacktest.execution="formula"`,
    and C++ `CostModel::execution = ExecutionMode::Formula`.
- **Defaults and `--no-impact`.**
  - `book` is the default only at the user surfaces: the `pkmn backtest`,
    `pkmn walkforward` and `POST /api/backtest` request models, and the web
    form.
  - `--no-impact` means flat cost and overrides both modes. The effective
    mode of an impact-off run is recorded as `"formula"`.
- **Synthetic labelling.** The resting orders are a model, not data. Every
  surface that shows book execution says so: CLI `--help`, CLI run output,
  the protocol doc, code comments at the ladder builder, the benchmark
  doc, and CLAUDE.md. Use the phrase "modelled resting orders (synthetic,
  built from the day's tcgcsv quote)".
- **Ladder prices.** The default ladder is priced in whole cents (ticks per
  dollar = 100), rounded to nearest with halves away from zero (`std::llround`).
  The test-only exact ladder uses 1,000,000,000 ticks per dollar and exists
  only to prove equivalence. The CLI does not expose it.
- **Registry key.** Every new `backtest` and `walkforward` registry record
  carries a top-level `config["execution"]` of `"book"` or `"formula"`.
  `walkforward.json` carries `"execution"`. Readers treat a missing key as
  `"formula`, because every record before this plan was formula.
- **Rebuild and gates.**
  - Run `uv sync --group api --group dashboard --reinstall-package pkmn-quant`
    after any `cpp/` change.
  - All four gates pass before every commit: pytest, ruff check, ruff
    format --check, mypy.
  - The C++ suite passes on macOS (`cpp/build`) and in the VM
    (`cpp/build-linux`).
  - `cd web && npm run check && npm run build` passes whenever `web/` changes.
- **Out of git.**
  - Wire files go under `data/wire/` (gitignored). No warehouse bytes are
    committed.
  - Test fixtures are built in code (pytest `tmp_path`, Catch2 in-memory
    buffers).
- **Pacing.** No pause between tasks. The owner's standing instruction for
  this plan series is to give a terse summary after each task and carry on.

## Review Focus

1. **Crossed or one-sided quotes** (`mid < base` on the ask side,
   `low >= market` on the bid side, NaN `mid` or `low`). Expected: a flat
   book at the base price with the full tier depth, and never a negative
   impact. Tests are in Task 1 (`ladder: crossed and missing quotes build
   flat books`) and Task 2 (`book buy on crossed quote charges only the
   floor premium`).
2. **Penny prices and the bid ladder near zero** (market $0.02, low
   $0.01). Expected: every bid price is >= 0 ticks, and sale proceeds are
   never above `qty * market`. The test is in Task 1 (`ladder: penny bid
   ladder never goes negative`).
3. **Warehouse values the wire cannot carry.** This means a price that is
   not a whole number of cents, or one at or above the u32 null sentinel
   ($42,949,672.95). Expected: the encoder raises `ValueError` naming the
   product, sub_type and date, and never writes a silently rounded file.
   The test is in Task 6 (`test_encode_rejects_non_cent_price`).
4. **A horizon open trip larger than the day's bid depth.** For example, a
   20-unit open lot on a $60 product with depth 3. Expected: the units past
   depth are priced on the ladder's own ramp extended past the last level.
   This matches what the formula does in its exact limit, and the extension
   is documented as extrapolation for a hypothetical exit only. The test is
   in Task 4 (`test_book_sell_impact_beyond_depth_extends_ramp`).
5. **A buy and a sell, or two buys, on one asset on one day.** Expected:
   each side has its own full depth, and two buys consume the ask levels in
   arrival order. The book is not refilled between orders on the same day,
   and it is rebuilt fresh the next day. Tests are in Task 2 (`book: buy
   and sell same day each get full depth`, `book: second buy continues up
   the same ladder`, `book: ladder is rebuilt next day`).

---

### Task 1: Branch, shared book event, ladder builder

**Files:**
- Create: `cpp/src/pkmn_book/book_event.hpp`, `cpp/src/pkmn_engine/ladder.hpp`,
  `cpp/src/pkmn_engine/ladder.cpp`, `cpp/tests/test_ladder.cpp`
- Modify: `cpp/src/pkmn_md/events.hpp` (becomes an alias), `cpp/CMakeLists.txt`
  (`ladder.cpp` goes into `pkmn_engine_core`, `pkmn_engine_core` links
  `pkmn_book` PUBLIC, and `test_ladder.cpp` goes into `engine_tests`),
  `docs/benchmarks/iex-book.md:306-307`. That last edit fixes the parked
  phrase: "on the full 2024-12-23 day" becomes "on the 2024-12-23 files
  (an incident day; see docs/iex-oracle.md)".

**Interfaces:**
- Consumes: `pkmn::book::OrderBook`, `Side`, `Price`, `Qty` (Plan A), and
  `pkmn::CostModel::buy_base` and `max_daily_qty` (`costs.hpp`).
- Produces:
  - `pkmn::book::BookEvent`, moved verbatim from `pkmn::md::BookEvent`. Add
    `using BookEvent = book::BookEvent;` in `pkmn_md/events.hpp` so every
    md caller compiles unchanged.
  - In `ladder.hpp` (namespace `pkmn`):
    ```cpp
    enum class LadderPricing : std::uint8_t { Cent, ExactTest };
    std::int64_t ticks_per_dollar(LadderPricing p);   // 100 or 1'000'000'000
    struct DayQuote { double market, mid, low; };      // NaN = missing (as MarketView)
    struct LadderShape { double base, far; std::int64_t depth; bool flat; };
    LadderShape ask_shape(const DayQuote& q, const CostModel& cm);  // base = cm.buy_base(market, low), far = mid, depth = cm.max_daily_qty(base)
    LadderShape bid_shape(const DayQuote& q, const CostModel& cm);  // base = market, far = low, depth = cm.max_daily_qty(market)
    // Price of 0-based unit k on that side, in ticks; defined for every k >= 0
    // (k >= depth is the ramp's extension, used only by hypothetical exits).
    book::Price unit_ticks(const LadderShape& s, book::Side resting, std::int64_t k, LadderPricing p);
    // Clears `out`, then emits one one-unit Add per unit: asks k = 0..depth-1
    // with ids 1..depth, then bids with ids (1<<32)+1.., each through sink(const book::BookEvent&).
    template <class Sink> void build_day_ladder(const DayQuote& q, const CostModel& cm, LadderPricing p, std::uint64_t symbol, Sink&& sink);
    void build_day_ladder(const DayQuote& q, const CostModel& cm, LadderPricing p, book::OrderBook& out);  // applies the Adds
    ```
  - Unit price on a non-flat side (the spec's calibration anchor, computed
    in double and then rounded once):
    - asks: `price = base + (far - base) * (k + 0.5) / depth`
    - bids: `price = base - (base - far) * (k + 0.5) / depth`
    - `ticks = llround(price * ticks_per_dollar)`
  - The side is flat (every unit at `llround(base * tpd)`) when `far` is
    NaN or `far - base` has the wrong sign or is zero. That is
    `mid <= base` for asks and `low >= market` for bids.
  - The builder requires `cm.impact_enabled`, asserted in debug builds.
    Book mode is never reached with impact off.

- [ ] **Step 1: Branch and commit the plan**

```bash
git checkout feat/instrument-repair-horizon && git checkout -b feat/pokemon-book
git add docs/superpowers/plans/2026-10-23-pokemon-book.md && git commit -m "docs: Plan B (Pokemon order book) implementation plan"
```

- [ ] **Step 2: Write the failing Catch2 tests in `cpp/tests/test_ladder.cpp`**

Use `CostModel cm; cm.impact_enabled = true;` (default tiers 5/20, 50/8,
200/3, fallback 1).

- `ladder: cent asks on a 12/13 quote` uses `DayQuote{12.0, 13.0, 1.0}`.
  Asserts that `ask_shape` gives base 12, depth 8, not flat. It then
  asserts that `unit_ticks` for k = 0..7 equals
  `{1206, 1219, 1231, 1244, 1256, 1269, 1281, 1294}`, and that k = 8 gives
  1306 (the extension).
- `ladder: cent bids on a 12/low 11 quote` uses `DayQuote{12.0, NaN, 11.0}`.
  Bid depth is 8, and k = 0..7 gives
  `{1194, 1181, 1169, 1156, 1144, 1131, 1119, 1106}`.
- `ladder: floor lifts the ask base` uses `DayQuote{12.0, 24.0, 20.0}`.
  Asserts that `ask_shape` gives base 20 and depth 8 (tiered on 20).
- `ladder: crossed and missing quotes build flat books` covers four
  quotes:
  - `{12, 11, 1}`: mid is below base, so asks are flat at 1200.
  - `{12, NaN, 1}`: asks are flat at 1200.
  - `{12, 13, NaN}`: bids are flat at 1200.
  - `{12, 13, 12}`: low equals market, so bids are flat at 1200.
  Each has the full depth.
- `ladder: penny bid ladder never goes negative` uses
  `DayQuote{0.02, NaN, 0.01}`. Every bid `unit_ticks` for k in 0..39 is
  >= 0.
- `ladder: build_day_ladder books every unit`. On `{12, 13, 1}`, after
  `build_day_ladder(..., OrderBook&)`, `order_count()` is 16. `best(Sell)`
  is `{1206, qty 1}`, and `best(Buy)` is `{1131, qty 1}`. The bids ramp
  from market 12 to low 1, so k = 0 is `llround((12 - 11*0.5/8)*100) = 1131`.
- `ladder: exact pricing reproduces the formula`. Over the grid
  - market in {0.37, 4.99, 12.0, 49.5, 199.99, 1234.56}
  - mid in {NaN, market - 1, market + 0.13, market * 1.4}
  - low in {NaN, market * 0.6, market * 1.1}

  build both sides with `LadderPricing::ExactTest` and form the unit-price
  prefix sums. For every `0 <= used`, `1 <= qty`, `used + qty <= depth`:
  - `sum(ask units used..used+qty-1) / 1e9 - qty*market` matches
    `cm.buy_impact(market, mid, low, qty, used)` within `1e-6`;
  - `qty*market - sum(bid units)/1e9` matches
    `cm.sell_impact(market, low, qty, used)` within `1e-6`.

- [ ] **Step 3: Run and see the tests fail**

Run: `cmake -S cpp -B cpp/build -DPKMN_BUILD_TESTS=ON && cmake --build cpp/build -j && ctest --test-dir cpp/build -R ladder`
Expected: build error, because `pkmn_engine/ladder.hpp` does not exist.

- [ ] **Step 4: Move `BookEvent`, then implement `ladder.hpp/.cpp`**

The builder's header comment must state the model plainly:

> modelled resting orders (synthetic, built from the day's tcgcsv quote);
> tcgcsv publishes no bids, so the bid side is an assumption the formula
> already made implicitly

- [ ] **Step 5: Run the C++ suite on both hosts, the gates, and the Python rebuild**

Run:

```bash
ctest --test-dir cpp/build
orb -m ubuntu bash -lc 'cd /Users/ml3787/pkmn-stock && cmake -S cpp -B cpp/build-linux -DPKMN_BUILD_TESTS=ON && cmake --build cpp/build-linux -j && ctest --test-dir cpp/build-linux'
uv sync --group api --group dashboard --reinstall-package pkmn-quant
uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Expected: all pass (130 + the new ladder tests), and pytest still reads 615.

- [ ] **Step 6: Commit** (`feat(book): daily ladder builder for modelled Pokemon resting orders`)

---

### Task 2: Book execution in the C++ engine

**Files:**
- Create: `cpp/src/pkmn_engine/execution_book.hpp`,
  `cpp/src/pkmn_engine/execution_book.cpp`, `cpp/tests/test_execution_book.cpp`
- Modify: `cpp/src/pkmn_engine/costs.hpp` (new field only),
  `cpp/src/pkmn_engine/backtest.cpp` (dispatch), `cpp/bindings/module.cpp`
  (`execution` argument), `src/pkmn_quant/engine/native.py`
  (`NativeBacktest.execution`), `cpp/CMakeLists.txt`.
- Test: `tests/engine/test_native_book.py`

**Interfaces:**
- Consumes: Task 1 `ask_shape`/`bid_shape`/`build_day_ladder`, and
  `MarketView::price/mid/low`.
- Produces:
  - In `costs.hpp`, add
    `enum class ExecutionMode : std::uint8_t { Formula, Book, BookExactTest };`
    and the field `ExecutionMode execution = ExecutionMode::Formula;`.
  - `class DayBooks` in `execution_book.hpp` (namespace `pkmn`):
    ```cpp
    // Per-run pool of OrderBooks, one per asset that has an order today.
    class DayBooks {
      public:
        explicit DayBooks(std::size_t n_assets);
        void new_day();   // marks every book stale; storage is reused
        // Builds the asset's ladder on first touch today, then returns the same book.
        book::OrderBook& get(AssetId a, const DayQuote& q, const CostModel& cm, LadderPricing p);
    };
    std::vector<Fill> execute_book(const std::vector<Order>& orders, const MarketView& market,
                                   Portfolio& portfolio, Day day, const CostModel& cm, DayBooks& books);
    ```
  - `run_backtest` calls `execute_book` when
    `cm.impact_enabled && cm.execution != ExecutionMode::Formula`, and
    `execute` otherwise. It owns one `DayBooks` per run, so it is
    thread-safe across walk-forward folds.
  - The binding gains `execution: str` (`"formula"`, `"book"` or
    `"book-exact-test"`; anything else is `ValueError`), placed last before
    `callback`.
  - `NativeBacktest` gains `execution: str = "formula"`. Its `Result.cost_model`
    dict gains `"execution"` only when it is not `"formula"`, so formula
    results stay byte-identical.

**Fill rules in book mode.** Same order loop, skip rules and T+1 timing as
`execute()`. `Fill.price` stays the print.

- **Buy.**
  - The ask ladder is built on first touch today.
  - `qty = min(order.quantity, ask units left)`.
  - Then walk `depth(Sell, ...)` best-first, and keep the largest `qty`
    whose ladder notional plus `shipping_per_line` is <= cash. Compare
    `notional_ticks / tpd + shipping <= cash` in double, where
    `tpd = ticks_per_dollar(p)`.
  - Then call `match(Buy, qty, INT64_MAX, execs)`.
  - `impact = notional_ticks / tpd - qty * market`.
  - `fees = shipping_per_line`.
- **Sell.**
  - `qty = min(-order.quantity, position qty, bid units left)`.
  - Call `match(Sell, qty, 0, execs)`.
  - `fees = qty * market * fee_rate + shipping_per_line`, exactly as
    `fill_sell`.
  - `impact = qty * market - notional_ticks / tpd`.
- **Depth.** There is no shared `filled_today` counter: each side's
  remaining depth is what is left in its book.

- [ ] **Step 1: Write the failing Catch2 tests in `test_execution_book.cpp`**

Use a one-asset `MarketView` built the way `test_backtest_golden.cpp`
builds one, with the impact-on CostModel and `execution = Book`.

- `book: cent buy matches the hand ladder`.
  - Quote: market 12, mid 13, low 1. The order is buy 10 with cash 100.
  - The ladder cumsums are 12.06, 24.25, 36.56, 49.00, 61.56, 74.25,
    87.06, 100.00.
  - Expect a fill of qty 7, price 12.0, fees 1.0, and impact
    `87.06 - 84.0` (within 1e-9). Cash after is `100 - 88.06`.
- `book: buy and sell same day each get full depth`.
  - Quote: market 12, mid 13, low 11. The position is 20 units seeded,
    with cash 1000.
  - Orders, in order: sell 8, then buy 8.
  - Both fill 8. The formula path on the same input fills the buy at
    `8 - 8 = 0` and emits no buy fill, so assert the book emits 2 fills and
    `execute()` emits 1.
- `book: second buy continues up the same ladder`. Two buy orders of 3 on
  the 12/13/1 quote give fill impacts of
  `(12.06+12.19+12.31) - 36 = 0.56` and
  `(12.44+12.56+12.69) - 36 = 1.69`.
- `book: ladder is rebuilt next day`. Over two days, a buy of 8 on each
  day fills 8 both days (cash ample).
- `book: buy on crossed quote charges only the floor premium`.
  - Quote: market 12, mid 11, low 20. That gives base 20, a flat book,
    and depth 8.
  - Buy 2 with cash 1000 gives impact `(20 - 12) * 2 = 16.0` exactly.
- `book: exact mode equals formula on one-sided days`. Over a seeded
  random 40-day, 5-asset market of one-sided orders, run `run_backtest`
  with `BookExactTest` and with `Formula`. Fills match on day, asset and
  quantity. Price, fees and impact are within 1e-6, and equity within
  1e-6.
- `book: impact off ignores execution mode`. With `impact_enabled = false`
  and `execution = Book`, the result is identical (`==`) to the formula
  run.

- [ ] **Step 2: Run them and see them fail**

Run: `cmake --build cpp/build -j && ctest --test-dir cpp/build -R "book:"`
Expected: compile error, because `execution_book.hpp` is missing.

- [ ] **Step 3: Implement `execution_book.{hpp,cpp}`, the dispatch in `backtest.cpp`, the binding argument, and `NativeBacktest.execution`**

- [ ] **Step 4: Write `tests/engine/test_native_book.py`**

- `test_book_cent_golden_through_native`.
  - Uses a 3-day warehouse: D1 (10/mid 11), D2 (12/mid 13), D3 (15/mid 16),
    every low 1.0.
  - Strategy `NativeStrategySpec("buy-and-hold", {})`, cash 100, impact
    on, `execution="book"`.
  - Equity is `approx([100.0, 95.94, 116.94], abs=1e-9)`.
  - One fill: qty 7, impact `approx(3.06, abs=1e-9)`.
- `test_formula_default_unchanged`. The same run without `execution` gives
  equity `approx([100.0, 95.9375, 116.9375])` and impact
  `approx(3.0625)`.
- `test_unknown_execution_rejected`. `execution="fast"` raises `ValueError`.

- [ ] **Step 5: Rebuild, run the C++ suite on both hosts, run pytest including `tests/test_native_parity.py`, then the gates**

Expected: all pass, and the parity tests are unchanged.

- [ ] **Step 6: Commit** (`feat(engine): book execution against daily modelled ladders`)

---

### Task 3: The `--execution` switch, end to end

**Files:**
- Modify:
  - `src/pkmn_quant/research/backtest_run.py`: an `execution` kwarg on
    `run_single_backtest` and `run_with_benchmark`, with default `"book"`,
    recorded in config.
  - `src/pkmn_quant/research/walkforward.py`: an `execution` kwarg, with
    default `"formula"` at library level. `engine == "python"` with `"book"`
    raises `ValueError("--execution book needs the cpp engine; the Python engine is the frozen formula reference")`.
  - `src/pkmn_quant/research/artifacts.py`: `write_walkforward_json(...,
    execution: str = "formula")` writes the `"execution"` key.
  - `src/pkmn_quant/cli.py`: `--execution` on `backtest` and `walkforward`,
    plus run output.
  - `src/pkmn_quant/api/models.py`: `BacktestRequest.execution:
    Literal["book", "formula"] = "book"`.
  - `src/pkmn_quant/api/app.py`: pass the field through.
  - `web/src/api/types.ts` and `web/src/pages/NewRun.tsx`: a select
    defaulting to book, labelled "Execution: book (modelled resting
    orders) / formula".
  - `web/src/pages/NewRun.test.tsx`.
- Test: `tests/test_cli_backtest.py`, `tests/test_cli_walkforward.py`,
  `tests/research/test_backtest_run.py` (or the existing file covering
  `run_single_backtest`), `tests/api/` (request validation).

**Interfaces:**
- Consumes: Task 2 `NativeBacktest(execution=...)`.
- Produces:
  - `effective_execution(execution: str, impact: bool) -> str` in
    `backtest_run.py`. It returns `"formula"` when `impact` is False, and
    otherwise validates `execution in {"book", "formula"}`, raising
    `ValueError` on anything else.
  - Every new `backtest` and `walkforward` registry config has
    `config["execution"]`, set to the effective value.
  - `run_with_benchmark` runs the paired benchmark with the same effective
    mode.
- CLI help for `--execution`:

  > book (default): fills walk modelled resting orders (synthetic, built
  > from the day's tcgcsv quote), whole-cent levels. formula: the
  > continuous walk-the-spread charge. --no-impact overrides both.

  After `strategy: ...`, the CLI prints `execution: book (modelled resting
  orders, synthetic)` or `execution: formula`.

- [ ] **Step 1: Write the failing tests**

- **Existing goldens.**
  - Add `"--execution", "formula"` to the `run_cli(...)` calls in the
    impact goldens `test_backtest_golden_numbers_with_impact` and
    `..._with_price_floor`.
  - Their numbers stay as they are, per the spec's "formula goldens stay".
  - `test_backtest_golden_numbers` (impact off) gets no flag and must still
    pass, because impact-off is formula.
- **`test_backtest_golden_numbers_book`.** New fixture `seed_tick`: D1 10/11,
  D2 12/13, D3 15/16, low 1.0. It runs the default CLI (book) and asserts:
  - equity `approx([100.0, 95.94, 116.94], abs=1e-9)`;
  - one fill, qty 7, price 12.0, fees 1.0, impact `approx(3.06, abs=1e-9)`.

  The docstring carries the hand derivation from Task 2 (the ladder
  12.06 … 12.94, cumsum 87.06 <= 99 < 100.00).
- **`test_backtest_formula_on_tick_fixture`.** The same fixture with
  `--execution formula` gives equity `approx([100.0, 95.9375, 116.9375])`.
  This shows the 0.0025 gap is cent discreteness.
- **`test_backtest_records_execution`.** Assert the registry record has
  `config["execution"] == "book"` by default, `"formula"` with
  `--execution formula`, and `"formula"` with `--no-impact`.
- **`test_backtest_bad_execution_clean_error`.** `--execution fast` exits
  non-zero, and the output names the valid values.
- **`test_walkforward_python_engine_rejects_book`.** `--engine python
  --execution book` exits non-zero with the message above.
- **`test_walkforward_records_execution`.** On the existing small
  walkforward fixture, `walkforward.json["execution"] == "book"` and the
  registry config agrees.
- **API.** A `POST /api/backtest` body without `execution` validates to
  `"book"`. `"fast"` gets 422.
- **Web.** NewRun submits `execution: "book"` by default.

- [ ] **Step 2: Run them and see them fail**

Run: `uv run pytest tests/test_cli_backtest.py tests/test_cli_walkforward.py tests/api -q`
Expected: the new tests FAIL with "No such option: --execution".

- [ ] **Step 3: Implement**

- [ ] **Step 4: Run the full gates plus `cd web && npm run check && npm run build`**

Expected: all pass.

- [ ] **Step 5: Commit** (`feat: --execution book|formula on backtest, walkforward and the web trigger`)

---

### Task 4: No mixing — evaluate and horizon guards, book exits, live-path notes

**Files:**
- Modify:
  - `src/pkmn_quant/cli.py`: `evaluate`, `horizon` and `_benchmark_candidates`.
  - `src/pkmn_quant/research/horizon.py`: the exit-impact hook.
  - `cpp/bindings/module.cpp` and `src/pkmn_quant/engine/native.py`: the
    `book_sell_impact` binding and wrapper.
  - `src/pkmn_quant/live/paper.py` and the `signals` command docstring in
    `cli.py`: a mismatch note.
  - `cpp/src/pkmn_engine/execution_book.{hpp,cpp}`: the hypothetical-sale
    helper.
- Test: `tests/test_cli_evaluate.py`, `tests/test_cli_horizon.py`,
  `tests/engine/test_native_book.py`, `cpp/tests/test_execution_book.cpp`.

**Interfaces:**
- Consumes: Task 1 `bid_shape`/`unit_ticks`, and Task 3 `config["execution"]`
  and `walkforward.json["execution"]`.
- Produces:
  - C++, in `execution_book.hpp`: `double hypothetical_sell_impact(const DayQuote& q, const CostModel& cm, LadderPricing p, std::int64_t qty);`
    - Units `0..min(qty, depth)-1` are matched against a freshly built
      bid book.
    - Units `depth..qty-1` are priced with `unit_ticks` on the extended
      ramp.
    - Returns `qty*market - notional/tpd`.
  - Binding:
    `_engine.book_sell_impact(market, low, qty, tier_thresholds, tier_qtys, fallback_max_qty) -> float`,
    where `low` is NaN for missing and pricing is always Cent.
  - Python, in `native.py`:
    `book_sell_impact(cost_model: CostModel, market: float, low: float | None, qty: int) -> float`.
  - `horizon_report(..., exit_impact: Callable[[float, float | None, int], float] | None = None)`.
    `None` keeps `costs.sell_impact`, so existing callers and tests are
    unchanged.
  - `_benchmark_candidates(records, execution: str)` filters on
    `r.config.get("execution", "formula") == execution`.

**Rules.**
- **`pkmn evaluate`.**
  - Every discovered `wf-*` artifact's `execution` (missing means formula)
    must be one value. Otherwise it fails with
    `error: walk-forward artifacts mix execution modes (book: a, b; formula: c); evaluate one mode at a time`.
  - The auto benchmark is the newest candidate in that mode. An explicit
    `--benchmark <dir>` is looked up in the registry by `artifact_path`;
    a mode mismatch fails, and no record found means formula.
  - The mode goes into the evaluate config and `evaluate.json` as
    `"execution"`.
- **`pkmn horizon`.**
  - The benchmark must share the run's mode. Explicit mismatch fails, and
    auto picks the newest in-mode candidate.
  - When the run is book, the still-open exit uses
    `book_sell_impact(run_costs, ...)`.
  - The mode goes into the horizon config and JSON.
- **Live paths** (`signals`, `daily --paper`, `live/paper.py`). They keep
  the formula `CostModel`. A comment at the pricing site and one line in
  the `signals` help say so:

  > Live and paper pricing use the formula cost model, not book execution;
  > research runs default to book, so a recommendation's quoted outlay can
  > differ from what a book backtest would have charged.

- [ ] **Step 1: Write the failing tests**

- C++ `book: hypothetical sale beyond depth extends the ramp`.
  - Quote market 60, low 50 gives depth 3 (tier of $60).
  - For qty 5, the impact is
    `5*60 - (sum of unit_ticks k=0..4 on bid_shape)/100`.
  - At `ExactTest`, it equals `cm.sell_impact(60, 50, 5, 0)` within 1e-6.
- `test_book_sell_impact_beyond_depth_extends_ramp` (Python binding). It
  asserts `book_sell_impact(CostModel(impact_enabled=True), 60.0, 50.0, 5)`
  equals the hand-computed cent value, and that it is within $0.03 of
  `sell_impact(60, 50, 5)`.
- `test_book_sell_impact_no_low_is_zero`. `low=None` gives 0.0.
- `test_evaluate_refuses_mixed_execution`. Two `wf-*` artifacts are
  hand-written with `execution` book and formula. The run exits 1 and the
  message names both.
- `test_evaluate_picks_benchmark_in_mode`. Two buy-and-hold records are in
  the registry. The newer one is formula and the older one is book. With
  book artifacts, the report's benchmark is the book one.
- `test_evaluate_legacy_artifacts_are_formula`. Artifacts without the key
  plus a record without the key evaluate fine, with mode formula.
- `test_horizon_refuses_benchmark_in_other_mode`. An explicit
  `--benchmark` with the other mode exits 1.
- `test_horizon_book_run_uses_book_exit`. On a book run with a still-open
  lot, the open trip's `strategy_return` is computed with the book exit.
  Assert it differs from the formula-exit value computed through
  `horizon_report` with `exit_impact=None` on the same inputs, and that it
  equals the value computed with `exit_impact` set to the book function.

- [ ] **Step 2: Run them and see them fail**

Run: `uv run pytest tests/test_cli_evaluate.py tests/test_cli_horizon.py tests/engine/test_native_book.py -q`
Expected: the new tests FAIL.

- [ ] **Step 3: Implement**

- [ ] **Step 4: Rebuild, run the C++ suite on both hosts, then the full gates**

Expected: all pass.

- [ ] **Step 5: Commit** (`feat: refuse book/formula mixing in evaluate and horizon; book-priced open exits`)

---

### Task 5: Pokemon wire protocol and codec

**Files:**
- Create:
  - `docs/pokemon-wire-protocol.md`
  - `cpp/src/pkmn_wire/wire.hpp` (layout constants and `WireEvent`)
  - `cpp/src/pkmn_wire/encoder.{hpp,cpp}`
  - `cpp/src/pkmn_wire/decoder.{hpp,cpp}`
  - `cpp/tests/test_wire.cpp`
- Modify: `cpp/CMakeLists.txt`. Add a `pkmn_wire` STATIC library (no zlib;
  it builds under SKBUILD too; `-ffp-contract=off` PUBLIC; PIC; it links
  `pkmn_engine_core` PUBLIC). Add `test_wire.cpp` to `engine_tests`.

**Layout** (pinned here so that the encoder, decoder, Plan C and the doc
agree). Everything is little-endian and packed.

- **Packet.** A 16-byte header:

  | field | type | value |
  |---|---|---|
  | `magic` | u32 | the bytes `"PKW1"` |
  | `msg_count` | u16 | |
  | `payload_len` | u16 | |
  | `first_seq` | i64 | starts at 1 per stream |

  The header is followed by `msg_count` messages, each written as a
  `u16 len` (bytes after this field) and then a `u8 type` and the body.
  The encoder caps a packet at 1400 total bytes, which is UDP-ready for
  Plan C.

- **Messages.**

  | type | body | len |
  |---|---|---|
  | `'D'` Instrument | instrument u32, product_id i64, sub_type_len u8, sub_type char[24] (zero-padded) | 38 |
  | `'S'` DayStart | day i32 (days since 1970-01-01) | 5 |
  | `'Q'` Quote | instrument u32, market u32, low u32, mid u32, high u32 (whole cents; `0xFFFFFFFF` = null) | 21 |
  | `'E'` DayEnd | day i32, quote_count u32 | 9 |
  | `'T'` Trade | instrument u32, price u32 (cents), qty u32, ts_ns i64 | 21 |

  - Instrument ids are dense from 0, ordered by `(product_id, sub_type)`.
  - All `D` messages precede the first `S`.
  - `Q` is ordered by instrument within a day, and days ascend.
  - `T` is reserved for the spec's future trade feed. The encoder never
    emits it, and the decoder decodes it.
- **Decoder rules.**
  - Copy the IEX lessons: skip unknown types, and accept a `len` longer
    than the body by reading the known prefix and skipping the rest.
  - Count a short message or packet (`short_messages`, `short_packets`)
    and stop that packet; never read past the buffer.
  - Sequence each packet with `pkmn::md::SequenceTracker` (header-only
    `pkmn_md/sequence.hpp`) and expose its counters.

**Interfaces:**
- Produces (namespace `pkmn::wire`):
  ```cpp
  struct WireEvent {
      enum class Kind : std::uint8_t { Instrument, DayStart, Quote, DayEnd, Trade };
      Kind kind; std::uint32_t instrument; std::int32_t day;
      std::int64_t product_id; std::string_view sub_type;     // Instrument only (views the packet)
      std::uint32_t market, low, mid, high;                    // Quote, cents, kNull = 0xFFFFFFFF
      std::uint32_t price, qty; std::int64_t ts_ns;            // Trade
      std::uint32_t quote_count;                               // DayEnd
  };
  inline constexpr std::uint32_t kNull = 0xFFFFFFFFu;
  struct EncodeInput {  // parallel arrays, rows sorted (day, instrument)
      std::span<const std::int64_t> inst_product_id; std::span<const std::string> inst_sub_type;
      std::span<const std::int32_t> row_day; std::span<const std::uint32_t> row_instrument;
      std::span<const std::uint32_t> row_market, row_low, row_mid, row_high;  // cents or kNull
  };
  // Writes packets to `out`; returns messages written. Throws std::invalid_argument on unsorted rows or sub_type > 24 bytes.
  std::uint64_t encode(const EncodeInput& in, std::ostream& out);
  struct DecodeStats { std::uint64_t packets, messages, unknown, short_messages, short_packets, gaps, gap_messages, duplicates; };
  class Decoder {
    public:
      // Calls sink(const WireEvent&) per message; never allocates per message.
      template <class Sink> void packet(std::span<const std::byte> bytes, Sink&& sink);
      const DecodeStats& stats() const;
  };
  // Streams a file packet by packet (reads header, then payload_len bytes).
  template <class Sink> DecodeStats decode_file(const std::string& path, Sink&& sink);
  ```
  The encoder's cents arrays are produced by the Python side (Task 6),
  which owns the cent-exactness check.

- [ ] **Step 1: Write `docs/pokemon-wire-protocol.md`**

It contains the tables above, byte offsets per message, an annotated hex
dump of one packet (a D, then an S, then a Q), the ordering rules, the
null sentinel, and the reserved Trade. Its first paragraph says the wire
carries quotes, not ladder levels, and why (from the spec: on the order of
a billion one-unit levels, and the cost settings would freeze into the
file).

- [ ] **Step 2: Write the failing Catch2 tests in `test_wire.cpp`**

- `wire: round trip`.
  - Encode 3 instruments (one with sub_type "1st Edition Holofoil", 20
    bytes) over 2 days, with one null low.
  - Decode it back and check that the event sequence equals D, D, D, S,
    Q, Q, Q, E, S, …, field for field.
  - `stats.gaps == 0`.
- `wire: packets stay under 1400 bytes`. With 500 instruments, every
  packet's `16 + payload_len <= 1400`, and seq is contiguous.
- `wire: unknown type skipped, longer message accepted`. A hand-built
  packet holds a type `'Z'` message of len 7, then a Q with len 25 (4
  extra bytes). The decoder gives one Quote event with the right fields,
  and `unknown == 1`.
- `wire: truncated packet counted, no overread`. A packet cut mid-Quote
  gives `short_messages == 1`, and earlier messages are still delivered.
  Run under the existing ASan build dir if present.
- `wire: gap detected`. Dropping packet 2 of 4 gives `gaps == 1`, with
  `gap_messages` equal to that packet's count.
- `wire: trade message decodes`. A hand-built T gives a Trade event with
  price, qty and ts_ns intact.
- `wire: encoder rejects unsorted rows`. This throws
  `std::invalid_argument`.

- [ ] **Step 3: Run them and see them fail**

Run: `cmake --build cpp/build -j && ctest --test-dir cpp/build -R "wire:"`
Expected: compile error, because there is no `pkmn_wire`.

- [ ] **Step 4: Implement `wire.hpp`, `encoder.cpp` and `decoder.cpp`**

- [ ] **Step 5: Run the C++ suite on both hosts, rebuild, then the gates**

Run the C++ suite on both hosts, then
`uv sync --group api --group dashboard --reinstall-package pkmn-quant` (the
wheel now compiles `pkmn_wire`), then the gates. Expected: all pass.

- [ ] **Step 6: Commit** (`feat(wire): ITCH-style Pokemon quote protocol, encoder and decoder`)

---

### Task 6: Replay path, encoder CLI, and the identical-books proof

**Files:**
- Create:
  - `cpp/src/pkmn_wire/replay.hpp`: decoder events to per-asset-day
    ladders through `build_day_ladder`.
  - `cpp/tools/pokemon_replay.cpp`
  - `src/pkmn_quant/wire.py`
  - `tests/test_wire.py`
- Modify:
  - `cpp/bindings/module.cpp`: `encode_wire`, `wire_ladder_digests` and
    `ladder_digests`.
  - `cpp/CMakeLists.txt`: the `pokemon_replay` tool, under `NOT SKBUILD`
    with the other tools.
  - `src/pkmn_quant/cli.py`: a `wire` sub-app with `encode`.

**Interfaces:**
- Consumes: Task 5's codec, Task 1's `build_day_ladder`, and
  `PreparedMarket` row arrays.
- Produces:
  - `std::uint64_t ladder_digest(const book::OrderBook& b)` in `replay.hpp`.
    It is FNV-1a 64 over `depth(Sell, all)` and then `depth(Buy, all)`,
    hashing `(price, qty, orders)` per level and side tags.
  - Bindings:
    - `_engine.encode_wire(inst_product_id: int64[], inst_sub_type: list[str], row_day: int32[], row_instrument: uint32[], row_market/low/mid/high: uint32[], path: str) -> int`
      returns the number of messages written.
    - `_engine.wire_ladder_digests(path, tier_thresholds, tier_qtys, fallback_max_qty) -> (day: int32[], product_id: int64[], sub_type: list[str], digest: uint64[], stats: dict)`.
      It builds a Cent ladder for every Quote with a non-null market. The
      quote's doubles are `cents / 100.0`, and null maps to NaN.
    - `_engine.ladder_digests(row_day, row_asset, row_market, row_mid, row_low, tier_thresholds, tier_qtys, fallback_max_qty) -> uint64[]`
      returns one digest per row, through the backtester's own `DayQuote`
      path (the doubles straight from `PreparedMarket`).
  - Python, in `src/pkmn_quant/wire.py`:
    ```python
    def to_cents(values: pl.Series, *, what: str, frame: pl.DataFrame) -> NDArray[np.uint32]  # null -> 0xFFFFFFFF
    def encode_window(warehouse: Warehouse, start: date, end: date, out: Path) -> int
    ```
    - `encode_window` loads `warehouse.load_prices()` filtered to
      `[start, end]`, which is exactly `MarketData.from_frame`'s filter
      with warmup 0.
    - Instruments are numbered by `(product_id, sub_type)` sorted. Rows are
      sorted by `(date, instrument)`.
    - The function returns the number of messages.
  - `to_cents` raises `ValueError` when `round(v*100)/100 != v` or
    `v*100 >= 0xFFFFFFFF`. The message names product_id, sub_type, date
    and column.
- CLI: `pkmn wire encode --start --end --out data/wire/<start>_<end>.pkw`
  prints the messages written, the bytes, and the time taken.
- Tool: `pokemon_replay <file.pkw> [--no-ladders] [--repeat N]` streams the
  file. With ladders on, it builds every Quote's ladder into one reused
  `OrderBook`. It prints JSON with messages, quotes, seconds, msgs_per_s,
  ladders_built and the decode stats, using `timing.hpp`'s clock (from
  `pkmn_md`, header-only).

- [ ] **Step 1: Write the failing tests in `tests/test_wire.py`**

- `test_encode_rejects_non_cent_price`. A warehouse row with market
  12.345 makes `encode_window` raise `ValueError` matching
  `r"product_id=1 .*market"`.
- `test_encode_rejects_sentinel_overflow`. A row with high 42949672.95
  raises.
- `test_wire_and_backtester_build_identical_books`.
  - Seed a 5-day, 4-product warehouse in `tmp_path`. Cover: one null low,
    one null mid, one crossed quote, one floor case (market < low), a
    multi-sub_type product, and prices across all four tiers including
    $250.
  - Then `encode_window`, and digest it with `wire_ladder_digests`.
  - Digest `PreparedMarket.prepare(...)` rows with `ladder_digests`.
  - Join on `(day, product_id, sub_type)`. Every row is present on both
    sides, and every digest is equal.
- `test_wire_cli_encode`. The CLI writes the file. The printed message
  count equals instruments + 2*days + quotes.

- [ ] **Step 2: Run them and see them fail**

Run: `uv run pytest tests/test_wire.py -q`
Expected: FAIL with ImportError on `pkmn_quant.wire`.

- [ ] **Step 3: Implement `replay.hpp`, the bindings, `wire.py`, the CLI and `pokemon_replay`**

- [ ] **Step 4: Rebuild, run the C++ suite on both hosts, then the full gates**

Expected: all pass.

- [ ] **Step 5: Real-window identity check (no commit of data)**

Run:

```bash
uv run pkmn wire encode --start 2026-06-01 --end 2026-06-30 --out data/wire/2026-06.pkw
uv run python -c "from tests.test_wire import check_real_window; check_real_window('data/wire/2026-06.pkw', '2026-06-01', '2026-06-30')"
```

Expose `check_real_window(path, start, end)` as a plain helper in
`tests/test_wire.py` that asserts the join is complete and the digests are
equal. It prints the row count and `0 mismatches`.

Expected: about 1.3M asset-days with 0 mismatches. Record the exact count
in the task report.

- [ ] **Step 6: Commit** (`feat(wire): replay path, pkmn wire encode, and identical-books proof`)

---

### Task 7: Real-data equivalence and measurements

**Files:**
- Create:
  - `scripts/book_equivalence.py`
  - `scripts/bench_pokemon_book.py`
  - `scripts/bench_pokemon_replay.sh` (re-execs in the VM like
    `scripts/bench_iex.sh`)
  - `docs/benchmarks/pokemon-book.md`
- Modify: `cpp/bindings/module.cpp`, adding
  `_engine.ladder_equivalence(row_market, row_mid, row_low, tier_thresholds, tier_qtys, fallback_max_qty) -> dict`.

**Interfaces:**
- `ladder_equivalence` handles each row with a printed market. For each
  side, it builds the `ExactTest` ladder into an `OrderBook`. It then takes
  the units out one at a time with `match(..., 1, ...)`, which runs the
  book's priority ordering, and forms prefix sums. Finally it compares
  every `(used, qty)` pair with `used + qty <= depth` against
  `CostModel::buy_impact`/`sell_impact`.
- It returns `{"rows", "checks", "max_abs_err", "worst_row", "worst_side", "worst_used", "worst_qty"}`.

- [ ] **Step 1: `scripts/book_equivalence.py --start 2024-03-01 --end 2026-06-30`**

The script has three parts, and none of them is a pytest. It is a
real-data gate run once, and the result goes in the doc.

- **(a) Pricing equivalence.** Run `ladder_equivalence` over every row of
  the window. The tolerance is `max_abs_err <= 1e-6`; the script exits 1
  otherwise.
- **(b) End to end.**
  - Run each of the five native strategies (default params, $10,000,
    impact on) with `execution="formula"` and with the test-only
    `"book-exact-test"`, through `NativeBacktest` directly.
  - On asset-days where only one side filled, the fills match on day,
    asset and quantity, and impact is within 1e-6.
  - Report how many fills fall on two-sided asset-days. This is the
    spec's "count how many real asset-days the change touches", measured
    on fills.
  - ml-ranker and ml-ranker-v2 are skipped. They take 75-140 min through
    the bridge, and the doc says so.
- **(c) Cent vs formula.** For the same five strategies, report total
  return, fill count and total impact under `"book"` vs `"formula"`.
  - Label the table: "capability check, default params, NOT a research
    result — Plan D owns the re-run".

Expected: (a) and (b) pass. Record every number in the doc.

- [ ] **Step 2: `uv run python scripts/parity_full.py`**

Expected: PASS bit-for-bit on all five native strategies. This is the
formula path unchanged.

- [ ] **Step 3: `scripts/bench_pokemon_book.py`**

- **Encode.** Measure the wall-clock of `pkmn wire encode` over the full
  warehouse (`2024-02-08` to the last ingested day) into
  `data/wire/full.pkw`. Record the messages and the bytes.
- **Backtest cost.** Record the full-window (2024-03-01 to 2026-06-30)
  backtest wall-clock, book vs formula, best of 3 interleaved, for
  buy-and-hold and dip-buyer.
- **Walk-forward cost.** Record one walk-forward
  (sealed-accumulation, trials 15, `--workers 1`) in book and in formula,
  run once each. Say that the walk-forward is n=1.
- **Output.** A JSON block with the git SHA and dirty flag, the date and
  the host (macOS M1 Pro here).

- [ ] **Step 4: `scripts/bench_pokemon_replay.sh`**

In the VM, Release build, it runs `pokemon_replay data/wire/full.pkw` with
and without ladders, 3 interleaved runs each, and reports msgs/s best and
median, plus spread. This is the spec's "Pokemon replay throughput, with
and without building each day's ladders". The script states the hardware
string "Apple M1 Pro, 7-vCPU OrbStack Ubuntu 24.04 arm64 VM", the compiler
and the flags.

- [ ] **Step 5: Write `docs/benchmarks/pokemon-book.md`**

It records the method, hardware, date, SHA, all of the numbers above, the
equivalence results, the two-sided count, and the synthetic-orders label.
Write it in the style of `docs/benchmarks/iex-book.md`.

- [ ] **Step 6: Run the gates, then commit** (`bench: Pokemon book equivalence, replay throughput and backtest cost`)

---

### Task 8: Write-up

**Files:**
- Modify: `CLAUDE.md` (Status, Commands, Layout, and Gotchas), `README.md`
  (the C++ section and the test-count badge).

- [ ] **Step 1: CLAUDE.md**

- **Status bullet.** Add a "Plan B (Pokemon order book) complete on
  feat/pokemon-book" bullet. It carries the test counts as observed, the
  equivalence numbers, the identical-books count, the replay and backtest
  costs, the two-sided-fill count, and the default change. The default
  change is that `pkmn backtest` and `walkforward` now default to
  `--execution book`.
- **What the bullet must say plainly:**
  - the resting orders are synthetic;
  - no research number was re-run, because that is Plan D;
  - every pre-Plan-B registry record reads as formula.
- **Commands.** `pkmn wire encode`, `pokemon_replay`, and the two scripts.
- **Layout.** `ladder.*`, `execution_book.*`, `pkmn_wire/`, and
  `docs/pokemon-wire-protocol.md`.
- **Gotchas.**
  - Book vs formula comparisons are refused by evaluate and horizon.
  - Live paths are still formula.
  - Cent rounding is to nearest.
  - Each side has its own depth.

- [ ] **Step 2: README**

Add one paragraph on the Pokemon book (equivalence plus identical books,
with the synthetic label) and update the badge counts to the observed
pytest, vitest and Catch2 numbers.

- [ ] **Step 3: Run the full gates, then commit** (`docs: Plan B status, commands and gotchas`)
