# IEX book benchmarks

Results of `scripts/bench_iex.sh` (DEEP+ decode + `BookSet::apply`, `cpp/tools/iex_bench.cpp`), newest first.

Every result here is measured on one capture, the 2024-12-23 DPLS file, so it describes that day's message mix,
not a normal session's. That day was an incident: every symbol was operationally halted at 10:22:29 ET, the feed
restarted at 11:32:50 ET, 50,878,184 of its 50,959,176 messages come before the restart and 80,992 after, and the
last book event is at 13:45:40 ET (see `docs/iex-oracle.md`). Throughput and latency on a normal full day may
differ.

## 2026-10-22 — 20241223_IEXTP1_DPLS1.0.pcap.gz

- Hardware: Apple M1 Pro, 7-vCPU OrbStack Ubuntu 24.04 arm64 VM
- Host: `ubuntu Linux 7.2.9-orbstack-00391-g2b298d05fbd4 aarch64`
- git: `bc6e8059807b`
- Compiler: `g++ 13.3.0`
- Flags: `CMAKE_BUILD_TYPE=Release: -O3 -DNDEBUG -ffp-contract=off`
- Timer (ram run 2, the median run the percentiles come from): cntvct_el0 (isb before read), cntfrq 24,000,000 Hz, calibrated 0.024000 ticks/ns, resolution 41.7 ns per tick; empty ticks() pair 10.5 ns mean (p50 0 ticks; 11.9, 10.5, 9.5 ns in runs 1-3); steady_clock resolution 41 ns
- *Corrected 2026-10-23, not re-run:* the Timer line above originally showed run 1's overhead (11.9 ns) while the percentiles come from run 2. The generator now takes it from the median run; this line is that generator's output on the JSON below, pasted in by hand.
- ram held 3,445,841,433 payload bytes in 42,492,551 packets (cap 4,294,967,296, truncated: false); 50,959,176 messages, 1 restart(s)

> **Quantization warning (generated):** p50 = 2 ticks (±50.0%) of a 41.7 ns counter. Each sample is a whole number of ticks, so a percentile at or under 10 ticks carries a ±1-tick uncertainty of 10% or more; a change smaller than one tick (41.7 ns) at that percentile cannot be seen. Use the timed-region mean (not quantized) and msgs/s for small changes.

| | msgs/s | mean ns/msg | p50 | p99 | p99.9 | max |
|---|---:|---:|---:|---:|---:|---:|
| ram throughput pass, best of 3 | 7,342,902 | 136.2 (a) | | | | |
| ram latency pass, median run | | 136.3 (b) | 83 ns (2 ticks, ±50.0%) | 626 ns (15 ticks, ±6.7%) | 6,448 ns (155 ticks, ±0.6%) | 40,436,760 ns |
| stream, end to end incl. gunzip | 2,497,490 | 400.4 (c) | | | | |

- (a) throughput pass wall time / messages, no per-message timer: SegmentFilter (IEX-TP framing, sequence tracking) + decode + `BookSet::apply`.
- (b) timed-region mean: sum of per-message tick deltas / messages / ticks_per_ns, the mean of exactly what the percentiles measure (decode + apply plus one ticks() pair, whose empty mean is 10.5 ns). Not quantized: per-sample rounding averages out. It excludes SegmentFilter, so it is not comparable to (a).
- (c) stream wall time / messages, including inflate and pcapng parsing.
- Percentiles: whole ticks converted to ns; the band is ±1 tick relative to the value. Max is exact; a single sample of milliseconds is most likely the VM being descheduled or a page fault (not verified: no hardware counters), not decode + apply.
- Latency pass wall time / messages (median run): 208.2 ns. This is the cost of the instrumented loop (SegmentFilter, decode + apply, the timer pair, two histogram records and an llround per message), not a latency figure.

Run-to-run ram throughput: 7,342,902, 7,302,905, 6,355,436 msgs/s (spread 13.5% of the median).

| ram run | msgs/s | timed-region mean ns | p50 ticks | p99 ticks | p99.9 ticks | max ns |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 7,342,902 | 129.8 | 2 | 14 | 116 | 12,069,912 |
| 2 | 7,302,905 | 136.3 | 2 | 15 | 155 | 40,436,760 |
| 3 | 6,355,436 | 129.5 | 2 | 13 | 130 | 162,325,249 |

Profile: `docs/benchmarks/iex-book-flamegraph.svg` (throughput pass only, cpp/build-linux-prof = these flags plus `-g -fno-omit-frame-pointer`, one separate ram run under `perf record -F 999 -g`; perf: `perf version 6.8.12 task-clock:upppH`). Top self-time leaves:

