#include <catch2/catch_test_macros.hpp>

#include <cmath>
#include <limits>

#include "pkmn_engine/costs.hpp"

using pkmn::CostModel;

namespace {
constexpr double kNaN = std::numeric_limits<double>::quiet_NaN();
}

TEST_CASE("max_daily_qty: strict < at tier thresholds (costs.py max_daily_qty)") {
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
    CHECK(cm.buy_impact(12.0, 16.0, 10.0, 7, 0) == 12.25);
    CHECK(cm.buy_impact(12.0, 16.0, 10.0, 8, 0) == 16.0);
    // depth-aware: used shifts the walk deeper: 4 * 2 * (2*3 + 2) / 16 = 4.0
    CHECK(cm.buy_impact(12.0, 16.0, 10.0, 2, 3) == 4.0);
}

TEST_CASE("impact is zero when disabled, missing, crossed, or qty<=0") {
    CostModel cm;  // impact_enabled defaults false
    CHECK(cm.buy_impact(12.0, 16.0, 10.0, 5, 0) == 0.0);
    cm.impact_enabled = true;
    CHECK(cm.buy_impact(12.0, kNaN, 10.0, 5, 0) == 0.0);   // missing mid
    CHECK(cm.sell_impact(12.0, kNaN, 5, 0) == 0.0);  // missing low
    CHECK(cm.buy_impact(12.0, 11.0, 10.0, 5, 0) == 0.0);   // crossed: mid < market
    CHECK(cm.sell_impact(12.0, 13.0, 5, 0) == 0.0);  // crossed: low > market
    CHECK(cm.buy_impact(12.0, 16.0, 10.0, 0, 0) == 0.0);
}

TEST_CASE("sell_impact walks market toward low") {
    CostModel cm;
    cm.impact_enabled = true;
    // spread market-low = 12-10 = 2; qty 4 used 0 at cap 8: 2*4*4/16 = 2.0
    CHECK(cm.sell_impact(12.0, 10.0, 4, 0) == 2.0);
}

TEST_CASE("buy_base is the single definition of the executable buy price") {
    CostModel cm;
    cm.impact_enabled = true;
    CHECK(cm.buy_base(10.0, 25.0) == 25.0);  // inverted row: floored at the listing
    CHECK(cm.buy_base(30.0, 25.0) == 30.0);  // ordinary row: the print stands
    CHECK(cm.buy_base(10.0, kNaN) == 10.0);  // no low: never invent a floor
    cm.impact_enabled = false;
    CHECK(cm.buy_base(10.0, 25.0) == 10.0);  // impact off: the floor does not exist
}

TEST_CASE("buy_impact walks at the base's depth tier, not the print's") {
    // Mirrors tests/engine/test_costs.py
    // test_buy_impact_walk_uses_the_bases_depth_tier_not_the_prints.
    // market 4 (tier <5 -> Q=20) but base 60 (tier <200 -> Q=3): premium
    // (60-4)*2 = 112, walk (90-60)*2*2/(2*3) = 20 -> 132. Off the print the
    // walk would be 3.0 and the total 115 — a $4 product's depth granted to a
    // $60 purchase.
    CostModel cm;
    cm.impact_enabled = true;
    CHECK(cm.buy_impact(4.0, 90.0, 60.0, 2, 0) == 132.0);
}

TEST_CASE("a missing or crossed mid still pays the floor premium") {
    // Mirrors tests/engine/test_costs.py
    // test_missing_or_crossed_mid_still_pays_the_floor_premium: the branch
    // kills the WALK only. market 10, low 20, qty 3 -> premium 30 both ways.
    CostModel cm;
    cm.impact_enabled = true;
    CHECK(cm.buy_impact(10.0, kNaN, 20.0, 3, 0) == 30.0);
    CHECK(cm.buy_impact(10.0, 15.0, 20.0, 3, 0) == 30.0);
}

// Cite the symbol, not a line range: the previous "costs.py:63-89" here was
// already stale by four lines when it was checked.
TEST_CASE("buy_impact floors the fill at the cheapest listing (costs.py buy_impact)") {
    CostModel cm;
    cm.impact_enabled = true;
    // market 10 (sales-derived) below low 25 (cheapest listing); mid 25.
    // base = max(10, 25) = 25; premium = (25-10)*2 = 30; walk spread
    // mid-base = 0 -> total 30.
    CHECK(cm.buy_impact(10.0, 25.0, 25.0, 2, 0) == 30.0);
    // The walk starts AT the floor, not at market: base 20, premium
    // (20-10)*2 = 20; spread 40-20 = 20 -> 20*2*2/(2*8) = 5 -> total 25.
    CHECK(cm.buy_impact(10.0, 40.0, 20.0, 2, 0) == 25.0);
    // market at or above low: untouched, identical to a missing low.
    CHECK(cm.buy_impact(30.0, 40.0, 20.0, 2, 0) == cm.buy_impact(30.0, 40.0, kNaN, 2, 0));
    // Disabled beats everything, floor included.
    cm.impact_enabled = false;
    CHECK(cm.buy_impact(10.0, 40.0, 1000.0, 5, 0) == 0.0);
}
