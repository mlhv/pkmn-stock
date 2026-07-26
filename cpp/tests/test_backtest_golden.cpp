#include <catch2/catch_test_macros.hpp>

#include <vector>

#include "pkmn_engine/backtest.hpp"
#include "pkmn_engine/strategies/buy_and_hold.hpp"

using namespace pkmn;

namespace {
// Mirrors tests/test_cli_backtest.py seed(): one sealed product, three days,
// prices 10/12/15. price_row hardcodes mid=2.0/low=1.0 (crossed vs market
// -> zero impact even when enabled).
MarketView flat_view() {
    std::vector<Day> days{100, 101, 102};
    std::vector<PriceRow> rows{
        {100, 0, 10.0, 2.0, 1.0}, {101, 0, 12.0, 2.0, 1.0}, {102, 0, 15.0, 2.0, 1.0}};
    std::vector<MarkEvent> events{{100, 0, 10.0}, {101, 0, 12.0}, {102, 0, 15.0}};
    return MarketView(1, days, rows, events);
}

// Mirrors seed_impact(): mids 13/16/18 (uncrossed).
MarketView impact_view() {
    std::vector<Day> days{100, 101, 102};
    std::vector<PriceRow> rows{
        {100, 0, 10.0, 13.0, 1.0}, {101, 0, 12.0, 16.0, 1.0}, {102, 0, 15.0, 18.0, 1.0}};
    std::vector<MarkEvent> events{{100, 0, 10.0}, {101, 0, 12.0}, {102, 0, 15.0}};
    return MarketView(1, days, rows, events);
}

ProductTable one_sealed() { return ProductTable{{1}, {0}, {100}}; }

// Emits no orders: isolates seeding so the equity curve is purely
// cash + seeded-position value across carry-forward marks.
struct NoTrade : Strategy {
    std::vector<Order> on_bar(const Context&) override { return {}; }
};
}  // namespace

TEST_CASE("golden flat-cost: matches test_backtest_golden_numbers exactly") {
    auto mkt = flat_view();
    auto prods = one_sealed();
    CostModel cm;  // impact off = --no-impact
    BuyAndHold strat(0);
    auto res = run_backtest(mkt, prods, strat, cm, 100.0);
    // D1: order 10 units; equity 100. D2: fill clipped to 8 (cap 8, cash 8);
    // cash 3, equity 99. D3: equity 3 + 8*15 = 123. EXACT doubles.
    REQUIRE(res.equity == std::vector<double>{100.0, 99.0, 123.0});
    REQUIRE(res.fills.size() == 1);
    CHECK(res.fills[0].day == 101);
    CHECK(res.fills[0].quantity == 8);
    CHECK(res.fills[0].price == 12.0);
    CHECK(res.fills[0].fees == 1.0);
    CHECK(res.fills[0].impact == 0.0);
}

TEST_CASE("golden impact-on: matches test_backtest_golden_numbers_with_impact") {
    auto mkt = impact_view();
    auto prods = one_sealed();
    CostModel cm;
    cm.impact_enabled = true;
    BuyAndHold strat(0);
    auto res = run_backtest(mkt, prods, strat, cm, 100.0);
    // D2: spread 4, cap 8. qty 8 -> impact 16, cost 113 > 100 -> shrink to
    // qty 7 -> impact 12.25, cost 97.25. cash 2.75, equity 86.75.
    REQUIRE(res.equity == std::vector<double>{100.0, 86.75, 107.75});
    REQUIRE(res.fills.size() == 1);
    CHECK(res.fills[0].quantity == 7);
    CHECK(res.fills[0].impact == 12.25);
}

TEST_CASE("run_backtest is repeatable on one MarketView (reset-safety)") {
    auto mkt = flat_view();
    auto prods = one_sealed();
    CostModel cm;
    BuyAndHold strat(0);
    auto r1 = run_backtest(mkt, prods, strat, cm, 100.0);
    auto r2 = run_backtest(mkt, prods, strat, cm, 100.0);
    REQUIRE(r1.equity == r2.equity);
    REQUIRE(r1.fills.size() == r2.fills.size());
}

TEST_CASE("orders for assets that do not print expire unfilled") {
    // asset prints D1 only; strategy orders on D1; no D2 print -> no fill ever
    std::vector<Day> days{100, 101};
    std::vector<PriceRow> rows{{100, 0, 10.0, 2.0, 1.0}};
    std::vector<MarkEvent> events{{100, 0, 10.0}};
    MarketView mkt(1, days, rows, events);
    auto prods = one_sealed();
    CostModel cm;
    BuyAndHold strat(0);
    auto res = run_backtest(mkt, prods, strat, cm, 100.0);
    REQUIRE(res.fills.empty());
    REQUIRE(res.equity == std::vector<double>{100.0, 100.0});
}

TEST_CASE("seeded holdings value through the loop with no trading") {
    auto mkt = flat_view();  // asset 0, marks 10/12/15 on days 100/101/102
    auto prods = one_sealed();
    CostModel cm;  // impact off
    NoTrade strat;
    // Start with 50 cash and 4 units of asset 0 (cost basis 9, opened day 90).
    std::vector<SeedPosition> holdings{{0, 4, 9.0, 90}};
    auto res = run_backtest(mkt, prods, strat, cm, 50.0, holdings);
    // No fills ever; equity = 50 + 4*mark. D1 50+40=90, D2 50+48=98,
    // D3 50+60=110. EXACT doubles.
    REQUIRE(res.fills.empty());
    REQUIRE(res.equity == std::vector<double>{90.0, 98.0, 110.0});
}

TEST_CASE("no initial holdings leaves the existing signature behavior") {
    auto mkt = flat_view();
    auto prods = one_sealed();
    CostModel cm;
    NoTrade strat;
    // 5-arg call still compiles (default empty holdings); flat cash curve.
    auto res = run_backtest(mkt, prods, strat, cm, 50.0);
    REQUIRE(res.fills.empty());
    REQUIRE(res.equity == std::vector<double>{50.0, 50.0, 50.0});
}
