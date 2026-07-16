#include <catch2/catch_test_macros.hpp>

#include <cmath>
#include <limits>
#include <stdexcept>

#include "pkmn_engine/market.hpp"

using pkmn::AssetId;
using pkmn::Day;
using pkmn::MarketView;
using pkmn::MarkEvent;
using pkmn::PriceRow;

namespace {
constexpr double kNaN = std::numeric_limits<double>::quiet_NaN();

MarketView make_view() {
    // asset 0 prints on days 100,101,103; asset 1 on 100,103 (gap on 101).
    // day 102 is a trading day with no prints at all.
    std::vector<Day> days{100, 101, 102, 103};
    std::vector<PriceRow> rows{
        {100, 0, 10.0, 11.0, 9.0},
        {100, 1, 50.0, kNaN, 45.0},
        {101, 0, 12.0, 14.0, 11.0},
        {103, 0, 8.0, 9.0, 7.0},
        {103, 1, 60.0, 66.0, kNaN},
    };
    std::vector<MarkEvent> events{
        {100, 0, 10.0}, {100, 1, 50.0}, {101, 0, 12.0}, {103, 0, 8.0}, {103, 1, 60.0}};
    return MarketView(2, days, rows, events);
}
}  // namespace

TEST_CASE("load_day exposes prints without carry-forward (data.py prices_on)") {
    auto mkt = make_view();
    mkt.load_day(101);
    CHECK(mkt.price(0) == 12.0);
    CHECK(std::isnan(mkt.price(1)));  // gap day for asset 1: no stale fill price
    CHECK(mkt.mid(0) == 14.0);
    mkt.load_day(102);
    CHECK(std::isnan(mkt.price(0)));
    mkt.load_day(103);
    CHECK(mkt.price(1) == 60.0);
    CHECK(std::isnan(mkt.low(1)));  // null low stays missing
}

TEST_CASE("marks cursor carries forward and preserves event insertion order") {
    auto mkt = make_view();
    const auto& m1 = mkt.marks_until(101);
    CHECK(*m1.find(0) == 12.0);
    CHECK(*m1.find(1) == 50.0);  // carried from day 100
    // insertion order: asset 0 entered first (event order)
    CHECK(m1.entries()[0].key == 0);
    const auto& m2 = mkt.marks_until(103);
    CHECK(*m2.find(1) == 60.0);
    CHECK_THROWS_AS(mkt.marks_until(100), std::logic_error);  // monotone only
    mkt.reset();
    const auto& m3 = mkt.marks_until(100);
    CHECK(*m3.find(0) == 10.0);
    CHECK(m3.size() == 2);
}

TEST_CASE("history queries: last-at-or-before, prefix peak, window max") {
    auto mkt = make_view();
    CHECK(mkt.last_price_at_or_before(0, 99) == std::nullopt);
    CHECK(*mkt.last_price_at_or_before(0, 100) == 10.0);
    CHECK(*mkt.last_price_at_or_before(0, 102) == 12.0);  // carry across gap
    CHECK(*mkt.last_price_at_or_before(1, 102) == 50.0);
    CHECK(*mkt.peak_until(0, 103) == 12.0);
    CHECK(*mkt.peak_until(0, 100) == 10.0);
    CHECK(*mkt.max_in_window(0, 101, 103) == 12.0);
    CHECK(*mkt.max_in_window(0, 102, 103) == 8.0);
    CHECK(mkt.max_in_window(0, 104, 110) == std::nullopt);
}

TEST_CASE("constructor validates sortedness and asset range") {
    std::vector<Day> days{100, 100};  // not strictly increasing
    CHECK_THROWS_AS(MarketView(1, days, {}, {}), std::invalid_argument);
    std::vector<Day> ok{100};
    std::vector<PriceRow> bad_rows{{101, 0, 1.0, kNaN, kNaN}, {100, 0, 1.0, kNaN, kNaN}};
    CHECK_THROWS_AS(MarketView(1, ok, bad_rows, {}), std::invalid_argument);
    std::vector<PriceRow> bad_asset{{100, 5, 1.0, kNaN, kNaN}};
    CHECK_THROWS_AS(MarketView(1, ok, bad_asset, {}), std::invalid_argument);
}