```
 22.68%  pkmn::book::OrderBook::level_pos
 15.78%  pkmn::md::parse_tp_header
 13.77%  pkmn::md::decode_deep_plus
 12.52%  std::__detail::_Map_base<unsigned long, std::pair<unsigned long const, pkmn::book::OrderBook>, std::allocator<std::pair<unsigned long const, pkmn::book::OrderBoo
  7.96%  pkmn::md::SegmentFilter::accept
  5.62%  bench::PassResult bench::replay<pkmn::md::CallStep>
  5.40%  pkmn::book::OrderBook::unlink_free
  5.23%  pkmn::book::OrderBook::add
  3.42%  pkmn::book::OrderBook::erase_entry
  2.74%  pkmn::book::OrderBook::find_entry
  1.80%  pkmn::book::OrderBook::link_back
  1.19%  pkmn::md::BookSet::apply
  0.53%  pkmn::book::OrderBook::alloc_node
  0.38%  pkmn::book::OrderBook::remove
  0.30%  pkmn::book::OrderBook::free_node
```

Top inclusive frames below the throughput pass's replay loop:

```
 22.94%  pkmn::book::OrderBook::remove
 22.81%  pkmn::md::SegmentFilter::accept
 22.68%  pkmn::book::OrderBook::level_pos
 17.54%  pkmn::book::OrderBook::add
 17.40%  pkmn::book::OrderBook::unlink_free
 15.78%  pkmn::md::parse_tp_header
 13.77%  pkmn::md::decode_deep_plus
 13.48%  pkmn::md::BookSet::apply
 12.57%  std::__detail::_Map_base<unsigned long, std::pair<unsigned long const, pkmn::book::OrderBook>, std::allocator<std::pair<unsigned long const, pkmn::book::OrderBoo
 12.30%  pkmn::book::OrderBook::link_back
  3.63%  pkmn::book::OrderBook::erase_order
  3.42%  pkmn::book::OrderBook::erase_entry
  2.74%  pkmn::book::OrderBook::find_entry
  2.43%  pkmn::book::OrderBook::modify
  0.59%  pkmn::book::OrderBook::alloc_node
```

<details><summary>Raw JSON (ram runs 1-3, stream)</summary>

