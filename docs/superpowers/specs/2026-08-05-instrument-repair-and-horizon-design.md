# Instrument Repair and Horizon-Matched Evaluation — Design

**Status:** design approved, not yet planned
**Date:** 2026-08-05
**Predecessor:** `docs/superpowers/specs/2026-08-03-full-set-coverage-design.md`

## Goal

Make this project able to answer the question its owner actually has:

> Over a 1-3 month window, does an active strategy beat simply holding sealed
> over that same window?

Today it cannot answer that, for three reasons. The benchmark it compares
everything against is 95% idle cash. The cost model prices buys from a
number nothing is offered at. And every result is reported as a single
stitched return over 28 months, which cannot distinguish a strategy that
wins on 2-month holds from one that wins on 2-week holds.

This design fixes the two broken instruments and adds the missing
measurement. It is repair and measurement work. **It is not expected to
make any strategy profitable**, and success does not depend on that.

## Background: measured facts this design rests on

All measured 2026-08-04/05 against the live 908-day warehouse.

**The benchmark is barely invested.** `buy_and_hold` allocates
`cash / len(universe)` and floors to integer units
(`src/pkmn_quant/strategies/buy_and_hold.py:38-43`). The universe went from
45 day-one sealed assets to 1,315, so the per-asset budget went $222 → $7.60
and most sealed products cost more than that. Result: $466.78 of $10,000
deployed (4.7%), versus $6,012.55 (60.1%) pre-swap. The published +10.53%
is a return on 4.7% of the account, and is not comparable to the pre-swap
+183.67% on 60.1%.

**The day-one sealed universe.** 1,314 sealed products priced on
2024-03-01; median market $37.03, p25 $17.33, p75 $87.67. The *most liquid*
sealed products are the expensive ones: median market among the top 100 by
print density is $103.77. A larger N therefore makes deployment worse, not
better.

(1,314 here is a direct warehouse count of sealed rows on 2024-03-01 with
`market > 0`. The 1,315 quoted for the engine's day-one universe counts
`ctx.marks`, which includes carry-forward marks. The one-row difference is
expected and neither number is wrong; a plan should quote whichever matches
what it is measuring.)

**Deployment by N, equal-dollar first pass only** ($10,000, day-one prices,
screened as below):

| N | per-asset budget | deployed | assets holding units |
|---:|---:|---:|---:|
| 25 | $400.00 | $6,150.67 (61.5%) | 19 / 25 |
| 50 | $200.00 | $5,439.47 (54.4%) | 32 / 50 |
| 100 | $100.00 | $5,590.78 (55.9%) | 69 / 100 |
| 200 | $50.00 | $2,935.15 (29.4%) | 72 / 200 |
| 400 | $25.00 | $1,413.35 (14.1%) | 73 / 400 |

Screening alone tops out near the pre-swap 60% — it fixes *which* assets,
not the remainder integer sizing leaves on each one.

**Deployment with the deficit sweep** (same inputs, algorithm below):

| N | deployed | assets holding units | residual cash |
|---:|---:|---:|---:|
| 25 | $9,984.62 (99.8%) | 20 / 25 | $15.38 |
| 50 | $9,995.86 (100.0%) | 36 / 50 | $4.14 |
| 100 | $9,994.45 (99.9%) | 73 / 100 | $5.55 |
| 200 | $9,996.72 (100.0%) | 78 / 200 | $3.28 |

**Print density barely discriminates at the first bar.** Only 22 archive
days precede 2024-03-01 (`EARLIEST_ARCHIVE_DATE = 2024-02-08`), so the
trailing density of the top 400 sealed products all saturates at 22 days.
The screen needs a secondary sort key that does real work, not a
tie-break on `product_id`.

**`market` and `low` are different data series, not inconsistent versions
of one.** `src/pkmn_quant/data/transforms.py:42-45` maps them straight
through from tcgcsv: `market` ← `marketPrice`, `low` ← `lowPrice`, `mid` ←
`midPrice`. TCGplayer derives `marketPrice` from recent completed **sales**;
`lowPrice`/`midPrice`/`highPrice` describe current **listings**. So
`market < low` is not a corrupt row — it is what you observe when recent
sales cleared below the cheapest listing currently available. 2,191,059
in-window rows across 15,186 products are in this state (7.43% of
pre-existing rows, 6.29% of newly-added ones).

The impact model assumes `market` is the front of the book and walks
outward from it (`engine/quotes.py`, cost model). That premise is wrong,
and it is what produced fills at $936/unit on a day whose cheapest listing
was $1,800 — the mechanism behind two of the four catastrophic strategy
losses.

