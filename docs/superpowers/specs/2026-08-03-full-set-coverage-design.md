# Full Set Coverage — Design

**Date:** 2026-08-03
**Status:** approved, ready for an implementation plan

## Goal

Ingest prices for **all 217 tcgcsv Pokemon groups** instead of the 23 currently
tracked, over the same date window. The tradeable universe widens with the data
(the coupling is accepted, not worked around), so this work also re-runs the
research suite and updates the research record.

## What this does and does not give you

**It gives ~7x more products over the same dates.** Measured on one archive:
6,342 price rows for the 23 tracked sets, 45,092 for all 217. Extrapolated
across ~900 days that is roughly 3.16M → ~22M price rows, warehouse ~38 MB →
~270 MB.

**It does not give deeper history.** `EARLIEST_ARCHIVE_DATE = 2024-02-08`
(`config.py:12`) is when tcgcsv's daily archives begin, and each archive holds
every group's prices *for that day only*. So Base Set, Evolving Skies and the
rest arrive priced from 2024-02-08 onward — not back to their release. Pre-2024
price history is not available from this source.

**The downloads are already paid for.** 904 archives are cached locally
(2.8 GB) and `download_archive` skips anything on disk. This is a re-extract,
not a re-download: measured 1.84s/day for all 217 groups versus 1.45s for 23
(7z decompression dominates, so 7x the data costs almost nothing extra). A full
rebuild is roughly 30-50 minutes.

## Constraints discovered during design

These shaped the architecture and are why the obvious approach does not work.

**The quality-gate chain is sequential.** `apply_quality_gates(prices,
previous)` compares each day against the *previous day's clean output* to catch
price jumps. A wider set list changes that baseline too, so days must be rebuilt
**in order from the beginning** — re-extracting a subset of days is not
meaningful.

**`ingest_range` skips stored days.** `if warehouse.has_day(day)`
(`ingest.py:108`) means simply lowering the set filter and re-running
`pkmn ingest` would do **nothing at all**: every day is present, so every day is
skipped and the wider set list never gets applied. A deliberate rebuild path is
required.

**The universe is derived from prices.** `PreparedMarket.prepare`
(`engine/prepared.py:98-102`) builds `assets_df` from every priced
`(product_id, sub_type)` in the window. There is no set-release filter
downstream, so ingesting all sets *is* widening the tradeable universe. Accepted
deliberately (see Decisions).

**Existing products are provably unaffected by the wider extract.** The jump
gate joins on `(product_id, sub_type)`, and the null/nonpositive/duplicate gates
are per-row, so extra products cannot change an existing product's gate outcome.
Verified empirically on 4 consecutive days: existing products' clean rows were
identical under both set lists. This is what makes the acceptance criterion
below achievable rather than aspirational.

**Thin products are already structurally constrained.** `engine/data.py:35-37`
separates marks (carry-forward, for valuation) from prices (actual prints,
required to fill), so a card that prices monthly can be valued on a stale mark
but **cannot be traded** on a day it did not print. `DEFAULT_LIQUIDITY_TIERS`
additionally caps daily quantity by price tier. No new mechanism is needed for
illiquid old cards; the residual effect is a documentation matter (see Caveats).

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Purpose | Ingest everything **and** trade everything | The user wants to trade every set, so a permanent store-vs-trade scope split would be machinery serving a distinction they do not want. |
| Rebuild strategy | **Shadow warehouse, then swap** | The live warehouse is never written during the rebuild; rollback is a directory move. Costs ~270 MB and one 2-line production change against a real safety net. |
| Universe coupling | **Accepted** | Following from the purpose. Consequence: the re-run and docs must live in *this* work, not a follow-up — at the swap, every documented number describes a universe that no longer exists. |
| Acceptance criterion | **Old days must reproduce exactly** | The one check that catches a subtly wrong rebuild. Verifies the data independently of any research interpretation, which is what keeps the coupling safe. |
| Daily launchd job | **Leave running** | The shadow build does not touch the live warehouse, so the 09:00 job keeps working. After the swap it simply ingests one more day, now across 217 sets. |

## Architecture

Two small production changes; everything else is a script plus a procedure,
because it runs once.

### Production changes

**1. `Paths` gains `warehouse_name: str = "warehouse"`** (`config.py`). A frozen
dataclass field with a default, so every existing caller is unaffected. It lets
a shadow build write to `data/warehouse-full/` while sharing the existing
archive cache — `raw_archives` stays derived from `root`, so no archive is
re-downloaded.

**2. `tracked_groups` drops its `MIN_SET_RELEASE` lower bound**
(`ingest.py:58-59`). Names and prices then cover the same 217 groups, so the
catalog-vs-prices scope split documented in the previous plan collapses into one
scope. CLAUDE.md must be updated to say so; leaving the old split documented
would be actively misleading.

### One-shot scripts

**3. `scripts/rebuild_warehouse.py`** — walks 2024-02-08 → the latest stored
day, extracting each cached archive for all 217 groups, running the same
`apply_quality_gates` chain in order, writing into the shadow warehouse. No
downloads. Thin orchestration over the existing `ingest_day`.

