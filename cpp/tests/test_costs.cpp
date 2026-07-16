#include <catch2/catch_test_macros.hpp>

#include <cmath>
#include <limits>

#include "pkmn_engine/costs.hpp"

using pkmn::CostModel;

namespace {
constexpr double kNaN = std::numeric_limits<double>::quiet_NaN();
}

TEST_CASE("max_daily_qty: strict < at tier thresholds (costs.py:55-61)") {
    CostModel cm;
    CHECK(cm.max_daily_qty(4.99) == 20);
    CHECK(cm.max_daily_qty(5.0) == 8);  // exactly at threshold -> NEXT tier
    CHECK(cm.max_daily_qty(49.99) == 8);
    CHECK(cm.max_daily_qty(50.0) == 3);
    CHECK(cm.max_daily_qty(200.0) == 1);  // above last tier -> fallback
}

TEST_CASE("buy_impact matches the hand-derived golden (test_cli_backtest.py)") {
    CostModel cm;
    cm.impact_enabled = true;
    // $12 price -> cap 8; spread mid-market = 16-12 = 4.
    // impact(qty=7, used=0) = 4 * 7 * 7 / (2*8) = 12.25 exactly.
    CHECK(cm.buy_impact(12.0, 16.0, 7, 0) == 12.25);
    CHECK(cm.buy_impact(12.0, 16.0, 8, 0) == 16.0);
    // depth-aware: used shifts the walk deeper: 4 * 2 * (2*3 + 2) / 16 = 4.0
    CHECK(cm.buy_impact(12.0, 16.0, 2, 3) == 4.0);
}

TEST_CASE("impact is zero when disabled, missing, crossed, or qty<=0") {
    CostModel cm;  // impact_enabled defaults false
    CHECK(cm.buy_impact(12.0, 16.0, 5, 0) == 0.0);
    cm.impact_enabled = true;
    CHECK(cm.buy_impact(12.0, kNaN, 5, 0) == 0.0);   // missing mid
    CHECK(cm.sell_impact(12.0, kNaN, 5, 0) == 0.0);  // missing low
    CHECK(cm.buy_impact(12.0, 11.0, 5, 0) == 0.0);   // crossed: mid < market
    CHECK(cm.sell_impact(12.0, 13.0, 5, 0) == 0.0);  // crossed: low > market
    CHECK(cm.buy_impact(12.0, 16.0, 0, 0) == 0.0);
}

TEST_CASE("sell_impact walks market toward low") {
    CostModel cm;
    cm.impact_enabled = true;
    // spread market-low = 12-10 = 2; qty 4 used 0 at cap 8: 2*4*4/16 = 2.0
    CHECK(cm.sell_impact(12.0, 10.0, 4, 0) == 2.0);
}