**The open question this closes.** `docs/research-findings-2026-07.md:1482`
records "whether `market < low` is a tcgcsv data defect or a real (thin)
print" as unestablished, with the two readings implying opposite fixes.
The answer is neither: it is a category error in the consumer. This design
records that resolution.

## Component 1: liquidity-screened, fully-deployed benchmark

`src/pkmn_quant/strategies/buy_and_hold.py` gains a two-stage allocation,
applied once at first entry. The benchmark buys once and holds, so no
rolling re-screen is built — there would be no consumer for it.

### The screen

From the sealed products with a mark on the first bar, rank by:

1. **print density** — count of distinct dates with a mark for that
   `product_id` across all of `ctx.history`, descending. `ctx.history`
   contains only rows with `date <= today`, so this is leakage-bounded by
   construction. No lookback-window parameter is introduced: at first entry
   the available history *is* whatever precedes the backtest start, so a
   window would be a knob with nothing to tune against. This does mean
   density depends on the backtest start date — with a 2024-03-01 start it
   sees 22 days — which is precisely why the secondary key below carries
   the discriminating load.
2. **relative spread** `(mid - low) / mid`, ascending. Does the
   discriminating work that density cannot at the first bar, and doubles as
   a quote-sanity filter: products whose listings are internally consistent
   sort ahead of those whose are not. If `mid` is null or `<= 0`, treat the
   spread as infinite so the product sorts last.
3. **`product_id`**, ascending — final deterministic tie-break.

Keep the top `n_assets`.

### The allocation

**Pass 1, equal dollar:** `target = cash / n_assets`; for each screened
asset in screen order, `qty = floor(target / price)`.

**Pass 2, deficit sweep:** while any screened asset has
`price <= remaining_cash`, buy one unit of whichever has the largest
`target_dollars - held_dollars`; ties break by `product_id` ascending.
Repeat until nothing is affordable.

The sweep converges toward equal *dollar* weight rather than toward cheap
or early assets, which is what makes it safe — a naive first-come sweep
would tilt the benchmark toward whichever assets the loop reached first.
Deterministic tie-breaking at both stages is required for bit-for-bit C++
parity, not merely for tidiness.

### Parameters

`n_assets` becomes a `ParamSpec(name="n_assets", kind=int, default=100,
low=25, high=400)` on the registry entry, like every other hyperparameter —
CLI-settable, walk-forward-searchable, and declarative.

Default 100: it holds units in 73 assets at 99.9% deployment, the widest
diversification among the measured options that fully deploys. N=200 holds
5 more assets but reaches further down the liquidity ranking for them.

## Component 2: executable price floor for buys

In the cost model, a buy's base price becomes `max(market, low)` before
market impact walks it toward `mid`. You cannot lift an offer that does not
exist. When `low` is null, the base stays `market` — there is nothing
better to use.

Sells are **deliberately unchanged**. The symmetric fix would need a bid
series, and tcgcsv publishes none: `low`/`mid`/`high` are all ask-side.
Inventing a sell-side floor would be fabricating a price. This asymmetry is
intentional and must be documented where the cost model is described, not
left for a reader to discover.

**Marking is also unchanged.** This component changes the price a fill
executes at, not the price a held position is valued at — positions
continue to mark at `market`. That combination is what produces the
immediate unrealised loss described below, and it is the correct
combination: you really did pay the ask, and the position really is worth
what comparable units are selling for.

### Expected consequence, stated in advance

This will probably not rescue the losing strategies, and may make some look
worse. dip-buyer selects on `market`, so it will still see a $71.88 mark as
a bargain and will now pay $1,800 for it. Worse: the portfolio marks
positions at `market`, so buying at the ask and marking at the last sale
books an immediate unrealised loss.

That is the honest state of the world, not a regression. What changes is
that the number becomes a real price paid for a real listing instead of an
artifact of walking outward from a price nobody was offering. **A plan
built on this design must not treat a worse result as failure.**

Whether a strategy should trade on `market` at all when it is inconsistent
with the listings is a strategy-design question, and is out of scope here.

## Component 3: horizon-matched evaluation

New module `src/pkmn_quant/research/horizon.py`.

**Inputs:** a run's `fills.parquet`, and the benchmark run's
`equity.parquet` — which *is* the passive sealed index, so no new data
structure is needed.

**Round-trip pairing:** FIFO per asset. Each completed round trip yields
entry date, exit date, holding days, quantity, strategy return net of the
fees and impact recorded on both fills, the index return between those same
two dates, and the difference (the edge).

**Edge cases that must be handled explicitly, not incidentally:**

