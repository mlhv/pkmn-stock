## 2026-10-23 — full.pkw

- Hardware: Apple M1 Pro, 7-vCPU OrbStack Ubuntu 24.04 arm64 VM
- Host: `ubuntu`
- git: `5f5ba34581cc84a6f696ee48b01faad54d21a842` (dirty)
- Compiler: `c++ (Ubuntu 13.3.0-6ubuntu2~24.04) 13.3.0`
- Flags: `-O3 -DNDEBUG -std=gnu++20 -ffp-contract=off`
- File: 890,723,220 bytes, 38,250,316 messages, 46,076 instruments, 949 days, 38,202,342 quotes, 38,202,342 ladders built, 1,261,666,066 resting orders booked per ladder pass
- Method: 3 interleaved pairs (ladders on, then off), one pass each; each pass timed by the tool's counter around the whole pass, file reads included.

| mode | msgs/s best | msgs/s median | spread | seconds (runs 1-3) |
|---|---:|---:|---:|---|
| ladders (decode + build each day's ladders) | 1,488,661 | 1,472,031 | 8.9% | 25.694, 28.169, 25.985 |
| no ladders (decode only) | 97,221,748 | 61,585,370 | 70.6% | 0.621, 0.712, 0.393 |

<details><summary>Raw JSON (on 1-3, off 1-3)</summary>

```json
{
  "file": "data/wire/full.pkw",
  "ladders": true,
  "repeat": 1,
  "messages": 38250316,
  "instruments": 46076,
  "days": 949,
  "quotes": 38202342,
  "null_market_quotes": 0,
  "ladders_built": 38202342,
  "resting_orders_booked": 1261666066,
  "seconds": 25.694447,
  "msgs_per_s": 1488661,
  "pass_seconds": [
    25.694447
  ],
  "timer_ticks_per_ns": 0.024,
  "stats": {
    "packets": 638077,
    "messages": 38250316,
    "unknown": 0,
    "short_messages": 0,
    "short_packets": 0,
    "gaps": 0,
    "gap_messages": 0,
    "duplicates": 0,
    "bad_magic": 0
  }
}
{
  "file": "data/wire/full.pkw",
  "ladders": true,
  "repeat": 1,
  "messages": 38250316,
  "instruments": 46076,
  "days": 949,
  "quotes": 38202342,
  "null_market_quotes": 0,
  "ladders_built": 38202342,
  "resting_orders_booked": 1261666066,
  "seconds": 28.16911,
  "msgs_per_s": 1357882,
  "pass_seconds": [
    28.16911
  ],
  "timer_ticks_per_ns": 0.024,
  "stats": {
    "packets": 638077,
    "messages": 38250316,
    "unknown": 0,
    "short_messages": 0,
    "short_packets": 0,
    "gaps": 0,
    "gap_messages": 0,
    "duplicates": 0,
    "bad_magic": 0
  }
}
{
  "file": "data/wire/full.pkw",
  "ladders": true,
  "repeat": 1,
  "messages": 38250316,
  "instruments": 46076,
  "days": 949,
  "quotes": 38202342,
  "null_market_quotes": 0,
  "ladders_built": 38202342,
  "resting_orders_booked": 1261666066,
  "seconds": 25.984724,
  "msgs_per_s": 1472031,
  "pass_seconds": [
    25.984724
  ],
  "timer_ticks_per_ns": 0.024,
  "stats": {
    "packets": 638077,
    "messages": 38250316,
    "unknown": 0,
    "short_messages": 0,
    "short_packets": 0,
    "gaps": 0,
    "gap_messages": 0,
    "duplicates": 0,
    "bad_magic": 0
  }
}
{
  "file": "data/wire/full.pkw",
  "ladders": false,
  "repeat": 1,
  "messages": 38250316,
  "instruments": 46076,
  "days": 949,
  "quotes": 38202342,
  "null_market_quotes": 0,
  "ladders_built": 0,
  "resting_orders_booked": 0,
  "seconds": 0.621094,
  "msgs_per_s": 61585370,
  "pass_seconds": [
    0.621094
  ],
  "timer_ticks_per_ns": 0.024001,
  "stats": {
    "packets": 638077,
    "messages": 38250316,
    "unknown": 0,
    "short_messages": 0,
    "short_packets": 0,
    "gaps": 0,
    "gap_messages": 0,
    "duplicates": 0,
    "bad_magic": 0
  }
}
{
  "file": "data/wire/full.pkw",
  "ladders": false,
  "repeat": 1,
  "messages": 38250316,
  "instruments": 46076,
  "days": 949,
  "quotes": 38202342,
  "null_market_quotes": 0,
  "ladders_built": 0,
  "resting_orders_booked": 0,
  "seconds": 0.71154,
  "msgs_per_s": 53757114,
  "pass_seconds": [
    0.71154
  ],
  "timer_ticks_per_ns": 0.024004,
  "stats": {
    "packets": 638077,
    "messages": 38250316,
    "unknown": 0,
    "short_messages": 0,
    "short_packets": 0,
    "gaps": 0,
    "gap_messages": 0,
    "duplicates": 0,
    "bad_magic": 0
  }
}
{
  "file": "data/wire/full.pkw",
  "ladders": false,
  "repeat": 1,
  "messages": 38250316,
  "instruments": 46076,
  "days": 949,
  "quotes": 38202342,
  "null_market_quotes": 0,
  "ladders_built": 0,
  "resting_orders_booked": 0,
  "seconds": 0.393434,
  "msgs_per_s": 97221748,
  "pass_seconds": [
    0.393434
  ],
  "timer_ticks_per_ns": 0.024001,
  "stats": {
    "packets": 638077,
    "messages": 38250316,
    "unknown": 0,
    "short_messages": 0,
    "short_packets": 0,
    "gaps": 0,
    "gap_messages": 0,
    "duplicates": 0,
    "bad_magic": 0
  }
}
```

</details>