```json
{
  "host": "ubuntu Linux 7.2.9-orbstack-00391-g2b298d05fbd4 aarch64",
  "compiler": "g++ 13.3.0",
  "flags": "CMAKE_BUILD_TYPE=Release: -O3 -DNDEBUG -ffp-contract=off",
  "git_sha": "bc6e8059807b",
  "tree_hash": null,
  "file": "/Users/ml3787/pkmn-stock/data/iex/20241223_IEXTP1_DPLS1.0.pcap.gz",
  "mode": "ram",
  "timer": "cntvct_el0 (isb before read)",
  "cntfrq_hz": 24000000,
  "ticks_per_ns": 0.024000009,
  "timer_resolution_ns": 41.667,
  "timer_overhead_ns": 11.891,
  "timer_overhead_ticks_p50": 0,
  "timer_overhead_ticks_max": 62811,
  "steady_clock_resolution_ns": 41,
  "ram_bytes": 4294967296,
  "held_bytes": 3445841433,
  "held_packets": 42492551,
  "ram_truncated": false,
  "load_seconds": 18.888,
  "segments": 42492551,
  "restarts": 1,
  "orders_at_end": 0,
  "unknown_id": 0,
  "messages": 50959176,
  "seconds": 6.94,
  "msgs_per_sec": 7342902,
  "batch_ns_per_msg": 136.19,
  "timed_region_mean_ns": 129.85,
  "latency_pass_wall_seconds": 9.295,
  "latency_pass_wall_ns_per_msg": 182.4,
  "p50_ticks": 2,
  "p99_ticks": 14,
  "p999_ticks": 116,
  "max_ticks": 289678,
  "p50_ns": 83,
  "p99_ns": 582,
  "p999_ns": 4848,
  "max_ns": 12069912
}
{
  "host": "ubuntu Linux 7.2.9-orbstack-00391-g2b298d05fbd4 aarch64",
  "compiler": "g++ 13.3.0",
  "flags": "CMAKE_BUILD_TYPE=Release: -O3 -DNDEBUG -ffp-contract=off",
  "git_sha": "bc6e8059807b",
  "tree_hash": null,
  "file": "/Users/ml3787/pkmn-stock/data/iex/20241223_IEXTP1_DPLS1.0.pcap.gz",
  "mode": "ram",
  "timer": "cntvct_el0 (isb before read)",
  "cntfrq_hz": 24000000,
  "ticks_per_ns": 0.024000019,
  "timer_resolution_ns": 41.667,
  "timer_overhead_ns": 10.486,
  "timer_overhead_ticks_p50": 0,
  "timer_overhead_ticks_max": 14293,
  "steady_clock_resolution_ns": 41,
  "ram_bytes": 4294967296,
  "held_bytes": 3445841433,
  "held_packets": 42492551,
  "ram_truncated": false,
  "load_seconds": 19.585,
  "segments": 42492551,
  "restarts": 1,
  "orders_at_end": 0,
  "unknown_id": 0,
  "messages": 50959176,
  "seconds": 6.978,
  "msgs_per_sec": 7302905,
  "batch_ns_per_msg": 136.93,
  "timed_region_mean_ns": 136.3,
  "latency_pass_wall_seconds": 10.607,
  "latency_pass_wall_ns_per_msg": 208.15,
  "p50_ticks": 2,
  "p99_ticks": 15,
  "p999_ticks": 155,
  "max_ticks": 970483,
  "p50_ns": 83,
  "p99_ns": 626,
  "p999_ns": 6448,
  "max_ns": 40436760
}
{
  "host": "ubuntu Linux 7.2.9-orbstack-00391-g2b298d05fbd4 aarch64",
  "compiler": "g++ 13.3.0",
  "flags": "CMAKE_BUILD_TYPE=Release: -O3 -DNDEBUG -ffp-contract=off",
  "git_sha": "bc6e8059807b",
  "tree_hash": null,
  "file": "/Users/ml3787/pkmn-stock/data/iex/20241223_IEXTP1_DPLS1.0.pcap.gz",
  "mode": "ram",
  "timer": "cntvct_el0 (isb before read)",
  "cntfrq_hz": 24000000,
  "ticks_per_ns": 0.023999994,
  "timer_resolution_ns": 41.667,
  "timer_overhead_ns": 9.507,
  "timer_overhead_ticks_p50": 0,
  "timer_overhead_ticks_max": 8556,
  "steady_clock_resolution_ns": 41,
  "ram_bytes": 4294967296,
  "held_bytes": 3445841433,
  "held_packets": 42492551,
  "ram_truncated": false,
  "load_seconds": 17.785,
  "segments": 42492551,
  "restarts": 1,
  "orders_at_end": 0,
  "unknown_id": 0,
  "messages": 50959176,
  "seconds": 8.018,
  "msgs_per_sec": 6355436,
  "batch_ns_per_msg": 157.35,
  "timed_region_mean_ns": 129.46,
  "latency_pass_wall_seconds": 9.258,
  "latency_pass_wall_ns_per_msg": 181.67,
  "p50_ticks": 2,
  "p99_ticks": 13,
  "p999_ticks": 130,
  "max_ticks": 3895805,
  "p50_ns": 83,
  "p99_ns": 542,
  "p999_ns": 5424,
  "max_ns": 162325249
}
{
  "host": "ubuntu Linux 7.2.9-orbstack-00391-g2b298d05fbd4 aarch64",
  "compiler": "g++ 13.3.0",
  "flags": "CMAKE_BUILD_TYPE=Release: -O3 -DNDEBUG -ffp-contract=off",
  "git_sha": "bc6e8059807b",
  "tree_hash": null,
  "file": "/Users/ml3787/pkmn-stock/data/iex/20241223_IEXTP1_DPLS1.0.pcap.gz",
  "mode": "stream",
  "timer": "cntvct_el0 (isb before read)",
  "cntfrq_hz": 24000000,
  "ticks_per_ns": 0.024000014,
  "timer_resolution_ns": 41.667,
  "timer_overhead_ns": 10.204,
  "timer_overhead_ticks_p50": 0,
  "timer_overhead_ticks_max": 19279,
  "steady_clock_resolution_ns": 41,
  "packets": 42492551,
  "segments": 42492551,
  "restarts": 1,
  "orders_at_end": 0,
  "messages": 50959176,
  "seconds": 20.404,
  "msgs_per_sec": 2497490,
  "p50_ns": null,
  "p99_ns": null,
  "p999_ns": null,
  "max_ns": null
}
```

</details>


## Task 6 — interleaved A/B, baseline book vs optimized book (hand-written, 2026-10-22)

Not generated by `scripts/bench_iex.sh`; the generated section above is the clean-SHA result for the kept
version.

- **A (before):** `1c14e20`, whose `cpp/` is identical to `c9e60e0` (only this file changed in between),
  checked out as a `git worktree` in a scratch directory and built Release in its own build dir. Its JSON
  reads `1c14e20e25d7-dirty` only because that build dir sits untracked inside the worktree.
