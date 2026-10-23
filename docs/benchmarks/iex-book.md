# IEX book benchmarks

Results of `scripts/bench_iex.sh` (DEEP+ decode + `BookSet::apply`, `cpp/tools/iex_bench.cpp`), newest first.

## 2026-10-22 — 20241223_IEXTP1_DPLS1.0.pcap.gz

- Hardware: Apple M1 Pro, 7-vCPU OrbStack Ubuntu 24.04 arm64 VM
- Host: `ubuntu Linux 7.2.9-orbstack-00391-g2b298d05fbd4 aarch64`
- git: `b8af3bf5b443-dirty` (the working tree on top of b8af3bf: the measured `cpp/` and `scripts/` are exactly those of the commit that adds this section)
- Compiler: `g++ 13.3.0`
- Flags: `CMAKE_BUILD_TYPE=Release: -O3 -DNDEBUG -ffp-contract=off`
- Timer: cntvct_el0 (isb before read), cntfrq 24,000,000 Hz, calibrated 0.024000 ticks/ns, resolution 41.7 ns per tick; empty ticks() pair 9.0 ns mean (p50 0 ticks); steady_clock resolution 41 ns
- ram held 3,445,841,433 payload bytes in 42,492,551 packets (cap 4,294,967,296, truncated: false); 50,959,176 messages, 1 restart(s)

| | msgs/s | ns/msg (batch) | p50 | p99 | p99.9 | max |
|---|---:|---:|---:|---:|---:|---:|
| ram, best of 3 (throughput pass) | 5,518,907 | 181.2 | | | | |
| ram, median run (latency pass, per message) | | 208.8 | 125 ns (3 ticks) | 542 ns (13 ticks) | 4088 ns (98 ticks) | 10,120,684 ns |
| stream, end to end incl. gunzip | 2,142,376 | 466.8 | | | | |

Run-to-run ram throughput: 5,492,303, 5,518,907, 5,189,047 msgs/s (spread 6.0% of the median).

| ram run | msgs/s | p50 ticks | p99 ticks | p99.9 ticks | max ns |
|---|---:|---:|---:|---:|---:|
| 1 | 5,492,303 | 3 | 13 | 98 | 10,120,684 |
| 2 | 5,518,907 | 3 | 12 | 69 | 24,042,863 |
| 3 | 5,189,047 | 3 | 13 | 124 | 6,104,510 |

Percentiles are whole ticks of the 41.7 ns counter converted to ns, and every sample includes one ticks() pair's overhead (the empty-pair mean above). The latency pass's batch ns/msg exceeds the throughput pass's by that overhead plus the cost of the isb barriers. Never quote the batch mean alone.

Notes on this run (hand-written, not generated):

