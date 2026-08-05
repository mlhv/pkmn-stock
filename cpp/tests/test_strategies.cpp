#include <catch2/catch_test_macros.hpp>

#include "pkmn_engine/backtest.hpp"
#include "pkmn_engine/strategies/buy_and_hold.hpp"
#include "pkmn_engine/strategies/dip_buyer.hpp"
#include "pkmn_engine/strategies/factory.hpp"
#include "pkmn_engine/strategies/momentum.hpp"
#include "pkmn_engine/strategies/sealed_accumulation.hpp"

using namespace pkmn;

TEST_CASE("dip buyer: buys the dip, exits on hold_days from the FILL date") {
    // one single, price 10 for days 100-104, crashes to 6 on day 105+.
    std::vector<Day> days;
    std::vector<PriceRow> rows;
    std::vector<MarkEvent> events;
    for (Day d = 100; d <= 118; ++d) {
        double px = d < 105 ? 10.0 : 6.0;
        days.push_back(d);
        rows.push_back({d, 0, px, px * 1.2, px * 0.9});
        if (d == 100 || d == 105) events.push_back({d, 0, px});
    }
    MarketView mkt(1, days, rows, events);
    ProductTable prods{{3}, {1}, {100}};
    CostModel cm;
    DipBuyer strat(3, 0.20, 5, 99.0, 10, 1.0, 3.0);  // 20% dip vs 3d ago, hold 5
    auto res = run_backtest(mkt, prods, strat, cm, 100.0);
    REQUIRE(res.fills.size() >= 2);
    CHECK(res.fills[0].quantity > 0);   // dip entry
    CHECK(res.fills[1].quantity < 0);   // hold_days exit
    // exit fill lands hold_days+1 after entry: emitted when
    // (today - opened_on) >= 5, filled T+1.
    CHECK(res.fills[1].day - res.fills[0].day == 6);
}

TEST_CASE("sealed accumulation: age gate excludes too-new and too-old product") {
    // two sealed products in deep drawdown; only asset 0 is inside the age band
    std::vector<Day> days{200, 201, 202};
    std::vector<PriceRow> rows;
    std::vector<MarkEvent> events;
    for (Day d = 200; d <= 202; ++d) {
        double px = d == 200 ? 100.0 : 50.0;  // 50% drawdown from peak
        for (AssetId a = 0; a < 2; ++a) {
            rows.push_back({d, a, px, px * 1.1, px * 0.9});
            if (d == 200 || d == 201) events.push_back({d, a, px});
        }
    }
    MarketView mkt(2, days, rows, events);
    // asset 0 released 100 days ago (in band 60..365); asset 1 released
    // 10 days ago (too new)
    ProductTable prods{{1, 2}, {0, 0}, {100, 190}};
    CostModel cm;
    SealedAccumulation strat(60, 365, 0.25, 99.0, 10, 1.0);
    auto res = run_backtest(mkt, prods, strat, cm, 1000.0);
    REQUIRE(!res.fills.empty());
    for (const auto& f : res.fills) CHECK(f.asset == 0);
}

TEST_CASE("momentum: flat portfolio rebalances immediately, holds winners") {
    // two singles: asset 0 rising, asset 1 falling; top_n=1 must pick 0.
    std::vector<Day> days;
    std::vector<PriceRow> rows;
    std::vector<MarkEvent> events;
    for (Day d = 100; d <= 110; ++d) {
        double up = 10.0 + static_cast<double>(d - 100);
        double down = 20.0 - static_cast<double>(d - 100);
        days.push_back(d);
        rows.push_back({d, 0, up, up * 1.2, up * 0.9});
        rows.push_back({d, 1, down, down * 1.2, down * 0.9});
        events.push_back({d, 0, up});
        events.push_back({d, 1, down});
    }
    MarketView mkt(2, days, rows, events);
    ProductTable prods{{3, 4}, {1, 1}, {100, 100}};
    CostModel cm;
    CrossSectionalMomentum strat(5, 1, 3, 3.0);
    auto res = run_backtest(mkt, prods, strat, cm, 100.0);
    REQUIRE(!res.fills.empty());
    for (const auto& f : res.fills) {
        if (f.quantity > 0) CHECK(f.asset == 0);  // only the winner is bought
    }
}