- **B (after):** the optimized `OrderBook`, built Release in `cpp/build-linux-release` from the working tree
  that was then committed unchanged as `bc6e805` (`git diff 1c14e20 bc6e805` touches only
  `order_book.{hpp,cpp}`). JSON reads `1c14e20e25d7-dirty` with a working-tree hash.
- Hardware: Apple M1 Pro, 7-vCPU OrbStack Ubuntu 24.04 arm64 VM; `g++ 13.3.0`,
  `-O3 -DNDEBUG -ffp-contract=off` both sides.
- Timer (both sides, every run): cntvct_el0 (isb before read) at 24 MHz, 41.7 ns per tick; measured empty
  ticks() pair overhead 8.8-13.9 ns mean (median 9.6) for A and 9.0-17.2 ns (median 9.4) for B, included in
  every timed-region mean and percentile below. Hardware: Apple M1 Pro, 7-vCPU OrbStack Ubuntu 24.04 arm64 VM.
- **Method:** `iex_bench <DPLS file> --mode ram`, one VM session, strictly alternating A B A B ..., 9 pairs
  in two batches (pairs 1-4 at 23:17-23:22 ET, pairs 5-9 at 23:32-23:39 ET). The Mac host was heavily loaded
  by unrelated processes during batch 1 (load average up to 112; batch 2 started after it fell under 20),
  which is why pair 4 is slow on both sides.

| pair | A msgs/s | B msgs/s | B/A | A timed mean ns | B timed mean ns | B/A | A p50/p99/p99.9 ticks | B p50/p99/p99.9 ticks | A max ns | B max ns |
|---|---:|---:|---:|---:|---:|---:|---|---|---:|---:|
| 1 | 4,625,301 | 7,336,914 | 1.59 | 184.3 | 118.2 | 0.64 | 3/18/162 | 2/12/121 | 10,716,821 | 30,010,219 |
| 2 | 4,896,135 | 5,747,977 | 1.17 | 169.6 | 141.0 | 0.83 | 3/16/156 | 2/15/167 | 32,305,347 | 26,528,996 |
| 3 | 4,926,764 | 7,277,689 | 1.48 | 176.8 | 110.5 | 0.62 | 3/14/120 | 2/11/146 | 146,598,462 | 14,512,488 |
| 4 | 3,425,199 | 4,823,997 | 1.41 | 251.3 | 121.5 | 0.48 | 3/21/182 | 2/13/155 | 99,277,244 | 68,675,210 |
| 5 | 4,712,531 | 5,899,858 | 1.25 | 172.4 | 119.9 | 0.70 | 3/16/152 | 2/13/152 | 10,666,538 | 22,158,570 |
| 6 | 4,629,145 | 7,120,518 | 1.54 | 211.2 | 139.0 | 0.66 | 3/20/170 | 2/15/157 | 75,356,370 | 28,768,122 |
| 7 | 4,356,942 | 5,558,656 | 1.28 | 212.3 | 130.2 | 0.61 | 3/19/171 | 2/14/156 | 93,275,887 | 38,558,018 |
| 8 | 4,782,551 | 7,584,533 | 1.59 | 194.6 | 116.0 | 0.60 | 3/18/169 | 2/12/133 | 30,462,070 | 11,188,176 |
| 9 | 3,892,240 | 5,800,307 | 1.49 | 175.5 | 137.9 | 0.79 | 3/16/145 | 2/15/159 | 20,938,505 | 35,322,948 |

| | A (before) | B (after) | change |
|---|---:|---:|---:|
| ram msgs/s, best of 9 | 4,926,764 | 7,584,533 | +53.9% |
| ram msgs/s, median of 9 | 4,629,145 | 5,899,858 | +27.5% |
| timed-region mean ns, best of 9 | 169.6 | 110.5 | −34.8% |
| timed-region mean ns, median of 9 | 184.3 | 121.5 | −34.1% |
| p50, median (ticks) | 3 | 2 | cannot evidence a change (±33-50% band) |
| p99, median | 750 ns (18 ticks, ±5.6%) | 542 ns (13 ticks, ±7.7%) | −28%, but A's p99 ranged 14-21 ticks (39% spread) and B's 11-15 (31%); the ranges overlap |
| p99.9, median | 6,736 ns (162 ticks, ±0.6%) | 6,448 ns (155 ticks, ±0.6%) | −4%, inside run-to-run noise |
| max | 10.7-146.6 ms | 11.2-68.7 ms | VM-dominated, not comparable |

