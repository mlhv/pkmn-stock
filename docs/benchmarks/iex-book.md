# IEX book benchmarks

Results of `scripts/bench_iex.sh` (DEEP+ decode + `BookSet::apply`, `cpp/tools/iex_bench.cpp`), newest first.

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

Profile: `docs/benchmarks/iex-book-flamegraph.svg` (throughput pass only, cpp/build-linux-prof = these flags plus `-g -fno-omit-frame-pointer`, one separate ram run under `perf record -F 999 -g`; perf: `perf version 6.8.12 task-clock:upppH`). Top self-time leaves:

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

