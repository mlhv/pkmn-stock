# Market-Data Pipeline and Order Book (CLOB): Design

**Status:** design written, awaiting owner approval
**Date:** 2026-10-22
**Predecessor:** `docs/superpowers/specs/2026-08-05-instrument-repair-and-horizon-design.md`

## Intent

pkmn-quant already has the research half of a quant system: a 36M-row price
warehouse, a C++20 event-driven backtester with bit-for-bit Python parity,
walk-forward optimization, and a statistics layer (bootstrap CIs, deflated
Sharpe, White's Reality Check). It does not have the systems half that quant
firms build around an exchange: a binary wire protocol, a feed handler, and a
central limit order book (CLOB).

The owner's goal, in their words: take a project everyone has done before
(a CLOB and its market-data plumbing) and make it work for a trending market
almost nobody has done algo trading on (Pokemon card and sealed product
prices), and find out whether applying industry-standard practice and
systems knowledge gives an edge there.

This is a portfolio project. The story an interviewer should be able to walk
through end to end is one stack: wire protocol, feed handler, order book,
strategy, backtest, statistics.

### What the book can and cannot change (state this plainly everywhere)

tcgcsv publishes one row per product per day: `market` (sales-derived),
`low`/`mid`/`high` (current listings, ask side only), `directLow`. There are
no individual orders, no trades, and no bids. Recent-sales data was
investigated on 2026-10-22: TCGplayer offers no official sales endpoint and
is closed to new API keys; eBay's Marketplace Insights API is restricted to
approved developers and covers 90 days; PriceCharting is paid with unclear
sales coverage. None is used here.

So the Pokemon book is built from the same four numbers the current formula
cost model uses. **It adds mechanism, not information**: discrete price
levels, partial fills, and fills that can be audited level by level. The
expected effect on research conclusions is small, and the standing result
("nothing beats buy-and-hold sealed") is not expected to flip. That outcome
is a legitimate result and success does not depend on an edge appearing.

The real-scale systems claims (throughput, latency, order-by-order book
correctness) come from the IEX side, which is real exchange data.

## Goals

1. **One C++20 order book core** with price-time priority, shared by two
   input feeds: real IEX exchange data and the Pokemon warehouse.
2. **IEX path:** read IEX HIST packet captures (pcap) without copying, parse
   the IEX-TP transport layer, decode the order-by-order DEEP+ feed, and
   maintain a full book. Prove the book correct against an independent
   oracle, and measure throughput and latency honestly.
3. **Pokemon path:** an ITCH-style binary protocol of the owner's own design,
   an encoder that turns the warehouse into that protocol, and a decoder that
   turns it into the same internal book events the IEX decoder emits.
4. **Book execution becomes the default** in the C++ backtester, replacing
   the formula impact model. The formula stays available behind a flag.
5. **UDP multicast transport:** a replay publisher and a feed handler with
   sequence-gap detection and recovery, tested by deliberately dropping
   packets.
6. **Re-run the research** (benchmark, six strategies, horizon, `pkmn
   evaluate`) through the book and report book vs formula side by side.

## Non-goals

- Live trading, order entry to any real venue, or any real-time Pokemon
  feed. Pokemon data updates daily; speed matters for replay throughput
  (more experiments per hour), not for trading.
- Kernel bypass, NIC tuning, or hardware timestamping. There is no physical
  network here.
- A book in the Python engine. See "Engines" below.
- Book execution in the live paths (`pkmn signals`, `pkmn daily --paper`,
  `live/paper.py`). They keep the formula `CostModel` for now. This is a
  known mismatch and must be documented where those paths price orders.
- Scraping TCGplayer, eBay, or PriceCharting for sales data.
- Rust, or any second language for the core.

## Anti-goals (would make this a failure even if it technically works)

- **Mirroring someone else's project.** The owner saw a similar project
  online (NanoBook: order book from IEX UDP packets, feed handler, Rust and
  C++ book engines). Do not copy its structure, components, or numbers.
  Every figure on the resume must be a measurement taken in this repo.
- **Presenting synthetic Pokemon orders as real data.** The Pokemon book's
  resting orders are a model. Every place they appear (code comments, CLI
  help, reports, findings doc, resume wording) says so.
- **Latency numbers without a method.** No average-only figures, no number
  without its hardware and measurement method stated.
- **Silently changing research numbers.** Every result produced through the
  book records that it was, and no comparison mixes book and formula runs.

## Constraints

- **Hardware:** Apple M1 Pro, 8 cores, 16 GB (macOS, arm64), plus an
  OrbStack Linux VM `ubuntu` (Ubuntu 24.04, arm64, 7 vCPUs, g++ 13.3,
  clang++). Measured 2026-10-22: `cmake` and `perf` are not installed in the
  VM, and **the VM exposes no hardware performance counters**
  (`/sys/bus/event_source/devices` lists only breakpoint, kprobe, software,
  tracepoint, uprobe). Cache-miss and branch-miss counts are therefore
  unavailable; time-based sampling profiles are available if `perf` can be
  installed for the OrbStack kernel (the builder verifies this; if not, use
  another sampling profiler).
- **Where numbers come from:** headline latency and throughput are measured
  in the Linux VM and always reported with the hardware stated (for
  example "M1 Pro, 7-vCPU OrbStack Linux VM"). Everything must still build
  and pass tests on macOS, as today.
- **Existing project rules hold:** all four gates before every commit;
  no `-ffast-math` / `-ffp-contract=fast` anywhere; `uv sync
  --reinstall-package pkmn-quant` after any `cpp/` change; feature branch
  per plan; stop and explain after each task and wait for the owner's
  green light.
- **Bit-for-bit parity stays intact on the formula path.** The Python
  engine and the C++ engine with `--execution formula` must still match
  exactly (`tests/test_native_parity.py`, `scripts/parity_full.py`).
- **Data stays out of git.** IEX pcaps are large (expect gigabytes per day)
  and go under the gitignored `data/` tree (for example `data/iex/`).
  Tests use small hand-built pcap fixtures committed under the test tree.

## Decisions

Owner-decided items are marked **(owner)**. Everything else is the designer's
call, made in the owner's absence, and open to the owner's override.

### Scope and framing

- **(owner)** IEX real data plus Pokemon in one project (option 2), so the
  systems numbers come from exchange-scale data while Pokemon stays the
  research subject.
- **(owner)** The CLOB is in this design, not a later one.
- **(owner)** The Pokemon book uses tcgcsv quotes only; sales data is an
  optional future input, and the design must leave room for it (see
  "Future trade feed").
- **(owner)** Book execution **replaces** the formula as the default.

### Architecture

Three new C++ components, kept separate from the engine core so each is
testable alone and the core keeps no I/O:

1. **`book`**: the order book. Pure, no I/O. Integer prices in ticks
   (int64): IEX prices are fixed-point already; Pokemon prices are cents.
   Integer prices keep the book exact and free of float-ordering concerns.
   Operations: add, cancel, reduce (partial cancel), execute against resting
   order, and match an incoming aggressive order across levels with
   price-time priority. Order lookup by id must be O(1) average. Data
   structure choice is the builder's, but it must be justified by a
   measurement, not asserted.
2. **`md`** (market data): pcapng reader (streaming through zlib, packets
   parsed in place; see "IEX side" for why it is not memory-mapped),
   IEX-TP segment parser, DEEP+ decoder, Pokemon protocol encoder/decoder,
   and the UDP publisher/feed handler. Decoders emit one shared internal
   event type that the book consumes. Decoding must not allocate per
   message.
3. **Engine integration:** the existing `pkmn_engine_core` execution step
   gains a book-backed path behind a single execution-mode switch.

### The Pokemon book (how the resting orders are built)

- **Rebuilt each trading day from that day's quote**, then discarded.
  No book state carries across days. This keeps it stateless, matches the
  existing per-day liquidity cap, and keeps strategies reset-safe.
- **Calibration anchor: the formula is the continuous limit of the book.**
  The current impact charge, `spread * qty * (2*used + qty) / (2*cap)`, is
  exactly the integral of a linear price ramp from `base` to `base +
  spread` over `cap` units. A ladder of one-unit levels where unit `k`
  (0-based) is priced at `base + spread * (k + 0.5) / cap` reproduces the
  formula's charge exactly. So:
  - Total depth per side per day equals the existing tier cap
    (`max_daily_qty`, tiered on the same price the formula tiers on).
  - Ask side: starts at `buy_base = max(market, low)` (the instrument-repair
    floor, kept deliberately conservative) and ramps toward `mid`, exactly
    as `buy_impact` does today.
  - Bid side: starts at `market` and ramps down toward `low`, exactly as
    `sell_impact` does today. **The bid side is a modelling assumption**:
    tcgcsv publishes no bids. Note that the formula already makes this same
    assumption implicitly; the book makes it explicit and labelled.
  - Edge cases mirror the formula: a null `low` or `mid`, or a non-positive
    spread, means a flat book at the base price with the same depth.
  - Default ladder prices are rounded to whole cents (the venue's real
    tick). The difference between the cent-tick book and the formula is
    therefore the discreteness of real prices, and it gets measured.
- **Equivalence test (required):** a test-only "unit-midpoint, no rounding"
  ladder must reproduce the formula's impact charge on real warehouse days
  within a stated floating-point tolerance. This is the book's correctness
  oracle on the Pokemon side.
- **Ledger semantics unchanged:** `Fill.price` stays the day's `market`
  print; everything the book charges above it on a buy (or below it on a
  sell) goes in the `impact` column. Downstream readers of fills (reports,
  dashboard, web) keep working unchanged; `pkmn horizon`'s modelled exit is
  the one exception (see below).
- **Strategy orders are aggressive** (they take liquidity and never rest).
  Multiple orders on the same asset in one day consume the same levels in
  arrival order, which replaces the formula's `used` counter.
- **Bid and ask depth are separate (designer's call).** Today both engines
  keep one per-asset daily counter shared by buys and sells
  (`engine/execution.py` `filled_today`, `cpp/src/pkmn_engine/
  execution.cpp`). A book has two sides, so a buy and a sell on the same
  asset on the same day each get their own side's full depth. This is a
  **deliberate change from the formula**, recorded in the findings
  write-up as a second source of book-vs-formula difference alongside cent
  rounding. The equivalence test is scoped to asset-days with orders on
  one side only, and the plan counts how many real asset-days the change
  touches.
- **Horizon exits use the book (designer's call).** `research/horizon.py`
  scores a still-open trip net of a modelled sale using the Python
  `CostModel.sell_impact`. For book runs that would give book entries and
  formula exits, which breaks the no-mixing rule, and the benchmark (which
  never sells) would be scored entirely on formula exits. So the C++ side
  exposes one binding that prices a hypothetical sale against a given
  day's bid ladder, and `horizon.py` uses it when the run's recorded
  execution mode is `book`. Formula runs keep the Python path unchanged.

### Engines

- **The book lives only in the C++ engine.** The Python engine keeps the
  formula as the frozen reference. This follows the existing direction
  toward a C++-only engine (Plan 2a), and writing a matching engine twice
  costs a lot for little.
- **Execution switch:** `--execution book|formula`, default `book`, on
  `pkmn backtest` and `pkmn walkforward` (and the web trigger's request
  model). `--no-impact` keeps meaning "flat cost, no frictions" and
  overrides both. `pkmn walkforward --engine python` with
  `--execution book` is an error with a clear message, not a silent
  fallback.
- **Bridged strategies** (ml-ranker, ml-ranker-v2) run on the C++ event
  loop, so they get book execution like everyone else.
- **Reproducibility:** the execution mode goes into the run config (and so
  into the config hash in `data/runs/registry.jsonl`). `pkmn evaluate`,
  `pkmn horizon`, and the benchmark pairing refuse to compare runs with
  different execution modes.
- **Goldens:** the formula-path goldens stay as they are. New book-path
  goldens are added with hand derivations in the docstring, as the project
  rule requires.

### Pokemon wire protocol

- Owner's own ITCH-style format: fixed-width, little-endian, packed
  messages, each with a type byte and a per-stream sequence number, carried
  in a small session header (sequence number of the first message plus a
  message count), so the same gap-detection logic serves both feeds.
- **(owner) The wire carries quotes, not ladder levels.** Message set at
  minimum: day start/end and one quote message per product per day
  (market, low, mid, high, in cents), about 36M messages for the full
  warehouse. Synthetic resting levels are **not** encoded: writing every
  one-unit level would be on the order of a billion messages (tens of GB),
  and would freeze the cost settings into the file and go stale on every
  daily ingest. Exact layout is the builder's call; it must be documented
  in a protocol spec file in the repo.
- **(owner) One ladder builder, two consumers.** A single C++ function
  turns a day's quote (plus the cost settings) into that day's book. The
  backtester calls it on the quote data it already loads through
  `engine/native.py`'s arrays; the replay path decodes the protocol file
  and calls the same function. **A required test asserts both paths build
  identical books for the same asset-days.** The backtester does not go
  over the network or read the protocol file; the accurate claim is "the
  backtester and the replay feed build provably identical books". UDP
  multicast is the separate replay path that proves transport and
  recovery. Backtests stay deterministic and fast.

### IEX side

- Data: IEX HIST pcaps, DEEP+ (order-by-order: order id, side, price, size,
  plus add, modify, cancel, execute messages, per the 2025 SEC filing).
- **Verified 2026-10-22** (the earlier "unverified" note is resolved):
  - HIST publishes DEEP+ as feed `DPLS` (v1.0, IEX-TP v1) from 2024-10-01
    onward, alongside `DEEP` and `TOPS` for the same days. A second feed,
    `DPLC`, appears from 2026-08-07; its meaning is not documented on the
    HIST page and it is not used here.
  - Listing endpoint: `https://iextrading.com/api/1.0/hist?date=YYYYMMDD`
    returns JSON with each file's `feed`, `version`, `protocol`, `size`,
    and a download `link`.
  - Spec: IEX DEEP+ Specification v1.06 (Message Protocol ID `0x8005`;
    DEEP is `0x8004`). Messages: `a` add, `M` modify (bit 0 of the flags:
    0 = reset priority, 1 = maintain), `R` delete, `L` order executed
    (residual keeps time priority), `T` trade (non-displayed, does not
    touch displayed book), `B` trade break, `C` clear book, plus system
    and status messages. Prices are int64 fixed-point with 4 implied
    decimals; all fields little-endian. Unknown message types and longer
    than expected messages must be skipped, not rejected.
  - Files are **gzipped pcapng** (not classic pcap), Ethernet link type,
    microsecond packet timestamps (`if_tsresol` 6), and each is **merged
    from several capture files**, so the decoder must expect duplicate and
    out-of-order packets at the merge seams, not only gaps.
  - IEX-TP v1 header, 40 bytes, confirmed on real packets (struct
    `<BBHIIHHqqq`): version, reserved, message protocol id, channel id,
    session id, payload length, message count, stream offset, first
    message sequence number, send time. Each message is prefixed by a
    2-byte length. Heartbeats carry a message count of 0.
  - **Sizes:** a full day is 1.5 to 20 GB per feed, compressed. The
    smallest complete trio found is 2024-12-23 (DPLS 1.69 GB, DEEP
    1.53 GB, TOPS 1.58 GB compressed).
- **Disk is the binding constraint.** Measured 2026-10-22: the Mac has
  13 GB free (97% full) and the VM 6.9 GB. So **(designer's call) the
  reader streams `.pcap.gz` through zlib and never writes a decompressed
  copy to disk**; "zero-copy" means packets are parsed in place inside the
  decompression buffer, not that the file is memory-mapped. Throughput is
  reported two ways: decode plus book on a decompressed slice held in RAM
  (the system's own speed) and end to end including gunzip (what a full
  file costs). The downloader refuses to start a download that would leave
  less than 2 GB free.
- **IEX HIST terms of use** (`iextrading.com` "IEX Historical Data Terms of
  Use") must be read and summarized in the repo before any data is
  downloaded; data stays out of git.
- **Book correctness oracle:** IEX publishes DEEP (aggregated levels) and
  TOPS (top of book) for the same days. Rebuild the book from DEEP+, then
  check its aggregated levels against DEEP and its best bid/offer against
  TOPS at matching sequence points. Any mismatch is a bug until explained.
- Decoder hygiene counts are reported, not assumed: messages decoded,
  unknown message types, gaps, duplicates.

### Transport

- UDP multicast over the VM's loopback. Publisher replays a stream at a
  set rate (1x up to unthrottled). The feed handler detects gaps and
  duplicates by sequence number, and recovers by requesting a retransmit of
  the missing range (a small TCP or UDP request channel to the publisher)
  or, past a window, a full snapshot. Recovery design detail is the
  builder's.
- **Recovery is tested with induced loss:** a drop-injection mode in the
  publisher (random and burst), and the test asserts the recovered book is
  identical to the book built from the file directly.

### Measurement

- **Throughput:** messages per second end to end (pcap mapped, decoded,
  applied to the book), on full real IEX days.
- **Latency:** per-message decode-plus-apply time, as a histogram with
  p50/p99/p999 and max. The clock and its own overhead are stated (on arm64
  the virtual counter `cntvct_el0` is the likely choice; the builder
  measures the timer's resolution and overhead and reports them). Never an
  average alone.
- **Pokemon replay throughput:** messages per second for the full
  warehouse (about 36M quote messages), with and without building each
  day's ladders.
- **Backtest cost:** wall-clock of a full-window backtest and walk-forward,
  book vs formula, so the price of the book is known.
- **Profiling:** time-based flamegraphs. Every optimization claim is
  before/after on the same benchmark.
- Benchmark harness and results are committed (scripts plus a results file
  with hardware, date, git SHA), in the style of `scripts/bench_engines.py`.

### Future trade feed

The Pokemon decoder's event type must be able to carry a trade (price,
quantity, time). No trade source is implemented. If the owner is ever
approved for eBay Marketplace Insights, trades would calibrate ladder depth
and shape without a redesign.

## Prerequisite (owner)

This work **starts from `main` after `feat/instrument-repair-horizon` is
finished and merged.** As of 2026-10-22 that branch has 18 commits not on
`main`, and CLAUDE.md marks its benchmark and horizon figures as superseded
pending a re-run after the 2026-08-09 fix wave. Finishing it means doing
that re-run (benchmark, six strategies, horizon, `pkmn evaluate`; budget
75-140 min per ML walk-forward with `--workers 1` under `caffeinate
-dimsu`), recording it, and merging. Those re-run figures are the formula
baseline this design's book-vs-formula comparison is measured against. That
finishing work is not part of this design's plan.

## Order of work (suggested)

The plan should split this into phases, each ending in a working, tested
state:

1. **Verify and fixture:** confirm DEEP+ availability, spec versions, and
   HIST terms; download one day; build small pcap fixtures; set up the VM
   toolchain (cmake, profiler).
2. **IEX side:** pcap reader, IEX-TP, DEEP+ decoder, book core, oracle
   check against DEEP/TOPS, throughput and latency benchmarks.
3. **Pokemon side:** protocol spec, encoder, decoder, daily ladder,
   equivalence test, book execution in the engine, `--execution` switch,
   registry recording, goldens.
4. **Transport:** UDP publisher, feed handler, gap recovery, drop
   injection tests.
5. **Research re-run:** benchmark, six strategies, horizon, `pkmn evaluate`
   through the book, with book vs formula side by side, written up in a new
   section of `docs/research-findings-2026-07.md`; README and CLAUDE.md
   status updated.

## Success criteria

- Book core: unit tests for every operation and priority rule; the DEEP+
  book matches DEEP and TOPS at every checked point on at least one full
  real day.
- Pokemon: the unit-midpoint ladder reproduces the formula within tolerance
  on real days; the formula path still passes bit-for-bit parity.
- Transport: zero divergence between the recovered book and the direct book
  under induced random and burst loss.
- Measurements: throughput, latency histogram, and replay figures recorded
  with hardware, method, date, and git SHA.
- Research: every strategy and the benchmark re-run through the book, with
  the change from the formula numbers reported plainly, including if it is
  nothing.

## Left to the builder

- Book data structures and memory layout (justified by measurement).
- Exact Pokemon protocol layout and session header format.
- Retransmit/snapshot recovery mechanics and window sizes.
- Timer choice details, histogram implementation, profiler choice in the VM.
- Python binding surface for book execution and benchmarks (keep it
  minimal; follow `engine/native.py`'s single-crossing style).
- Whether the IEX decoder also handles TOPS/DEEP fully or only as far as
  the oracle check needs.