**Within-session spread** ((max − min) / median, 9 runs): A msgs/s 32.4%, B msgs/s 46.8%; A timed mean
44.4%, B timed mean 25.1%. Batch 2 alone (5 pairs, quieter host): A msgs/s 19.2%, B 34.3%; A timed mean
20.5%, B 17.6%.

**Decision: kept, on the pairing and the timed-mean ranges, not on the spread rule alone.** Against the
pooled 9-run spreads only best msgs/s clears, and narrowly: +53.9% against a largest spread of 46.8%.
Median msgs/s (+27.5%) does not clear the pooled msgs/s spreads (32.4% A, 46.8% B), and median timed mean
(−34.1%) is under A's pooled timed-mean spread (44.4%), though above B's (25.1%). On the quieter batch 2,
best msgs/s (+58.6% vs 34.3%) and median timed mean (−33.1% vs 20.5%) clear, but median msgs/s (+27.5%) does
not clear B's batch-2 spread (34.3%). The keep rests mainly on two things: the timed-region mean ranges do
not overlap (B's worst run, 141.0 ns, beats A's best, 169.6 ns), and B is faster than A in all 9 pairs
(msgs/s ratio 1.17-1.59, timed mean ratio 0.48-0.83; two-sided sign test p = 2 × 0.5^9 ≈ 0.004). The tail
does not move: p99.9 and max are unchanged within noise, and p99 overlaps between the two builds.

**Correctness:** all Catch2 tests pass on macOS (`cpp/build`) and in the VM (`cpp/build-linux`), existing tests
untouched; `cpp/tests/test_order_book_paths.cpp` (added in the review fix) covers id-table growth, backward-shift
delete across the wrap-around, the binary-search level path, slot reuse after clear, and a seeded
differential run against a naive reference book; a 14-seed randomized differential trace (add/remove/modify/execute/match/depth/clear, deep books
past the 8-level linear probe, ASan + UBSan) hashed identically for both books, but that was a scratch run
that is not in the repo (the committed differential test in `test_order_book_paths.cpp` runs 3 seeds); `iex_oracle` on the
2024-12-23 files (an incident day; see docs/iex-oracle.md) (VM Release, default args) gives output identical to the baseline build line for line except
wall time and RSS: 96,980,017 points evaluated (96,974,545 checked once the 5,472 excluded by name are
taken out; the tool counted them together at the time), 7,870 mismatches (deep_level_ask 7, deep_level_bid 29,
tops_ask 6,632, tops_bid 1,202, same symbols), book anomalies all 0, exit 1.

**Profile after** (generated section above; `iex-book-flamegraph.svg` is now the after profile, the
baseline one is kept as `iex-book-flamegraph-c9e60e0.svg` / `iex-book-profile-c9e60e0.folded`):
malloc/free/new/delete and the `_Rb_tree`/`_Hashtable` frames are gone from the top leaves. Book inclusive
share fell from remove 29.9% + add 25.0% to remove 22.9% + add 17.5% of a shorter pass. The new largest
single self-time leaf is `OrderBook::level_pos` (22.7%), the per-side level search; it is the next
optimization target (most plausibly cache misses on the first touch of the level vector, not verified:
no hardware counters). The out-of-scope costs are now relatively larger: `parse_tp_header` 15.8%,
`decode_deep_plus` 13.8%, and `BookSet`'s per-symbol `unordered_map::operator[]` 12.5%.

## Task 6 — optimization target (written 2026-10-22, before any code change)

Named from the 2026-10-22 baseline profile below (`c9e60e0c2bf6`, perf `-F 999 -g` over the ram
throughput pass, 10,228,228,218 sample-weight units under `replay<CallStep>`; all percentages are of that
total). The two largest costs are both inside `OrderBook`:

1. **Order removal, `OrderBook::remove` -> `erase_order`: 29.9% inclusive** (erase_order 19.1% self,
   26.9% inclusive). Below it: `cfree` 4.2%, `operator delete` 1.2%, `_Rb_tree_rebalance_for_erase`
   1.4%, `_List_node_base::_M_unhook` 0.7%; plus the `index_` hashtable find under `remove`, 4.7%.
   The large self time is pointer chasing: an `unordered_map` bucket/node hop, a `std::map::find` tree
   walk to re-locate the level by price, then freeing one list node, one hash node and (when the level
   empties) one tree node.
2. **Order insertion, `OrderBook::add`: 25.0% inclusive** (15.7% self). Below it: `operator new` 4.0%,
   `std::map` insert-or-find 2.3%, `_Prime_rehash_policy::_M_need_rehash` 1.4%, `_List_node_base::_M_hook`
   1.3%. Same shape: a hash probe for the duplicate check, a tree walk to the level, and up to three heap
   allocations (list node, hash node, tree node) per order.

