# IEX book oracle

Results of `iex_oracle`. It rebuilds the order book from IEX's order-by-order DEEP+ feed
and checks it against IEX's own aggregated DEEP price levels and TOPS best quotes for the
same day. Newest first.

## 2026-10-22: 2024-12-23 (an incident day, not a full normal session), success criterion NOT met

**The spec's success criterion, "matches at every checked point on at least one full real
day", is not met on 2024-12-23.** 7,870 of 96,974,545 checked points mismatch (96,980,017
checkpoints evaluated, of which the 5,472 excluded by name are not checked). Almost all of
them (7,819) trace to an IEX-side order-id rewind that no IEX specification documents.
The open options are a second oracle day, asking IEX, or a named rule approved by the
owner. None has been taken. The tool keeps reporting the rewind family. A run over all
three files exits 1 (MISMATCH): every validity precondition held, and there are
mismatches.

**What the files cover.** 2024-12-23 is not a normal full session:

- At 10:22:29.38 ET every symbol gets an Operational Halt.
- At 11:32:50.036 ET all three feeds restart from sequence 1. DEEP+ messages resume at
  11:39 ET.
- DEEP+ carries 50,878,184 messages before the restart and 80,992 after it (50,959,176 in
  all).
- The last book event on DEEP+ and DEEP is at 13:45:40.82 ET, and the last TOPS quote at
  13:45:52.02 ET. Regular hours end at 16:00 ET. The DEEP+ stream itself runs on with
  system events to its end-of-messages event at 17:10 ET, but carries no book event after
  13:45:40.

So the checks cover a morning plus a thin early afternoon, not a trading day. **Even a
zero-mismatch run on these files would arguably not meet the "full real day" criterion.**
The second oracle day should be a normal session: no incident, no mid-day restart, and
not on a DST boundary (the tool's ET labels assume UTC-5). One inference, not
established: the 09:48:38 order-id rewind and the 6 `H`/`O`/`P` messages in that second
may be part of the same incident.

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

First run (2026-10-22): wall time 72.5 s, peak RSS 48.3 MiB. Re-run 2026-10-23 after the
exit-status and checked-count fix (same files, same command): wall time 61.0 s, peak RSS
51.1 MiB, and every count identical except that the TOPS line now splits evaluated,
excluded and checked.

### Exit status

| exit | result | meaning |
|---|---|---|
| 0 | PASS | every validity precondition held, and 0 mismatches |
| 1 | MISMATCH | every validity precondition held, and at least one mismatch |
| 3 | INCONCLUSIVE | at least one validity precondition failed, whatever the mismatch count |
| 2 | error | usage or I/O error |

The validity preconditions are checked after the run, and each failure is printed by
name:

- On each of DEEP+, DEEP and TOPS: 0 sequence gaps, 0 `tp_parse_failures` and 0
  `other_stream` segments. A restart under a new session id would make every later segment
  `other_stream`, which would otherwise leave the rest of that feed silently unchecked.
- 0 DEEP+ per-symbol timestamp regressions, and 0 DEEP transitions left open.
- Book anomalies `unknown_id`, `duplicate_add` and `exec_clamped` all 0.
- 0 `late_checkpoint` (a check that arrived after DEEP+ had passed its timestamp).
- At least 1 DEEP check and at least 1 TOPS check.

On 2024-12-23 every precondition holds and the run exits 1.

### Headline

| | |
|---|---|
| DEEP+ book events applied | 50,165,396 (0 gaps, 0 duplicates, 1 restart) |
| DEEP event-complete checkpoints | 49,732,804 (51,357,193 levels checked) |
| TOPS checkpoints | 45,622,824 evaluated, 5,472 excluded by name, 45,617,352 checked |
| points evaluated | 96,980,017 |
| excluded by name | 5,472 (`tops_unavailable_zero_quote`), not in checked |
| **checked points** | **96,974,545** |
| **mismatches** | **7,870** (0.008% of checked) in 8 symbols |
| validity preconditions | all held |
| exit status | 1 (MISMATCH) |
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
  zero, against a book that still has orders. Every other quote is checked. The spec
  documents the flag; the all-zero values are observed behaviour. The excluded quotes are
  not in the checked count. The same zero quote against a book that is itself empty on both
  sides is compared like any other quote, matches, and is counted as checked. The tool does
  not print how many of those there are.
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
  Data resumes at 11:39 ET. DEEP+ carries 50,878,184 messages before the restart and only
  80,992 after it. The last book event on DEEP+ and DEEP is at 13:45:40.82 ET, and the last
  TOPS quote at 13:45:52.02 ET. Regular hours end at 16:00 ET. The DEEP+ stream continues
  with system events only, up to end-of-messages at 17:10 ET.
- **Operational halt** of every symbol at 10:22:29.38 ET; see Halt and round-lot handling.
- 0 gaps and 0 duplicates on all three feeds. DEEP+ capture timestamps run about 37 s
  ahead of feed timestamps, which is why only feed timestamps are used.
