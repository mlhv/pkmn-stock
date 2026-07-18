#include <catch2/catch_test_macros.hpp>

#include <stdexcept>

#include "pkmn_engine/portfolio.hpp"

using pkmn::Fill;
using pkmn::InsertionMap;
using pkmn::Portfolio;

TEST_CASE("buy updates cash, avg cost, and realized pnl like portfolio.py:_buy") {
    Portfolio pf(100.0, 4);
    pf.apply(Fill{100, 0, 8, 12.0, 1.0, 0.0});
    CHECK(pf.cash == 3.0);  // 100 - 96 - 1 (golden arithmetic)
    CHECK(pf.realized_pnl == -1.0);
    const auto* pos = pf.positions.find(0);
    REQUIRE(pos != nullptr);
    CHECK(pos->quantity == 8);
    CHECK(pos->avg_cost == 12.0);
    CHECK(pos->opened_on == 100);
}

TEST_CASE("adding to a position averages cost, keeps opened_on") {
    Portfolio pf(1000.0, 4);
    pf.apply(Fill{100, 0, 2, 10.0, 1.0, 0.0});
    pf.apply(Fill{101, 0, 2, 20.0, 1.0, 0.0});
    const auto* pos = pf.positions.find(0);
    REQUIRE(pos != nullptr);
    CHECK(pos->quantity == 4);
    CHECK(pos->avg_cost == 15.0);  // (10*2 + 40) / 4
    CHECK(pos->opened_on == 100);  // unchanged by the add
}

TEST_CASE("sell realizes pnl and a full close removes the position") {
    Portfolio pf(100.0, 4);
    pf.apply(Fill{100, 0, 4, 10.0, 1.0, 0.0});  // cash 59
    pf.apply(Fill{101, 0, -4, 15.0, 2.0, 0.5});
    // proceeds 60; cash 59 + 60 - 2 - 0.5 = 116.5
    CHECK(pf.cash == 116.5);
    // realized: -1 (buy fee) + (60 - 40 - 2 - 0.5) = 16.5
    CHECK(pf.realized_pnl == 16.5);
    CHECK(pf.positions.find(0) == nullptr);
}

TEST_CASE("oversell and zero-qty fills throw like portfolio.py") {
    Portfolio pf(100.0, 4);
    pf.apply(Fill{100, 0, 2, 10.0, 1.0, 0.0});
    CHECK_THROWS_AS(pf.apply(Fill{101, 0, -3, 10.0, 1.0, 0.0}), std::invalid_argument);
    CHECK_THROWS_AS(pf.apply(Fill{101, 1, -1, 10.0, 1.0, 0.0}), std::invalid_argument);
    CHECK_THROWS_AS(pf.apply(Fill{101, 0, 0, 10.0, 1.0, 0.0}), std::invalid_argument);
    // Fill.__post_init__ validation lives in apply(): price/fees/impact
    CHECK_THROWS_AS(pf.apply(Fill{101, 0, 1, 0.0, 1.0, 0.0}), std::invalid_argument);
    CHECK_THROWS_AS(pf.apply(Fill{101, 0, 1, 10.0, -1.0, 0.0}), std::invalid_argument);
    CHECK_THROWS_AS(pf.apply(Fill{101, 0, 1, 10.0, 1.0, -0.5}), std::invalid_argument);
}

TEST_CASE("equity sums positions in insertion order; missing mark throws") {
    Portfolio pf(10.0, 4);
    pf.apply(Fill{100, 2, 1, 5.0, 0.5, 0.0});
    pf.apply(Fill{100, 0, 1, 3.0, 0.5, 0.0});
    InsertionMap<double> marks(4);
    marks.set(2, 6.0);
    marks.set(0, 4.0);
    CHECK(pf.equity(marks) == 10.0 - 5.5 - 3.5 + 6.0 + 4.0);
    InsertionMap<double> missing(4);
    missing.set(2, 6.0);
    CHECK_THROWS_AS(pf.equity(missing), std::out_of_range);
}