TEST_CASE("buy and hold: deficit sweep spends the integer-sizing remainder") {
    // Mirrors tests/strategies/test_buy_and_hold.py::
    // test_buy_and_hold_deficit_sweep_deploys_the_remainder exactly.
    // Two sealed assets priced 30, cash 1000, n_assets 2.
    // Pass 1: target 500 -> floor(500/30) = 16 each = 960 spent, 40 left.
    // Pass 2: both sit 500 - 16*30 = 20 below target; strict > gives the tie
    // to the earlier one in screen order -> 17 and 16, 990 spent.
    std::vector<Day> days{100};
    std::vector<PriceRow> rows{{100, 0, 30.0, 34.5, 27.0}, {100, 1, 30.0, 34.5, 27.0}};
    std::vector<MarkEvent> events{{100, 0, 30.0}, {100, 1, 30.0}};
    MarketView mkt(2, days, rows, events);
    ProductTable prods{{1, 2}, {0, 0}, {100, 100}};
    InsertionMap<Position> positions(2);
    InsertionMap<double> marks(2);
    marks.set(0, 30.0);
    marks.set(1, 30.0);
    BuyAndHold strat(0, 2);
    Context ctx{100, mkt, prods, positions, 1000.0, marks};
    auto orders = strat.on_bar(ctx);
    REQUIRE(orders.size() == 2);
    CHECK(orders[0].asset == 0);
    CHECK(orders[0].quantity == 17);
    CHECK(orders[1].asset == 1);
    CHECK(orders[1].quantity == 16);
    // Entered once: the second bar emits nothing.
    CHECK(strat.on_bar(ctx).empty());
}

TEST_CASE("buy and hold: the screen keeps the densest, tightest n_assets") {
    // asset 0 prints on all 3 days; assets 1 and 2 print only on the last,
    // asset 1 with a 0.1 relative spread and asset 2 with 0.5. n_assets = 2
    // must therefore keep 0 (density) then 1 (spread breaks the density tie).
    std::vector<Day> days{100, 101, 102};
    std::vector<PriceRow> rows{{100, 0, 10.0, 10.0, 9.0},
                               {101, 0, 10.0, 10.0, 9.0},
                               {102, 0, 10.0, 10.0, 9.0},
                               {102, 1, 10.0, 10.0, 9.0},
                               {102, 2, 10.0, 10.0, 5.0}};
    std::vector<MarkEvent> events{{100, 0, 10.0}, {102, 1, 10.0}, {102, 2, 10.0}};
    MarketView mkt(3, days, rows, events);
    ProductTable prods{{1, 2, 3}, {0, 0, 0}, {100, 100, 100}};
    InsertionMap<Position> positions(3);
    InsertionMap<double> marks(3);
    marks.set(2, 10.0);  // insertion order deliberately NOT screen order
    marks.set(1, 10.0);
    marks.set(0, 10.0);
    BuyAndHold strat(0, 2);
    Context ctx{102, mkt, prods, positions, 100.0, marks};
    auto orders = strat.on_bar(ctx);
    // target 100/2 = 50 -> 5 units each, 100 spent, nothing left to sweep.
    REQUIRE(orders.size() == 2);
    CHECK(orders[0].asset == 0);
    CHECK(orders[0].quantity == 5);
    CHECK(orders[1].asset == 1);
    CHECK(orders[1].quantity == 5);
}

TEST_CASE("make_strategy: stray param key throws, valid params still work") {
    ParamMap bogus{{"dip_window_days", 7.0}, {"bogus_param", 1.0}};
    CHECK_THROWS_AS(make_strategy("dip-buyer", bogus, -1), std::invalid_argument);

    ParamMap valid{{"dip_window_days", 7.0}, {"dip_threshold", 0.30}};
    auto strat = make_strategy("dip-buyer", valid, -1);
    CHECK(strat != nullptr);
}