**4. `scripts/verify_rebuild.py`** — the acceptance gate. For every
`(day, product_id, sub_type)` present in the *current* warehouse, the shadow
must hold the same row with identical `low/mid/high/market`. It also checks that
the shadow covers exactly the same day set (no gaps introduced) and that
quarantine rows for existing products match — a flipped quarantine decision
would signal the gate chain diverged. New products are ignored by construction:
they are the point, not a risk to old data.

The comparison is on **data, not file bytes**. Parquet row order and compression
can differ harmlessly between runs, so byte-comparing files would produce false
failures.

### Coverage statistics — reported, not gated

Rows, distinct products, quarantine rate by reason, and a **sparsity
distribution** for the new products (what fraction of days each actually
prices). The sparsity figure is what tells a reader how much of the new universe
is real versus nominally present. It informs the caveats; it does not block the
migration.

### Two hard stops

- **Verification failure** — any mismatch, any missing key. No swap.
- **Row growth far below the expected ~7x** — a silently-narrow extract would
  otherwise pass verification trivially, since verification only checks that old
  data survived.

### The daily-job race

The 09:00 launchd job keeps running (by design), so it can add a day to the
*live* warehouse after the rebuild has passed that date. The swap would then
silently lose that day.

This is handled rather than avoided: verification requires the shadow to cover
**every day the live warehouse has**. If the live gained a day mid-rebuild,
verification fails on the missing-day check, and the fix is to re-run the
rebuild — it is incremental, so `has_day` is false only for the new day and it
costs seconds. Then verify again. Swap only on a clean verification.

Re-verify immediately before the swap, not just after the rebuild, so the window
between the two is as small as possible.

### The swap

```bash
mv data/warehouse data/warehouse-old
mv data/warehouse-full data/warehouse
```

Then a smoke check (`pkmn runs list`, one short backtest) before anything else.
Rollback is the same two moves reversed. `data/warehouse-old` is retained until
deleted deliberately; the plan will not delete it.

Disk: ~270 MB shadow plus ~38 MB retained old, against 2.8 GB of archives
already present.

## Research re-run

Everything is genuinely superseded — unlike the previous catalog fix, where the
correction proved immaterial on this window, every documented number was
computed on a 23-set universe. Buy-and-hold sealed, the benchmark every strategy
is judged against, will move substantially because the sealed universe grows
too. CLAUDE.md's Conventions bullet pins +186.0% / +183.7% as the comparison
figure; that becomes wrong at the swap, and a stale reference number there
misleads every future comparison.

Scope: the benchmark, all six strategies' walk-forwards, and `pkmn evaluate`.
Then a new findings section, with prior sections marked superseded.

**Runtime warning, from the previous plan's experience.** Native strategies ran
~20s each and should stay in single-digit minutes. But ml-ranker took ~22
minutes on 23 sets and its feature frame scales with product count, so v1 and v2
could plausibly take **1-3 hours each** and will dominate the schedule. Two
lessons apply directly and must be stated in the plan:

- Run the ML walk-forwards **sequentially, never in parallel**. Running them in
  parallel previously tripled wall-clock (22 min → 55+ min) under memory
  pressure and put both runs at risk.
- Use `caffeinate -i`. A machine sleep previously killed a run silently, leaving
  zero bytes of output and no traceback.

## Testing

- **`Paths.warehouse_name`** — resolves correctly for a non-default name;
  defaults unchanged so existing callers are unaffected.
- **`tracked_groups`** — returns all groups with no lower bound, and still
  excludes groups published after `today`.
- **`verify_rebuild.py`** — tested against small synthetic warehouses. It is the
  acceptance gate, so it must be proven to **fail when data differs**, not
  merely to pass when it matches. Cover: a changed price, a missing day, a
  missing product, and a flipped quarantine decision.
- **`rebuild_warehouse.py`** — thin orchestration over existing tested code;
  the acceptance run is its real test.

`tests/test_cli_backtest.py` goldens and `tests/test_native_parity.py` both run
on synthetic fixtures and must not move. If either does, that is a
stop-and-report signal, not a golden to update.

## Caveats to document

- **Mark smoothing worsens.** CLAUDE.md already warns that Sharpe/Sortino here
  are inflated by carry-forward marks on thin markets; ~7x more thin products
  makes this worse. Restate with the new sparsity numbers.
- **"All sets" means more products, not deeper history.** The window remains
  2024-02-08 onward.
- **The new sparsity distribution**, so a future reader knows how much of the
  universe is thinly priced.
- **Quarantine rises from 0.32% to ~1.69%**, almost entirely `null_market`
  (measured: 761 of 763 on a sample day) — old thin cards with no sale that day.
  Expected, not a defect.

## Out of scope

- Deeper price history than 2024-02-08 (unavailable from this source).
- Any liquidity or sparsity floor on the tradeable universe — the engine's
  print-required-to-fill rule and liquidity tiers already constrain thin cards.
- Changes to strategies, the cost model, or the engine.
- Re-downloading archives.