Common cause of both: every resting order costs up to three node allocations and three dependent
pointer-chasing lookups. malloc/free/new/delete together are ~10% of the pass (self).

Named but **out of scope for this task** (the brief limits the change to `OrderBook`): `decode_deep_plus`
11.5%, `parse_tp_header` 11.3%, `SegmentFilter::accept` 15.9% inclusive, and `BookSet`'s per-symbol
`std::unordered_map<uint64_t, OrderBook>::operator[]` 8.2%.

Plan: replace the book's three node-based containers with (a) a per-book pooled order arena with a free
list and intrusive index-linked FIFO (no per-order heap allocation in steady state), (b) an
open-addressing `OrderId -> slot` hash table (linear probing, backward-shift delete), and (c) per-side
price levels in a sorted contiguous vector with the best price at the back (most activity is near the
touch, so search starts there). Measured A/B against `c9e60e0`-equivalent code in one VM session; kept
only if it beats the interleaved runs' spread.

## 2026-10-22 — 20241223_IEXTP1_DPLS1.0.pcap.gz

- Hardware: Apple M1 Pro, 7-vCPU OrbStack Ubuntu 24.04 arm64 VM
- Host: `ubuntu Linux 7.2.9-orbstack-00391-g2b298d05fbd4 aarch64`
- git: `c9e60e0c2bf6`
- Compiler: `g++ 13.3.0`
- Flags: `CMAKE_BUILD_TYPE=Release: -O3 -DNDEBUG -ffp-contract=off`
- Timer: cntvct_el0 (isb before read), cntfrq 24,000,000 Hz, calibrated 0.024003 ticks/ns, resolution 41.7 ns per tick; empty ticks() pair 9.4 ns mean (p50 0 ticks); steady_clock resolution 41 ns
- ram held 3,445,841,433 payload bytes in 42,492,551 packets (cap 4,294,967,296, truncated: false); 50,959,176 messages, 1 restart(s)

> **Quantization warning (generated):** p50 = 3 ticks (±33.3%) of a 41.7 ns counter. Each sample is a whole number of ticks, so a percentile at or under 10 ticks carries a ±1-tick uncertainty of 10% or more; a change smaller than one tick (41.7 ns) at that percentile cannot be seen. Use the timed-region mean (not quantized) and msgs/s for small changes.

| | msgs/s | mean ns/msg | p50 | p99 | p99.9 | max |
|---|---:|---:|---:|---:|---:|---:|
| ram throughput pass, best of 3 | 5,160,402 | 193.8 (a) | | | | |
| ram latency pass, median run | | 205.0 (b) | 125 ns (3 ticks, ±33.3%) | 794 ns (19 ticks, ±5.3%) | 6,960 ns (167 ticks, ±0.6%) | 77,280,703 ns |
| stream, end to end incl. gunzip | 2,178,747 | 459.0 (c) | | | | |

- (a) throughput pass wall time / messages, no per-message timer: SegmentFilter (IEX-TP framing, sequence tracking) + decode + `BookSet::apply`.
- (b) timed-region mean: sum of per-message tick deltas / messages / ticks_per_ns, the mean of exactly what the percentiles measure (decode + apply plus one ticks() pair, whose empty mean is 9.4 ns). Not quantized: per-sample rounding averages out. It excludes SegmentFilter, so it is not comparable to (a).
- (c) stream wall time / messages, including inflate and pcapng parsing.
- Percentiles: whole ticks converted to ns; the band is ±1 tick relative to the value. Max is exact; a single sample of milliseconds is most likely the VM being descheduled or a page fault (not verified: no hardware counters), not decode + apply.
- Latency pass wall time / messages (median run): 283.9 ns. This is the cost of the instrumented loop (SegmentFilter, decode + apply, the timer pair, two histogram records and an llround per message), not a latency figure.

Run-to-run ram throughput: 5,089,575, 5,160,402, 4,616,286 msgs/s (spread 10.7% of the median).

| ram run | msgs/s | timed-region mean ns | p50 ticks | p99 ticks | p99.9 ticks | max ns |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 5,089,575 | 205.0 | 3 | 19 | 167 | 77,280,703 |
| 2 | 5,160,402 | 162.5 | 3 | 13 | 79 | 40,338,376 |
| 3 | 4,616,286 | 174.1 | 3 | 16 | 158 | 24,888,331 |

Profile: `docs/benchmarks/iex-book-flamegraph-c9e60e0.svg` (renamed in Task 6 from `iex-book-flamegraph.svg`; folded stacks `iex-book-profile-c9e60e0.folded`; throughput pass only, cpp/build-linux-prof = these flags plus `-g -fno-omit-frame-pointer`, one separate ram run under `perf record -F 999 -g`; perf: `perf version 6.8.12 task-clock:upppH`). Top self-time leaves:

