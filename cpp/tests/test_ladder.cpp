#include <catch2/catch_test_macros.hpp>
#include <cmath>
#include <limits>
#include <vector>

#include "pkmn_engine/ladder.hpp"

using namespace pkmn;
using book::Side;

namespace {
const double NaN = std::numeric_limits<double>::quiet_NaN();
CostModel make_cm() {
    CostModel cm;
    cm.impact_enabled = true;
    return cm;
}
std::vector<book::Price> ticks(const LadderShape& s, Side side, int n, LadderPricing p = LadderPricing::Cent) {
    std::vector<book::Price> v;
    for (int k = 0; k < n; ++k) v.push_back(unit_ticks(s, side, k, p));
    return v;
}
}  // namespace

TEST_CASE("ladder: cent asks on a 12/13 quote") {
    CostModel cm = make_cm();
    LadderShape s = ask_shape(DayQuote{12.0, 13.0, 1.0}, cm);
    CHECK(s.base == 12.0);
    CHECK(s.depth == 8);
    CHECK_FALSE(s.flat);
    CHECK(ticks(s, Side::Sell, 8) ==
          std::vector<book::Price>{1206, 1219, 1231, 1244, 1256, 1269, 1281, 1294});
    CHECK(unit_ticks(s, Side::Sell, 8, LadderPricing::Cent) == 1306);
}

TEST_CASE("ladder: cent bids on a 12/low 11 quote") {
    CostModel cm = make_cm();
    LadderShape s = bid_shape(DayQuote{12.0, NaN, 11.0}, cm);
    CHECK(s.depth == 8);
    CHECK(ticks(s, Side::Buy, 8) ==
          std::vector<book::Price>{1194, 1181, 1169, 1156, 1144, 1131, 1119, 1106});
}

TEST_CASE("ladder: floor lifts the ask base") {
    CostModel cm = make_cm();
    LadderShape s = ask_shape(DayQuote{12.0, 24.0, 20.0}, cm);
    CHECK(s.base == 20.0);
    CHECK(s.depth == 8);
}

TEST_CASE("ladder: crossed and missing quotes build flat books") {
    CostModel cm = make_cm();
    for (auto q : {DayQuote{12, 11, 1}, DayQuote{12, NaN, 1}}) {
        LadderShape s = ask_shape(q, cm);
        CHECK(s.flat);
        CHECK(s.depth == 8);
        for (int k = 0; k < 8; ++k) CHECK(unit_ticks(s, Side::Sell, k, LadderPricing::Cent) == 1200);
    }
    for (auto q : {DayQuote{12, 13, NaN}, DayQuote{12, 13, 12}}) {
        LadderShape s = bid_shape(q, cm);
        CHECK(s.flat);
        CHECK(s.depth == 8);
        for (int k = 0; k < 8; ++k) CHECK(unit_ticks(s, Side::Buy, k, LadderPricing::Cent) == 1200);
    }
}

TEST_CASE("ladder: penny bid ladder never goes negative") {
    CostModel cm = make_cm();
    LadderShape s = bid_shape(DayQuote{0.02, NaN, 0.01}, cm);
    for (int k = 0; k < 40; ++k) CHECK(unit_ticks(s, Side::Buy, k, LadderPricing::Cent) >= 0);
}

TEST_CASE("ladder: build_day_ladder books every unit") {
    CostModel cm = make_cm();
    book::OrderBook ob;
    build_day_ladder(DayQuote{12, 13, 1}, cm, LadderPricing::Cent, ob);
    CHECK(ob.order_count() == 16);
    auto a = ob.best(Side::Sell);
    auto b = ob.best(Side::Buy);
    REQUIRE(a);
    REQUIRE(b);
    CHECK(a->price == 1206);
    CHECK(a->qty == 1);
    CHECK(b->price == 1131);
    CHECK(b->qty == 1);
    // Rebuilding clears first.
    build_day_ladder(DayQuote{12, 13, 1}, cm, LadderPricing::Cent, ob);
    CHECK(ob.order_count() == 16);
}

TEST_CASE("ladder: exact pricing reproduces the formula") {
    CostModel cm = make_cm();
    const double markets[] = {0.37, 4.99, 12.0, 49.5, 199.99, 1234.56};
    for (double m : markets) {
        const double mids[] = {NaN, m - 1, m + 0.13, m * 1.4};
        const double lows[] = {NaN, m * 0.6, m * 1.1};
        for (double mid : mids)
            for (double low : lows) {
                DayQuote q{m, mid, low};
                LadderShape as = ask_shape(q, cm), bs = bid_shape(q, cm);
                const double tpd = 1e9;
                std::vector<double> ap{0.0}, bp{0.0};
                for (std::int64_t k = 0; k < as.depth; ++k)
                    ap.push_back(ap.back() + static_cast<double>(unit_ticks(as, Side::Sell, k, LadderPricing::ExactTest)));
                for (std::int64_t k = 0; k < bs.depth; ++k)
                    bp.push_back(bp.back() + static_cast<double>(unit_ticks(bs, Side::Buy, k, LadderPricing::ExactTest)));
                for (std::int64_t used = 0; used < as.depth; ++used)
                    for (std::int64_t qty = 1; used + qty <= as.depth; ++qty) {
                        double got = (ap[used + qty] - ap[used]) / tpd - static_cast<double>(qty) * m;
                        CHECK(std::abs(got - cm.buy_impact(m, mid, low, qty, used)) < 1e-6);
                    }
                for (std::int64_t used = 0; used < bs.depth; ++used)
                    for (std::int64_t qty = 1; used + qty <= bs.depth; ++qty) {
                        double got = static_cast<double>(qty) * m - (bp[used + qty] - bp[used]) / tpd;
                        CHECK(std::abs(got - cm.sell_impact(m, low, qty, used)) < 1e-6);
                    }
            }
    }
}
