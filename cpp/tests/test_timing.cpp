// timing.hpp: the cycle-counter timer and the log-linear latency histogram
// iex_bench records into.

#include <catch2/catch_test_macros.hpp>

#include <chrono>
#include <cmath>
#include <cstdint>

#include "pkmn_md/timing.hpp"

using namespace pkmn::md;

namespace {

bool within_pct(std::uint64_t got, double want, double pct) {
    return std::fabs(static_cast<double>(got) - want) <= want * pct / 100.0;
}

}  // namespace

TEST_CASE("histogram percentiles within 1%") {
    Histogram h;
    for (std::uint64_t ns = 1; ns <= 100000; ++ns) h.record(ns);
    CHECK(h.count() == 100000);
    CHECK(h.max() == 100000);
    CHECK(within_pct(h.percentile(50), 50000, 1.0));
    CHECK(within_pct(h.percentile(99), 99000, 1.0));
    CHECK(within_pct(h.percentile(99.9), 99900, 1.0));
    CHECK(h.percentile(100) == 100000);
}

TEST_CASE("histogram is exact for small values and bounded across 1 ns .. 10 ms") {
    Histogram h;
    for (int i = 0; i < 10; ++i) h.record(42);
    CHECK(h.percentile(0) == 42);
    CHECK(h.percentile(50) == 42);
    CHECK(h.percentile(100) == 42);

    // Every value from 1 ns to 10 ms, one at a time: the single recorded
    // value comes back within 1%.
    for (std::uint64_t v = 1; v <= 10'000'000; v = v * 3 / 2 + 1) {
        Histogram one;
        one.record(v);
        INFO("v = " << v);
        CHECK(within_pct(one.percentile(50), static_cast<double>(v), 1.0));
    }
}

TEST_CASE("histogram keeps the exact max beyond its range") {
    Histogram h;
    h.record(5);
    h.record(1'000'000'000);  // 1 s: past the 10 ms bucket range
    CHECK(h.max() == 1'000'000'000);
    CHECK(h.percentile(100) == 1'000'000'000);
    CHECK(h.percentile(50) == 5);
    CHECK(h.count() == 2);
}

TEST_CASE("empty histogram reports zeros") {
    Histogram h;
    CHECK(h.count() == 0);
    CHECK(h.max() == 0);
    CHECK(h.percentile(50) == 0);
}

TEST_CASE("ticks monotone and calibrated") {
    std::uint64_t prev = ticks();
    for (int i = 0; i < 100000; ++i) {
        const std::uint64_t t = ticks();
        REQUIRE(t >= prev);
        prev = t;
    }

    // Two reads 10 ms apart. Spin on steady_clock rather than sleep: a VM may
    // oversleep a 10 ms sleep by more than the 2 ms tolerance, which would
    // test the scheduler, not the timer.
    using clk = std::chrono::steady_clock;
    const double tpn = ticks_per_ns();
    REQUIRE(tpn > 0);
    const std::uint64_t t0 = ticks();
    const auto s0 = clk::now();
    while (clk::now() - s0 < std::chrono::milliseconds(10)) {
    }
    const std::uint64_t t1 = ticks();
    const auto s1 = clk::now();
    const double ticks_ms = static_cast<double>(t1 - t0) / tpn / 1e6;
    const double steady_ms = std::chrono::duration<double, std::milli>(s1 - s0).count();
    INFO("ticks-derived " << ticks_ms << " ms, steady-derived " << steady_ms << " ms");
    CHECK(std::fabs(ticks_ms - 10.0) <= 2.0);
    CHECK(std::fabs(ticks_ms - steady_ms) <= 2.0);
}

TEST_CASE("calibration agrees with the counter's advertised frequency") {
    // ticks_per_ns() is measured against steady_clock; timer_frequency_hz()
    // is what the hardware says (cntfrq_el0 on arm64, 1e9 for the
    // steady_clock fallback). They are independent, so agreement within 1%
    // checks the calibration rather than restating it.
    const double advertised = static_cast<double>(timer_frequency_hz()) / 1e9;
    REQUIRE(advertised > 0);
    CHECK(std::fabs(ticks_per_ns() - advertised) <= advertised * 0.01);
}