- a position still open at window end — closed at the final mark and
  **flagged**, so open and completed trades are never silently mixed
- an asset bought twice and sold once — FIFO splits the sell across lots
- a sell with no matching buy — cannot occur from the engine, but the
  pairing must raise rather than silently drop it
- entry and exit on the same day — holding period 0, index return 0
- an index curve missing a date (non-trading day) — use the nearest prior
  date, and record that a substitution happened

**Outputs.** A distribution, never a single number:

- per-trade edge table (the artifact)
- median and mean edge, and win rate against the index
- holding-period histogram
- edge broken out by holding-period bucket, including the 1-3 month band

**Surfaced as `pkmn horizon <run-id>`**, following the existing
`pkmn runs show` / `pkmn evaluate` pattern, writing a report and JSON
artifact alongside the other run artifacts.

**Holding period is measured, never imposed.** No strategy gains a max-hold
constraint in this work. Whether any strategy naturally trades in the 1-3
month band is what the histogram reports; constraining strategies to that
band is a strategy-design change that should follow the measurement rather
than precede it. If the winners all turn out to be 6-month holds, that is
worth knowing before anything is constrained.

## Testing and parity

**Test-first**, per project convention. The screen, the deficit sweep, and
the FIFO pairing are pure functions over small frames and get unit tests
with hand-built fixtures before implementation. The sweep's tie-breaking
needs a test that would fail under non-deterministic ordering.

**C++ parity is the main risk in this work.** Both repairs land in code
with native ports: `buy_and_hold` is one of the five native strategies, and
the cost model lives in the engine core. Required:

- implement both sides, Python and C++
- `scripts/parity_full.py` on the real warehouse must pass bit-for-bit
- `uv sync --reinstall-package pkmn-quant` after every `cpp/` edit — a
  stale `.so` silently runs the old code
- never enable fast-math or fp-contract; parity depends on IEEE-754-exact,
  non-reassociated floating point

**The goldens will move.** `tests/test_cli_backtest.py` pins exact engine
numbers and the buy-price floor changes them. Convention is to update them
in the same commit with a hand-derivation in the docstring, so the new
values are checkable rather than blessed.

**`research/horizon.py` is Python-only.** It reads artifacts after a run;
it is not on the engine's hot path and needs no port.

## Re-run and documentation

Order matters: **the benchmark runs first**, because its equity curve is
the index every horizon comparison reads.

1. `pkmn backtest` — the screened benchmark, 2024-03-01..2026-06-30
2. the four native strategies (roughly 4.5 minutes each)
3. ml-ranker, then ml-ranker-v2, **serially**, `--workers 1`
4. `pkmn evaluate`, and `pkmn horizon` for each strategy

Two operational constraints, both learned the hard way and already recorded
in CLAUDE.md's gotchas: the ML walk-forwards must run with `--workers 1`
(fold-parallel thrashed the machine to a 7.4-hour non-completion at load 89
with swap at 24.6 GB of 25.6 GB; serial finished in 75 minutes), and long
runs need `caffeinate -dimsu` — `caffeinate -i` blocks only idle sleep and
a run died silently at 64 minutes with a zero-byte log.

Documentation updates: a findings section in
`docs/research-findings-2026-07.md`, plus `CLAUDE.md` and `README.md`. The
benchmark reference figure in CLAUDE.md's conventions must be updated — it
is the comparison point for every future strategy. Every published number
from the previous plan was measured on the 4.7%-deployed benchmark and
becomes superseded.

## Risks

1. **No N fully deploys at a realistic account size.** Measured false at
   $10,000: the sweep reaches 99.8-100% at every N tested. If a future
   account size or universe breaks that, the plan must report the measured
   deployment rather than quietly picking a bad N.
2. **The strategies may look worse.** Expected, per Component 2. Not
   failure.
3. **C++ parity may not hold first try.** The differential test is the
   gate; a parity failure blocks the re-run, since running the zoo on two
   engines that disagree produces numbers nobody can defend.
4. **No strategy may trade in the 1-3 month band at all.** The plan still
   succeeds: it will have produced the measurement that says what to build
   next.

## Out of scope

- Making any strategy profitable.
- The liquidity-floor experiment (open question 3) — the screen built here
  is the machinery it needs, but running it is a second full zoo re-run and
  a separate research question. Bundling it would make any change
  impossible to attribute to one cause.
- Diagnosing the two ML losses (open question 1).
- Max-hold constraints on strategies.
- Sell-side price floors (no bid series exists).
- Keying `evaluate` artifacts by run id rather than date — a real wart,
  already documented, unrelated to this work.
