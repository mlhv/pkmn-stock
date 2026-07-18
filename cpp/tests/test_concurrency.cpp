// Core thread-safety with no Python involved: two engines on separate
// MarketViews in std::threads must reproduce their serial results exactly.
#include <catch2/catch_test_macros.hpp>

#include <thread>
#include <vector>

#include "pkmn_engine/backtest.hpp"
#include "pkmn_engine/strategies/buy_and_hold.hpp"

using namespace pkmn;

namespace {
// Same shape as the golden fixture (test_backtest_golden.cpp), offset per id
// so the two threads run genuinely different data.
MarketView make_view(double base) {
    std::vector<Day> days{100, 101, 102};
    std::vector<PriceRow> rows{{100, 0, base, base * 1.3, 1.0},
                               {101, 0, base * 1.2, base * 1.6, 1.0},
                               {102, 0, base * 1.5, base * 1.8, 1.0}};
    std::vector<MarkEvent> events{
        {100, 0, base}, {101, 0, base * 1.2}, {102, 0, base * 1.5}};
    return MarketView(1, days, rows, events);
}

BacktestResult run_one(double base) {
    auto mkt = make_view(base);
    ProductTable prods{{1}, {0}, {100}};
    CostModel cm;
    cm.impact_enabled = true;
    BuyAndHold strat(0);
    return run_backtest(mkt, prods, strat, cm, 100.0);
}
}  // namespace

TEST_CASE("run_backtest is thread-safe across independent instances") {
    BacktestResult serial_a = run_one(10.0);
    BacktestResult serial_b = run_one(20.0);

    BacktestResult threaded_a, threaded_b;
    std::thread ta([&] { threaded_a = run_one(10.0); });
    std::thread tb([&] { threaded_b = run_one(20.0); });
    ta.join();
    tb.join();

    REQUIRE(threaded_a.equity == serial_a.equity);
    REQUIRE(threaded_b.equity == serial_b.equity);
    REQUIRE(threaded_a.fills.size() == serial_a.fills.size());
    REQUIRE(threaded_b.fills.size() == serial_b.fills.size());
}
