# IEX book oracle

Results of `iex_oracle`. It rebuilds the order book from IEX's order-by-order DEEP+ feed
and checks it against IEX's own aggregated DEEP price levels and TOPS best quotes for the
same day. Newest first.

## 2026-10-22: 2024-12-23 full day, success criterion NOT met

**The spec's success criterion, "matches at every checked point on at least one full real
day", is not met on 2024-12-23.** 7,870 of 96,980,017 checked points mismatch. Almost all
of them (7,819) trace to an IEX-side order-id rewind that no IEX specification documents.
The open options are a second oracle day, asking IEX, or a named rule approved by the
owner. None has been taken. The tool keeps reporting the rewind family, and the default
full-day run exits 1.

### Day and files

- `data/iex/20241223_IEXTP1_DPLS1.0.pcap.gz` (DEEP+, protocol 0x8005)
- `data/iex/20241223_IEXTP1_DEEP1.0.pcap.gz` (DEEP, 0x8004)
- `data/iex/20241223_IEXTP1_TOPS1.6.pcap.gz` (TOPS, 0x8003)

The run streams all three files concurrently (never decompressed to disk). Command, as run
in the OrbStack VM with a Release build:

```
cpp/build-linux-release/iex_oracle --deep-plus data/iex/20241223_IEXTP1_DPLS1.0.pcap.gz \
    --deep data/iex/20241223_IEXTP1_DEEP1.0.pcap.gz --tops data/iex/20241223_IEXTP1_TOPS1.6.pcap.gz
```

Wall time 72.5 s, peak RSS 48.3 MiB.

### Headline

| | |
|---|---|
| DEEP+ book events applied | 50,165,396 (0 gaps, 0 duplicates, 1 restart) |
| DEEP event-complete checkpoints | 49,732,804 (51,357,193 levels checked) |
| TOPS checkpoints | 45,622,824 |
| **checked points** | **96,980,017** |
| **mismatches** | **7,870** (0.008%) in 8 symbols |
| excluded by name | 5,472 (`tops_unavailable_zero_quote`) |
| book anomalies | unknown_id 0, duplicate_add 0, exec_clamped 0 |
| per-symbol DEEP+ timestamp regressions | 0 |
| late checkpoints (merge failures) | 0 |

### Checkpoint semantics

At every checkpoint (symbol s, timestamp t), every DEEP+ event for s with `ts_ns <= t`
has been applied, and none later. Timestamps are the feeds' message timestamps, never pcap
capture time.

1. **DEEP: every level an atomic transition touched.** A Price Level Update with Event Flags
   ON closes one atomic transition (DEEP v1.08 pp.17-19). Every level the transition
   touched is checked at the closing update's timestamp, last update per (side, price)
   winning: its Event Flags OFF updates plus the closing one. The check is
   `qty_at(side, price) == DEEP size`, where size 0 means the level is gone.
2. **Same-timestamp atomicity.** Messages with the same timestamp "were caused by the same
   event and may be interpreted to have happened simultaneously and atomically" (DEEP
   v1.08 p.4, TOPS v1.66 p.4). For one (symbol, timestamp), only the final state is a
   checkpoint. 5 earlier same-timestamp DEEP level updates and 66 earlier same-timestamp
   TOPS quotes were superseded, and the tool prints both counts.
   - Example: TRFM at 09:30:05.112037164. One event moves both sides, and TOPS sends two
     quotes at the same nanosecond. The first is stale on the bid.
   - Example: CGDG at 09:38:35.566091395. An `R` and an `a` share one timestamp, and DEEP
     sends two event-complete updates for 29.59 at that nanosecond (0, then 100).
3. **TOPS is checked against IEX's top-of-book rule, not the raw best level.** Since
   SR-IEX-2021-01 (SEC Release No. 34-90933, 2021-01-15, pp.7-8), displayed odd lots
   aggregate. Per side, the TOPS quote is the most aggressive price at which aggregate
   displayed interest at that price or better reaches one round lot, with that entire
   aggregate size. For example, 25 + 65 @ 10.02 plus 30 @ 10.01 is quoted as 120 @ 10.01.
   No qualifying price is quoted 0 / 0 (TOPS v1.66 p.16, "zero quote"). The rule differs
   from the raw best level on 4,628,467 of the day's quotes. Against raw `best()`, the
   first 50 symbols alone gave 229,128 TOPS mismatches.

### Halt and round-lot handling

- **Halt zero quote (excluded by name, 5,472).** At 10:22:29.38 ET every symbol gets an
  Operational Halt (`O` = 'O', DEEP/TOPS p.13). TOPS then sends quotes with Symbol
  Availability flag 0x80 ("halted, paused, or otherwise not available for trading on IEX",
  TOPS v1.66 p.26) and all four fields zero. DEEP+ and DEEP send no deletes, so the book
  keeps its resting orders. Only that exact shape is excluded: flag 0x80 *and* all fields
  zero. Every other quote is checked. The spec documents the flag; the all-zero values are
  observed behaviour.
