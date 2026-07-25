#include <catch2/catch_test_macros.hpp>

#include <stdexcept>

#include "pkmn_engine/portfolio.hpp"

using pkmn::Fill;
using pkmn::InsertionMap;
using pkmn::Portfolio;
using pkmn::SeedPosition;

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

TEST_CASE("seed installs positions without touching cash or realized pnl") {
    Portfolio pf(100.0, 4);
    // Insertion order is list order: asset 2 first, then asset 0.
    pf.seed(std::vector<SeedPosition>{{2, 3, 5.0, 90}, {0, 1, 8.0, 91}});
    CHECK(pf.cash == 100.0);          // seeding never moves cash
    CHECK(pf.realized_pnl == 0.0);    // ...or realized P&L
    const auto* p2 = pf.positions.find(2);
    REQUIRE(p2 != nullptr);
    CHECK(p2->quantity == 3);
    CHECK(p2->avg_cost == 5.0);
    CHECK(p2->opened_on == 90);
    const auto* p0 = pf.positions.find(0);
    REQUIRE(p0 != nullptr);
    CHECK(p0->quantity == 1);
    CHECK(p0->avg_cost == 8.0);
    CHECK(p0->opened_on == 91);
    // equity sums seeded positions in insertion order (2 before 0).
    InsertionMap<double> marks(4);
    marks.set(2, 6.0);
    marks.set(0, 10.0);
    CHECK(pf.equity(marks) == 100.0 + 3 * 6.0 + 1 * 10.0);  // 128.0
}

TEST_CASE("selling a seeded position realizes pnl against its cost basis") {
    Portfolio pf(0.0, 4);
    pf.seed(std::vector<SeedPosition>{{0, 4, 9.0, 90}});
    pf.apply(Fill{101, 0, -4, 12.0, 1.0, 0.0});  // sell all 4 at 12, fee 1
    // proceeds 48; cash 0 + 48 - 1 = 47
    CHECK(pf.cash == 47.0);
    // realized: 48 - 4*9 - 1 = 48 - 36 - 1 = 11
    CHECK(pf.realized_pnl == 11.0);
    CHECK(pf.positions.find(0) == nullptr);  // full close removes it
}

TEST_CASE("strategy buying more of a seeded asset averages the cost basis") {
    Portfolio pf(1000.0, 4);
    pf.seed(std::vector<SeedPosition>{{0, 2, 10.0, 90}});
    pf.apply(Fill{101, 0, 2, 20.0, 0.0, 0.0});  // buy 2 more at 20
    const auto* pos = pf.positions.find(0);
    REQUIRE(pos != nullptr);
    CHECK(pos->quantity == 4);
    CHECK(pos->avg_cost == 15.0);  // (10*2 + 20*2) / 4
    CHECK(pos->opened_on == 90);   // add keeps the seed's opened_on
}

TEST_CASE("seed validates quantity, avg_cost, and rejects duplicates") {
    Portfolio pf(100.0, 4);
    CHECK_THROWS_AS(pf.seed(std::vector<SeedPosition>{{0, 0, 5.0, 90}}),
                    std::invalid_argument);  // zero qty
    CHECK_THROWS_AS(pf.seed(std::vector<SeedPosition>{{0, -1, 5.0, 90}}),
                    std::invalid_argument);  // negative qty
    CHECK_THROWS_AS(pf.seed(std::vector<SeedPosition>{{0, 1, -1.0, 90}}),
                    std::invalid_argument);  // negative avg_cost
    CHECK_THROWS_AS(
        pf.seed(std::vector<SeedPosition>{{0, 1, 5.0, 90}, {0, 1, 5.0, 91}}),
        std::invalid_argument);  // duplicate asset
    // avg_cost == 0 is legal (a gift/pull with no cash basis)
    Portfolio ok(100.0, 4);
    ok.seed(std::vector<SeedPosition>{{0, 1, 0.0, 90}});
    CHECK(ok.positions.find(0)->avg_cost == 0.0);
}