- p50 is 3 ticks: just above the 2-tick quantization floor, so it means "about 85-165 ns", not 125 ns exactly. p99 and p99.9 sit well clear of the floor.
- max is 6-24 ms across runs (past the histogram's 10 ms design range; max is kept exactly). Single samples that long are the VM being descheduled or a page fault, not decode + apply; p99.9 also moves run to run (69-124 ticks), so treat the tail as noisy on this VM.
- ram throughput includes IEX-TP framing and sequence tracking (SegmentFilter) as well as decode + apply; the profile below shows how much (SegmentFilter::accept ~16% inclusive).
- Profile: `perf record -F 999 -g` works on the OrbStack kernel only with the software `task-clock` event (perf falls back from `cycles`: the VM exposes no hardware counters), using Ubuntu's 6.8 perf binary run directly (there is no linux-tools package for 7.2.9-orbstack). One separate ram run of the frame-pointer build under perf; stacks filtered to the throughput pass. Without counters it cannot say *why* a frame is hot: `parse_tp_header`'s ~11% self time is plausibly the first touch of each packet's bytes in the 3.4 GB held buffer (cache misses), but that is a hypothesis, not a measurement.

Profile: `docs/benchmarks/iex-book-flamegraph.svg` (throughput pass only, cpp/build-linux-prof = these flags plus `-g -fno-omit-frame-pointer`). Top self-time leaves:

```
 18.17%  pkmn::book::OrderBook::erase_order
 15.63%  pkmn::book::OrderBook::add
 11.49%  pkmn::md::decode_deep_plus
 10.93%  pkmn::md::parse_tp_header
  8.05%  std::__detail::_Map_base<unsigned long, std::pair<unsigned long const, pkmn::book::OrderBook>, std::allocator<std::pair<unsigned long const, pkmn::book::OrderBoo
  5.67%  pkmn::md::SegmentFilter::accept
  4.45%  bench::PassResult bench::replay<pkmn::md::CallStep>
  4.31%  _int_free
  3.33%  std::_Hashtable<unsigned long, std::pair<unsigned long const, pkmn::book::OrderBook::Loc>, std::allocator<std::pair<unsigned long const, pkmn::book::OrderBook::L
  2.39%  malloc
  2.25%  operator new
  2.08%  cfree@GLIBC_2.17
  1.59%  operator delete
  1.43%  std::__detail::_Prime_rehash_policy::_M_need_rehash
  1.36%  std::_Rb_tree_rebalance_for_erase
```

Top inclusive frames below the throughput pass's replay loop:

```
 29.61%  pkmn::book::OrderBook::remove
 25.97%  pkmn::book::OrderBook::erase_order
 25.31%  pkmn::book::OrderBook::add
 15.97%  pkmn::md::SegmentFilter::accept
 11.49%  pkmn::md::decode_deep_plus
 10.93%  pkmn::md::parse_tp_header
  8.61%  pkmn::md::BookSet::apply
  8.08%  std::__detail::_Map_base<unsigned long, std::pair<unsigned long const, pkmn::book::OrderBook>, std::allocator<std::pair<unsigned long const, pkmn::book::OrderBoo
  6.42%  cfree@GLIBC_2.17
  6.10%  std::_Hashtable<unsigned long, std::pair<unsigned long const, pkmn::book::OrderBook::Loc>, std::allocator<std::pair<unsigned long const, pkmn::book::OrderBook::L
  5.36%  operator new
  5.05%  pkmn::book::OrderBook::modify
  4.31%  _int_free
  2.91%  malloc
  2.53%  std::_Rb_tree_iterator<std::pair<long const, pkmn::book::OrderBook::LevelData> > std::_Rb_tree<long, std::pair<long const, pkmn::book::OrderBook::LevelData>, std
```

<details><summary>Raw JSON (ram runs 1-3, stream)</summary>

```json
{
  "host": "ubuntu Linux 7.2.9-orbstack-00391-g2b298d05fbd4 aarch64",
  "compiler": "g++ 13.3.0",
  "flags": "CMAKE_BUILD_TYPE=Release: -O3 -DNDEBUG -ffp-contract=off",
  "git_sha": "b8af3bf5b443-dirty",
  "file": "/Users/ml3787/pkmn-stock/data/iex/20241223_IEXTP1_DPLS1.0.pcap.gz",
  "mode": "ram",
  "timer": "cntvct_el0 (isb before read)",
  "cntfrq_hz": 24000000,
  "ticks_per_ns": 0.024000354,
  "timer_resolution_ns": 41.667,
  "timer_overhead_ns": 8.99,
  "timer_overhead_ticks_p50": 0,
  "timer_overhead_ticks_max": 1385,
  "steady_clock_resolution_ns": 41,
  "ram_bytes": 4294967296,
  "held_bytes": 3445841433,
  "held_packets": 42492551,
  "ram_truncated": false,
  "load_seconds": 18.552,
  "segments": 42492551,
  "restarts": 1,
  "orders_at_end": 0,
  "unknown_id": 0,
  "messages": 50959176,
  "seconds": 9.278,
  "msgs_per_sec": 5492303,
  "batch_ns_per_msg": 182.07,
  "timed_pass_seconds": 10.638,
  "timed_pass_ns_per_msg": 208.76,
  "p50_ticks": 3,
  "p99_ticks": 13,
  "p999_ticks": 98,
  "max_ticks": 242900,
  "p50_ns": 125,
  "p99_ns": 542,
  "p999_ns": 4088,
  "max_ns": 10120684
}
{
  "host": "ubuntu Linux 7.2.9-orbstack-00391-g2b298d05fbd4 aarch64",
  "compiler": "g++ 13.3.0",
  "flags": "CMAKE_BUILD_TYPE=Release: -O3 -DNDEBUG -ffp-contract=off",
  "git_sha": "b8af3bf5b443-dirty",
  "file": "/Users/ml3787/pkmn-stock/data/iex/20241223_IEXTP1_DPLS1.0.pcap.gz",
  "mode": "ram",
  "timer": "cntvct_el0 (isb before read)",
  "cntfrq_hz": 24000000,
  "ticks_per_ns": 0.024000219,
  "timer_resolution_ns": 41.667,
  "timer_overhead_ns": 8.885,
  "timer_overhead_ticks_p50": 0,
  "timer_overhead_ticks_max": 1944,
  "steady_clock_resolution_ns": 41,
  "ram_bytes": 4294967296,
  "held_bytes": 3445841433,
  "held_packets": 42492551,
  "ram_truncated": false,
  "load_seconds": 16.965,
  "segments": 42492551,
  "restarts": 1,
  "orders_at_end": 0,
  "unknown_id": 0,
  "messages": 50959176,
  "seconds": 9.234,
  "msgs_per_sec": 5518907,
  "batch_ns_per_msg": 181.2,
  "timed_pass_seconds": 9.918,
  "timed_pass_ns_per_msg": 194.63,
  "p50_ticks": 3,
  "p99_ticks": 12,
  "p999_ticks": 69,
  "max_ticks": 577034,
  "p50_ns": 125,
  "p99_ns": 501,
  "p999_ns": 2872,
  "max_ns": 24042863
}
{
  "host": "ubuntu Linux 7.2.9-orbstack-00391-g2b298d05fbd4 aarch64",
  "compiler": "g++ 13.3.0",
  "flags": "CMAKE_BUILD_TYPE=Release: -O3 -DNDEBUG -ffp-contract=off",
  "git_sha": "b8af3bf5b443-dirty",
  "file": "/Users/ml3787/pkmn-stock/data/iex/20241223_IEXTP1_DPLS1.0.pcap.gz",
  "mode": "ram",
  "timer": "cntvct_el0 (isb before read)",
  "cntfrq_hz": 24000000,
  "ticks_per_ns": 0.024000124,
  "timer_resolution_ns": 41.667,
  "timer_overhead_ns": 8.787,
  "timer_overhead_ticks_p50": 0,
  "timer_overhead_ticks_max": 794,
  "steady_clock_resolution_ns": 41,
  "ram_bytes": 4294967296,
  "held_bytes": 3445841433,
  "held_packets": 42492551,
  "ram_truncated": false,
  "load_seconds": 16.798,
  "segments": 42492551,
  "restarts": 1,
  "orders_at_end": 0,
  "unknown_id": 0,
  "messages": 50959176,
  "seconds": 9.821,
  "msgs_per_sec": 5189047,
  "batch_ns_per_msg": 192.71,
  "timed_pass_seconds": 10.923,
  "timed_pass_ns_per_msg": 214.34,
  "p50_ticks": 3,
  "p99_ticks": 13,
  "p999_ticks": 124,
  "max_ticks": 146509,
  "p50_ns": 125,
  "p99_ns": 542,
  "p999_ns": 5168,
  "max_ns": 6104510
}
{
  "host": "ubuntu Linux 7.2.9-orbstack-00391-g2b298d05fbd4 aarch64",
  "compiler": "g++ 13.3.0",
  "flags": "CMAKE_BUILD_TYPE=Release: -O3 -DNDEBUG -ffp-contract=off",
  "git_sha": "b8af3bf5b443-dirty",
  "file": "/Users/ml3787/pkmn-stock/data/iex/20241223_IEXTP1_DPLS1.0.pcap.gz",
  "mode": "stream",
  "timer": "cntvct_el0 (isb before read)",
  "cntfrq_hz": 24000000,
  "ticks_per_ns": 0.024000289,
  "timer_resolution_ns": 41.667,
  "timer_overhead_ns": 9.053,
  "timer_overhead_ticks_p50": 0,
  "timer_overhead_ticks_max": 1785,
  "steady_clock_resolution_ns": 41,
  "packets": 42492551,
  "segments": 42492551,
  "restarts": 1,
  "orders_at_end": 0,
  "messages": 50959176,
  "seconds": 23.786,
  "msgs_per_sec": 2142376,
  "p50_ns": null,
  "p99_ns": null,
  "p999_ns": null,
  "max_ns": null
}
```

</details>

