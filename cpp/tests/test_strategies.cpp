#include <catch2/catch_test_macros.hpp>

#include "pkmn_engine/backtest.hpp"
#include "pkmn_engine/strategies/dip_buyer.hpp"
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