```
 19.13%  pkmn::book::OrderBook::erase_order
 15.65%  pkmn::book::OrderBook::add
 11.48%  pkmn::md::decode_deep_plus
 11.28%  pkmn::md::parse_tp_header
  8.20%  std::__detail::_Map_base<unsigned long, std::pair<unsigned long const, pkmn::book::OrderBook>, std::allocator<std::pair<unsigned long const, pkmn::book::OrderBoo
  5.13%  pkmn::md::SegmentFilter::accept
  4.59%  bench::PassResult bench::replay<pkmn::md::CallStep>
  4.08%  _int_free
  3.02%  std::_Hashtable<unsigned long, std::pair<unsigned long const, pkmn::book::OrderBook::Loc>, std::allocator<std::pair<unsigned long const, pkmn::book::OrderBook::L
  2.49%  malloc
  1.92%  cfree@GLIBC_2.17
  1.85%  operator new
  1.45%  std::__detail::_Prime_rehash_policy::_M_need_rehash
  1.44%  operator delete
  1.35%  std::_Rb_tree_rebalance_for_erase
```

Top inclusive frames below the throughput pass's replay loop:

```
 29.93%  pkmn::book::OrderBook::remove
 26.85%  pkmn::book::OrderBook::erase_order
 25.01%  pkmn::book::OrderBook::add
 15.85%  pkmn::md::SegmentFilter::accept
 11.48%  pkmn::md::decode_deep_plus
 11.28%  pkmn::md::parse_tp_header
  8.65%  pkmn::md::BookSet::apply
  8.22%  std::__detail::_Map_base<unsigned long, std::pair<unsigned long const, pkmn::book::OrderBook>, std::allocator<std::pair<unsigned long const, pkmn::book::OrderBoo
  6.05%  cfree@GLIBC_2.17
  5.17%  std::_Hashtable<unsigned long, std::pair<unsigned long const, pkmn::book::OrderBook::Loc>, std::allocator<std::pair<unsigned long const, pkmn::book::OrderBook::L
  5.17%  operator new
  4.80%  pkmn::book::OrderBook::modify
  4.08%  _int_free
  3.01%  malloc
  2.28%  std::_Rb_tree_iterator<std::pair<long const, pkmn::book::OrderBook::LevelData> > std::_Rb_tree<long, std::pair<long const, pkmn::book::OrderBook::LevelData>, std
```

<details><summary>Raw JSON (ram runs 1-3, stream)</summary>

