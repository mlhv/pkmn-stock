#pragma once

// Cycle-counter timer and a log-linear latency histogram, for iex_bench.
//
// ticks(): on arm64 the generic timer's virtual count (cntvct_el0), read
// after an isb so the read cannot be hoisted above earlier instructions.
// Its rate is cntfrq_el0 (timer_frequency_hz()); on Apple silicon that is
// 24 MHz, i.e. one tick is ~41.7 ns, so a single short interval is quantized
// to whole ticks and anything under ~2 ticks says little on its own. Other
// architectures fall back to steady_clock in ns (frequency 1e9).
//
// ticks_per_ns(): measured once (first call, >= 200 ms) against
// steady_clock, not taken from cntfrq, so a mis-advertised counter shows up
// as disagreement between the two (the test checks they agree within 1%).
//
// Histogram: values 0..255 ns are exact; above that each power-of-two octave
// is split into 128 equal buckets, so a bucket is at most 1/128 (0.78%) of
// its lower bound wide and percentile() (the bucket midpoint) is within
// 0.4% of any value in it. Buckets cover up to 2^24 ns (~16.8 ms, past the
// 10 ms design range); larger values land in one overflow bucket, and max()
// is always exact. Fixed size (2,305 counters), record() never allocates.

#include <algorithm>
#include <array>
#include <bit>
#include <chrono>
#include <cmath>
#include <cstdint>

namespace pkmn::md {

inline std::uint64_t ticks() {
#if defined(__aarch64__)
    std::uint64_t v;
    asm volatile("isb\n\tmrs %0, cntvct_el0" : "=r"(v)::"memory");
    return v;
#else
    return static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch())
            .count());
#endif
}

// The counter's advertised rate in Hz (cntfrq_el0 on arm64; 1e9 otherwise).
inline std::uint64_t timer_frequency_hz() {
#if defined(__aarch64__)
    std::uint64_t f;
    asm volatile("mrs %0, cntfrq_el0" : "=r"(f));
    return f;
#else
    return 1'000'000'000ULL;
#endif
}

inline double ticks_per_ns() {
    static const double tpn = [] {
        using clk = std::chrono::steady_clock;
        const std::uint64_t t0 = ticks();
        const auto s0 = clk::now();
        // Spin, not sleep: the window is what is measured, so oversleeping
        // would be harmless, but spinning keeps the two clocks' reads paired.
        while (clk::now() - s0 < std::chrono::milliseconds(200)) {
        }
        const std::uint64_t t1 = ticks();
        const auto s1 = clk::now();
        const double ns = static_cast<double>(std::chrono::duration_cast<std::chrono::nanoseconds>(s1 - s0).count());
        return static_cast<double>(t1 - t0) / ns;
    }();
    return tpn;
}

class Histogram {
  public:
    static constexpr int kSubBits = 7;                       // 128 buckets per octave
    static constexpr std::uint64_t kExact = 2ULL << kSubBits;  // 0..255 exact
    static constexpr int kTopExp = 24;                       // log buckets up to 2^24 ns
    static constexpr std::size_t kLogBuckets = static_cast<std::size_t>(kTopExp - (kSubBits + 1)) << kSubBits;
    static constexpr std::size_t kBuckets = kExact + kLogBuckets + 1;  // + overflow

    void record(std::uint64_t ns) {
        ++counts_[index(ns)];
        ++count_;
        if (ns > max_) max_ = ns;
    }

    // Smallest bucket value v such that at least p% of recorded values are
    // <= v (rank ceil(p/100 * count), at least 1), reported as the bucket's
    // midpoint and clamped to max(). 0 if nothing was recorded.
    std::uint64_t percentile(double p) const {
        if (count_ == 0) return 0;
        p = std::clamp(p, 0.0, 100.0);
        std::uint64_t rank = static_cast<std::uint64_t>(std::ceil(p / 100.0 * static_cast<double>(count_)));
        rank = std::clamp<std::uint64_t>(rank, 1, count_);
        std::uint64_t seen = 0;
        for (std::size_t i = 0; i < kBuckets; ++i) {
            seen += counts_[i];
            if (seen >= rank) return std::min(midpoint(i), max_);
        }
        return max_;
    }

    std::uint64_t max() const { return max_; }
    std::uint64_t count() const { return count_; }

  private:
    static std::size_t index(std::uint64_t v) {
        if (v < kExact) return static_cast<std::size_t>(v);
        const int e = 63 - std::countl_zero(v);  // >= kSubBits + 1
        if (e >= kTopExp) return kBuckets - 1;
        const std::uint64_t sub = (v >> (e - kSubBits)) & ((1ULL << kSubBits) - 1);
        return static_cast<std::size_t>(kExact + (static_cast<std::uint64_t>(e - (kSubBits + 1)) << kSubBits) + sub);
    }
    static std::uint64_t midpoint(std::size_t i) {
        if (i < kExact) return i;
        if (i == kBuckets - 1) return ~0ULL;  // overflow: clamped to max_ by the caller
        const std::size_t j = i - kExact;
        const int e = static_cast<int>(j >> kSubBits) + kSubBits + 1;
        const std::uint64_t sub = j & ((1ULL << kSubBits) - 1);
        const std::uint64_t width = 1ULL << (e - kSubBits);
        const std::uint64_t lower = ((1ULL << kSubBits) + sub) * width;
        return lower + width / 2;
    }

    std::array<std::uint64_t, kBuckets> counts_{};
    std::uint64_t count_ = 0, max_ = 0;
};

}  // namespace pkmn::md
