# Pokemon book benchmarks

Plan B, Task 7: real-data equivalence of book execution with the walk-the-spread formula, and what book
execution costs. Book execution fills strategy orders against **modelled resting orders (synthetic, built from
the day's tcgcsv quote)**: tcgcsv publishes one daily quote per product, not an order book, so every resting
order below is a model of the quote, never market data. Nothing on this page is a research result.

All numbers on this page come from runs made on 2026-10-23 at git `5f5ba34581cc` **with a dirty tree**: the
uncommitted diff was this task's own work (the `ladder_equivalence` binding in `cpp/bindings/module.cpp`, its
`_engine.pyi` stub and pytest, and the three new scripts). No file on the formula or book execution path was
modified. The commit that adds this page is the one that contains that diff.

Warehouse at the time: 949 days, 2024-02-08..2026-09-13, 38,202,342 price rows.

## Hosts

- **macOS** (equivalence, parity, encode, backtest and walk-forward cost): Apple M1 Pro, macOS 15.8.1 arm64,
  Python 3.14.4, the `pkmn_quant._engine` wheel built by `uv sync --reinstall-package pkmn-quant`
  (scikit-build-core Release). The hostname read `n3-4-242.dhcp.drexel.edu` during the equivalence run and
  `Minhs-MacBook-Pro.local` during the benchmark run; it is the same machine (the DHCP name changed).
  16 GB RAM. **Swap was heavily used**: `vm.swapusage` read 26.7 GB used of 27.6 GB right after the benchmark
  run (most likely left over from `parity_full.py`'s Python-engine runs, not measured). Treat every macOS
  wall-clock below as noisy; the run-to-run spreads are quoted for that reason.
- **VM** (replay throughput): Apple M1 Pro, 7-vCPU OrbStack Ubuntu 24.04 arm64 VM, `c++ (Ubuntu
  13.3.0-6ubuntu2~24.04) 13.3.0`, flags `-O3 -DNDEBUG -std=gnu++20 -ffp-contract=off` (read from the target's
  generated `flags.make`), timer cntvct_el0 at 24 MHz (41.7 ns per tick). As with the IEX numbers, VM noise is
  10% or more.

## 1. Pricing equivalence (gate (a)) — PASS

`uv run python scripts/book_equivalence.py --start 2024-03-01 --end 2026-06-30`

`_engine.ladder_equivalence` takes every row of the window that has a printed market (`market > 0`, the same
skip rule as book execution). For each side it builds the test-only exact ladder (1,000,000,000 ticks per
dollar) into an `OrderBook`, takes the units out one at a time with `match(..., 1, ...)` (so the book's own
price-time priority picks the order), forms prefix sums, and compares every `(used, qty)` pair with
`used + qty <= depth` against `CostModel::buy_impact` / `sell_impact`. The book value is compared unclamped
(the fill path clamps at 0), which is the stricter check. Tolerance `max_abs_err <= 1e-6`, not loosened.

| | value |
|---|---:|
| rows in window (warmup 0) | 34,092,324 |
| rows checked (printed market) | 34,092,324 |
| `(used, qty)` checks, both sides | 11,165,181,052 |
| max abs error | 4.66e-10 dollars |
| worst | buy side, used 0, qty 20 |
| worst quote | product 89297 Reverse Holofoil, market $3.73, low $2.99, mid $182,906.33 |
| wall-clock of the check | 87.9 s (plus 34.6 s to prepare the window) |

The worst row is a quote with an absurd `mid` (a $3.73 card whose mid is $182,906), so its ramp is enormous and
the residual is the 1e-9-tick rounding of 20 units at that size. It is still 2,000x inside the tolerance.

## 2. End to end, formula vs exact ladder (gate (b)) — PASS

Same command. Each of the five native strategies runs through `NativeBacktest` directly, default params,
$10,000, impact on, warmup 0 (the `pkmn backtest` default), one shared `PreparedMarket`, once with
`execution="formula"` and once with the test-only `"book-exact-test"`. On asset-days where only one side filled
the fills must match on day, asset and quantity with impact within 1e-6.

| strategy | fills (formula / exact) | two-sided asset-days | fills on two-sided asset-days | one-sided fills compared | max impact error | mismatches |
|---|---:|---:|---:|---:|---:|---:|
| buy-and-hold | 60 / 60 | 0 | 0 | 60 | 3.3e-10 | 0 |
| sealed-accumulation | 50 / 50 | 0 | 0 | 50 | 5.7e-14 | 0 |
| dip-buyer | 322 / 322 | 0 | 0 | 322 | 3.3e-10 | 0 |
| xs-momentum | 323 / 323 | 0 | 0 | 323 | 3.3e-10 | 0 |
| cost-aware-reversion | 226 / 226 | 0 | 0 | 226 | 3.3e-10 | 0 |

**Two-sided count.** The spec asks how many real asset-days the change touches. Measured on fills, under
default params over this window: **zero**. None of the five strategies bought and sold one asset on one day, so
the one place book and formula are designed to differ (the formula shares one depth counter across both sides
of an asset-day; the book gives each side its own full depth) never fired. That is a statement about these five
strategies at default params, not about the change in general: a tuned or new strategy can still hit it.

ml-ranker and ml-ranker-v2 were **skipped**: they run through the per-bar Python callback bridge and take 75-140
minutes each.

## 3. Cent vs formula (part (c))

**Capability check, default params, NOT a research result — Plan D owns the re-run.**

Same runs as above plus one `execution="book"` run each (whole-cent ladder, the user-facing default).

| strategy | formula return | book return | formula fills | book fills | formula impact $ | book impact $ |
|---|---:|---:|---:|---:|---:|---:|
| buy-and-hold | +126.8426% | +126.8423% | 60 | 60 | 1,321.21 | 1,321.24 |
| sealed-accumulation | +6.2190% | +6.2185% | 50 | 50 | 882.99 | 883.03 |
| dip-buyer | −99.7095% | −99.7251% | 322 | 322 | 10,759.42 | 10,760.80 |
| xs-momentum | −98.6921% | −98.6969% | 323 | 323 | 8,237.16 | 8,237.16 |
| cost-aware-reversion | −98.4112% | −98.4129% | 226 | 226 | 14,228.35 | 14,228.53 |

Cent rounding moves impact by cents to about $1.38 per run and leaves every fill count unchanged. The
buy-and-hold figure here is warmup 0 on the 949-day warehouse at this commit; it is not the published
benchmark bar and must not be quoted as one.

## 4. Formula path unchanged — `scripts/parity_full.py` PASS

`uv run python scripts/parity_full.py` (Python engine vs C++ engine, formula, impact on, warmup 120,
2024-03-01..2026-06-30), bit-for-bit on equity and every fill field:

| strategy | fills | result | python (s) | cpp (s) |
|---|---:|---|---:|---:|
| buy-and-hold | 61 | PASS | 168.37 | 104.26 |
| sealed-accumulation | 54 | PASS | 124.57 | 38.73 |
| dip-buyer | 286 | PASS | 1,048.63 | 41.24 |
| xs-momentum | 330 | PASS | 192.39 | 31.54 |
| cost-aware-reversion | 230 | PASS | 91.93 | 38.65 |

Exit 0. The timings overlapped a pytest run and are not a benchmark. ml-ranker (`--ml`) was not run.

## 5. Encode

`uv run python scripts/bench_pokemon_book.py` runs
`pkmn wire encode --start 2024-02-08 --end 2026-09-13 --out data/wire/full.pkw` as a subprocess.

| | value |
|---|---:|
| messages | 38,250,316 |
| bytes | 890,723,220 |
| encode time reported by the command | 10.763 s |
| subprocess wall-clock (incl. `uv run` start-up and warehouse load) | 11.556 s |

n=1.

## 6. Backtest cost, book vs formula

Same script. buy-and-hold and dip-buyer, 2024-03-01..2026-06-30, $10,000, impact on, warmup 0, default params,
three interleaved runs per mode (formula, book, formula, book, formula, book). "total" is `NativeBacktest.run()`
loading and preparing the market itself (what one CLI backtest pays); "engine" hands every run one shared
`PreparedMarket`, so it is the C++ run plus result repackaging.

| strategy | measure | formula runs (s) | book runs (s) | formula best | book best | book / formula (best) |
|---|---|---|---|---:|---:|---:|
| buy-and-hold | total | 44.07, 50.31, 53.80 | 35.91, 60.95, 58.79 | 44.07 | 35.91 | 0.81 |
| buy-and-hold | engine | 5.82, 1.84, 2.21 | 1.76, 2.07, 2.17 | 1.84 | 1.76 | 0.96 |
| dip-buyer | total | 77.60, 54.46, 60.18 | 67.57, 54.71, 67.59 | 54.46 | 54.71 | 1.00 |
| dip-buyer | engine | 17.39, 10.05, 11.77 | 14.39, 9.75, 10.42 | 10.05 | 9.75 | 0.97 |

Book execution costs nothing measurable here: every best-of-3 ratio is inside the run-to-run spread (the
"total" rows vary by up to 70% between runs of the same mode, which is the swap pressure described above, not
the engine). A ladder is built only for an asset that has an order that day, so the extra work scales with fills,
not with the universe. Do not read the 0.81 as book being faster.

## 7. Walk-forward cost, book vs formula — n=1 each

Same script: `caffeinate -dimsu uv run pkmn walkforward --strategy sealed-accumulation --start 2024-03-01
--end 2026-06-30 --trials 15 --workers 1 --execution book|formula`, book first, then formula, **once each
(n=1, no spread)**.

| mode | wall-clock (s) | stitched total return |
|---|---:|---:|
| book | 266.24 | −10.31% |
| formula | 266.33 | −10.31% |

book / formula = 0.9997. The two walk-forwards picked results that agree to four decimals on every stitched
metric (Sharpe −0.9804 vs −0.9803). This is a timing run; its returns are not a research result.

Both ran with `--root /var/folders/d5/vdyll6vs51j0943drp37077r0000gn/T/pkmn-bench-wf-cmiayxt2`, a scratch
directory whose `data/warehouse` is a symlink to the real warehouse, so their artifacts
(`wf-sealed-accumulation-2024-03-01-2026-06-30` and `...-book`) and their two registry records live there
and **not** in the real `data/results` or `data/runs`; a later `pkmn evaluate` cannot see them. The scratch
registry records carry no git SHA (`git_info` runs in that root, which is not a repository); the SHA above is
the one the bench script recorded. The directory is under the system temp folder and may be cleaned by macOS.

## 8. Replay throughput (VM)

`scripts/bench_pokemon_replay.sh` (re-execs in the VM, Release build of `pokemon_replay` in
`cpp/build-linux-release`), on `data/wire/full.pkw`: three interleaved pairs, ladders on then off, one pass
each, each pass timed by the tool's counter around the whole pass, file reads included.

File: 890,723,220 bytes, 38,250,316 messages, 46,076 instruments, 949 days, 38,202,342 quotes, 38,202,342
ladders built, 1,261,666,066 resting orders booked per ladder pass; 0 gaps.

| mode | msgs/s best | msgs/s median | spread | seconds (runs 1-3) |
|---|---:|---:|---:|---|
| ladders (decode + build each day's ladders) | 1,488,661 | 1,472,031 | 8.9% | 25.694, 28.169, 25.985 |
| no ladders (decode only) | 97,221,748 | 61,585,370 | 70.6% | 0.621, 0.712, 0.393 |

Building a ladder for **every** quote (about 33 resting orders each on average) dominates replay by roughly
40x. That is the replay tool's worst case, not the backtester's, which builds ladders only for assets with an
order. The no-ladders spread (70.6%) is far outside the 10% VM noise floor and the 0.39 s pass reads 890 MB at
about 2.3 GB/s, so the decode-only figure is very likely dominated by page-cache state (not verified); quote
the median, and treat the best as an upper bound.

## Raw JSON

The scripts print their full JSON; the copies used for this page were kept outside the repo. Key fields:

```json
{
  "date": "2026-10-23",
  "git_sha": "5f5ba34581cc84a6f696ee48b01faad54d21a842",
  "git_dirty": true,
  "host": {"machine": "Apple M1 Pro", "os": "macOS-15.8.1-arm64-arm-64bit-Mach-O", "python": "3.14.4"},
  "a": {"rows": 34092324, "checks": 11165181052, "max_abs_err": 4.656612873077393e-10,
        "worst_row": 12025137, "worst_side": "buy", "worst_used": 0, "worst_qty": 20},
  "encode": {"start": "2024-02-08", "end": "2026-09-13", "messages": 38250316, "bytes": 890723220,
             "encode_reported_seconds": 10.763, "wall_seconds": 11.556},
  "walkforward": {"n": 1, "book": {"wall_seconds": 266.238}, "formula": {"wall_seconds": 266.328}}
}
```