```json
{
  "host": "ubuntu Linux 7.2.9-orbstack-00391-g2b298d05fbd4 aarch64",
  "compiler": "g++ 13.3.0",
  "flags": "CMAKE_BUILD_TYPE=Release: -O3 -DNDEBUG -ffp-contract=off",
  "git_sha": "c9e60e0c2bf6",
  "tree_hash": null,
  "file": "/Users/ml3787/pkmn-stock/data/iex/20241223_IEXTP1_DPLS1.0.pcap.gz",
  "mode": "ram",
  "timer": "cntvct_el0 (isb before read)",
  "cntfrq_hz": 24000000,
  "ticks_per_ns": 0.024003237,
  "timer_resolution_ns": 41.667,
  "timer_overhead_ns": 9.422,
  "timer_overhead_ticks_p50": 0,
  "timer_overhead_ticks_max": 36881,
  "steady_clock_resolution_ns": 41,
  "ram_bytes": 4294967296,
  "held_bytes": 3445841433,
  "held_packets": 42492551,
  "ram_truncated": false,
  "load_seconds": 19.361,
  "segments": 42492551,
  "restarts": 1,
  "orders_at_end": 0,
  "unknown_id": 0,
  "messages": 50959176,
  "seconds": 10.012,
  "msgs_per_sec": 5089575,
  "batch_ns_per_msg": 196.48,
  "timed_region_mean_ns": 205.0,
  "latency_pass_wall_seconds": 14.47,
  "latency_pass_wall_ns_per_msg": 283.95,
  "p50_ticks": 3,
  "p99_ticks": 19,
  "p999_ticks": 167,
  "max_ticks": 1854987,
  "p50_ns": 125,
  "p99_ns": 794,
  "p999_ns": 6960,
  "max_ns": 77280703
}
{
  "host": "ubuntu Linux 7.2.9-orbstack-00391-g2b298d05fbd4 aarch64",
  "compiler": "g++ 13.3.0",
  "flags": "CMAKE_BUILD_TYPE=Release: -O3 -DNDEBUG -ffp-contract=off",
  "git_sha": "c9e60e0c2bf6",
  "tree_hash": null,
  "file": "/Users/ml3787/pkmn-stock/data/iex/20241223_IEXTP1_DPLS1.0.pcap.gz",
  "mode": "ram",
  "timer": "cntvct_el0 (isb before read)",
  "cntfrq_hz": 24000000,
  "ticks_per_ns": 0.024003322,
  "timer_resolution_ns": 41.667,
  "timer_overhead_ns": 10.73,
  "timer_overhead_ticks_p50": 0,
  "timer_overhead_ticks_max": 122647,
  "steady_clock_resolution_ns": 41,
  "ram_bytes": 4294967296,
  "held_bytes": 3445841433,
  "held_packets": 42492551,
  "ram_truncated": false,
  "load_seconds": 17.518,
  "segments": 42492551,
  "restarts": 1,
  "orders_at_end": 0,
  "unknown_id": 0,
  "messages": 50959176,
  "seconds": 9.875,
  "msgs_per_sec": 5160402,
  "batch_ns_per_msg": 193.78,
  "timed_region_mean_ns": 162.5,
  "latency_pass_wall_seconds": 10.612,
  "latency_pass_wall_ns_per_msg": 208.25,
  "p50_ticks": 3,
  "p99_ticks": 13,
  "p999_ticks": 79,
  "max_ticks": 968255,
  "p50_ns": 125,
  "p99_ns": 542,
  "p999_ns": 3288,
  "max_ns": 40338376
}
{
  "host": "ubuntu Linux 7.2.9-orbstack-00391-g2b298d05fbd4 aarch64",
  "compiler": "g++ 13.3.0",
  "flags": "CMAKE_BUILD_TYPE=Release: -O3 -DNDEBUG -ffp-contract=off",
  "git_sha": "c9e60e0c2bf6",
  "tree_hash": null,
  "file": "/Users/ml3787/pkmn-stock/data/iex/20241223_IEXTP1_DPLS1.0.pcap.gz",
  "mode": "ram",
  "timer": "cntvct_el0 (isb before read)",
  "cntfrq_hz": 24000000,
  "ticks_per_ns": 0.024001529,
  "timer_resolution_ns": 41.667,
  "timer_overhead_ns": 9.148,
  "timer_overhead_ticks_p50": 0,
  "timer_overhead_ticks_max": 2633,
  "steady_clock_resolution_ns": 41,
  "ram_bytes": 4294967296,
  "held_bytes": 3445841433,
  "held_packets": 42492551,
  "ram_truncated": false,
  "load_seconds": 17.098,
  "segments": 42492551,
  "restarts": 1,
  "orders_at_end": 0,
  "unknown_id": 0,
  "messages": 50959176,
  "seconds": 11.039,
  "msgs_per_sec": 4616286,
  "batch_ns_per_msg": 216.62,
  "timed_region_mean_ns": 174.1,
  "latency_pass_wall_seconds": 12.298,
  "latency_pass_wall_ns_per_msg": 241.33,
  "p50_ticks": 3,
  "p99_ticks": 16,
  "p999_ticks": 158,
  "max_ticks": 597358,
  "p50_ns": 125,
  "p99_ns": 666,
  "p999_ns": 6576,
  "max_ns": 24888331
}
{
  "host": "ubuntu Linux 7.2.9-orbstack-00391-g2b298d05fbd4 aarch64",
  "compiler": "g++ 13.3.0",
  "flags": "CMAKE_BUILD_TYPE=Release: -O3 -DNDEBUG -ffp-contract=off",
  "git_sha": "c9e60e0c2bf6",
  "tree_hash": null,
  "file": "/Users/ml3787/pkmn-stock/data/iex/20241223_IEXTP1_DPLS1.0.pcap.gz",
  "mode": "stream",
  "timer": "cntvct_el0 (isb before read)",
  "cntfrq_hz": 24000000,
  "ticks_per_ns": 0.024001365,
  "timer_resolution_ns": 41.667,
  "timer_overhead_ns": 9.731,
  "timer_overhead_ticks_p50": 0,
  "timer_overhead_ticks_max": 48293,
  "steady_clock_resolution_ns": 41,
  "packets": 42492551,
  "segments": 42492551,
  "restarts": 1,
  "orders_at_end": 0,
  "messages": 50959176,
  "seconds": 23.389,
  "msgs_per_sec": 2178747,
  "p50_ns": null,
  "p99_ns": null,
  "p999_ns": null,
  "max_ns": null
}
```

</details>