- **Round lots (counted by default, 51).** The round lot comes from the DEEP+ Security
  Directory (p.14) when one is sent. That day only 6 are sent, all for IEX-listed
  securities. Otherwise the round lot is 100. NVR (45 mismatches), MKL (3) and SEB (3) are
  quoted by TOPS in 10-17-share and 1-share sizes, so their round lot is not 100. With
  `--round-lot NVR=10 --round-lot MKL=10 --round-lot SEB=1` all 51 disappear. The option is
  opt-in because those values were inferred from TOPS itself. No December 2024 reference
  was found: Reg NMS tiered round lots began only in November 2025, and SEB = 1 does not
  match those tiers.

### Mismatch table (default run)

| category | count | symbols |
|---|---|---|
| deep_level_ask | 7 | DRIP 3, OTEX 3, CNH 1 |
| deep_level_bid | 29 | CNH 24, OTEX 4, KBE 1 |
| tops_ask | 6,632 | OTEX 3,290, KBE 3,279, NVR 38, CNH 17, DRIP 5, MKL 3 |
| tops_bid | 1,202 | WMT 1,152, CNH 30, NVR 7, DRIP 5, OTEX 4, SEB 3, KBE 1 |
| **total** | **7,870** | 7,819 in the rewind family (DRIP, OTEX, CNH, KBE, WMT) + 51 round lot (NVR, MKL, SEB) |

### The order-id rewind (unexplained, not excluded)

- **What happens.** Starting at **09:48:38 ET**, per-symbol DEEP+ Add order ids go
  backwards by about 176k-191k. Across the day there are 926,703 Adds below their symbol's
  running maximum id, in 901 symbols. Each symbol's first rewind falls at its next Add
  after 09:48:38: 67 symbols in that second, 105 in the next, and a tail to 10:21. (51
  symbols also show non-monotonic ids at the 09:30:00 open; these cause no mismatch.)
- **The only other signal.** In the same second DEEP+ carries exactly 6 `H`, 6 `O` and 6 `P`
  messages, which looks like a re-spin of the day's 6 IEX-listed securities. There is no
  system event.
- **Consequence.** After the rewind, DEEP+ `M`/`R` messages carry ids that alias orders
  still resting from before it. DEEP disagrees, and some DEEP changes have no DEEP+ message
  at all. An independent Python replay agreed with the C++ book message for message, so
  the book is applying DEEP+ correctly.
  - **DRIP.** Id …132058 is a Buy 1100 @ 12.11, added at 09:35:35. At 09:53:49.940868078 a
    DEEP+ `M` moves …132058 to 200 @ 12.21. DEEP adds the 200 on the *sell* side at 12.21
    and leaves 12.11 unchanged. A DEEP+ `R` for …132058 at 09:53:50.48 removes the 200 from
    DEEP's sell side. DEEP then drops the 1100 @ 12.11 at 09:59:39.844215338, with no DEEP+
    message at that timestamp.
  - **OTEX.** Id …816975 is a Sell 300 @ 35.00, added at 09:30:01. At 09:48:51.783705577 an
    `M` moves it to 100 @ 27.68. DEEP shows that 100 on the *bid*, and DEEP's 35.00 ask
    keeps the 300 (it mismatches again at 10:23:58). Neighbouring ids …971 and …974 get
    plausible same-side modifies at the same moment.
  - **WMT.** Id …903738 is a Buy 100 @ 78.00, added at 09:30:22. At 09:49:19.702 an `M`
    moves it to 89.64. DEEP shows no change at the `M`'s timestamp and keeps 100 @ 78 (TOPS
    reads 135 @ 78). DEEP adds 100 @ 89.64 at a different nanosecond, with no DEEP+ message
    there.
  - **KBE and CNH** show the same 100-share, opposite-side signature after their rewinds.
- **Scope.** Only 5 of the 901 rewound symbols mismatch. A wrong level is only caught
  again when DEEP next updates it, or when it reaches the TOPS quote. That is why OTEX,
  KBE and WMT run to thousands of TOPS mismatches from a single 100-share divergence. The
  rewound id state is wiped when every book is cleared at the 11:32:50 restart of all
  three feeds. I did not separately measure whether any rewind-family mismatch falls
  after that restart.
- No DEEP+, DEEP or TOPS specification documents this. It is neither excluded nor
  quarantined.

### Feed facts

- **Simultaneous restart.** All three feeds restart at the identical send_time
  1734971570036197067 (11:32:50.036 ET), each detected by its own sequence tracker. The
  oracle resolves every pending check from before the restart, then clears every book.
  Data resumes at 11:39 ET. All three feeds end at about 13:45:40 ET (TOPS 13:45:52).
- **Operational halt** of every symbol at 10:22:29.38 ET; see Halt and round-lot handling.
- 0 gaps and 0 duplicates on all three feeds. DEEP+ capture timestamps run about 37 s
  ahead of feed timestamps, which is why only feed timestamps are used.
