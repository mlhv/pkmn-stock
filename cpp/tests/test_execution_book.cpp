#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>

#include <cmath>
#include <cstdint>
#include <limits>
#include <random>
#include <vector>

#include "pkmn_engine/backtest.hpp"
#include "pkmn_engine/execution.hpp"
#include "pkmn_engine/execution_book.hpp"
#include "pkmn_engine/strategies/buy_and_hold.hpp"

using namespace pkmn;
using Catch::Approx;

namespace {
const double NaN = std::numeric_limits<double>::quiet_NaN();

CostModel book_cm(ExecutionMode m = ExecutionMode::Book) {
    CostModel cm;
    cm.impact_enabled = true;
    cm.execution = m;
    return cm;
}

// One asset, one row per day; mid/low per day.
MarketView one_asset(const std::vector<Day>& days, double market, double mid, double low) {
    std::vector<PriceRow> rows;
    std::vector<MarkEvent> events;
    for (Day d : days) {
        rows.push_back({d, 0, market, mid, low});
        events.push_back({d, 0, market});
    }
    return MarketView(1, days, rows, events);
}

// Seeded random one-sided orders: per asset-day either buys or sells (or
// nothing), sometimes two orders on the same side.
struct RandomOneSided : Strategy {
    std::uint32_t seed;
    std::mt19937 rng;
    explicit RandomOneSided(std::uint32_t s) : seed(s), rng(s) {}
    void reset() override { rng.seed(seed); }
    std::vector<Order> on_bar(const Context& ctx) override {
        std::vector<Order> out;
        std::uniform_int_distribution<int> side(0, 2), qty(1, 12), twice(0, 3);
        for (AssetId a = 0; a < static_cast<AssetId>(ctx.market.n_assets()); ++a) {
            int s = side(rng);
            int n = twice(rng) == 0 ? 2 : 1;
            for (int i = 0; i < n; ++i) {
                std::int64_t q = qty(rng);
                if (s == 1) out.push_back({a, q});
                if (s == 2) {
                    const Position* p = ctx.positions.find(a);
                    if (p != nullptr) out.push_back({a, -q});
                }
            }
        }
        return out;
    }
};

std::int64_t cents_sum(std::initializer_list<double> v) {
    double s = 0;
    for (double x : v) s += x;
    return std::llround(s * 100.0);
}
}  // namespace

TEST_CASE("book: cent buy matches the hand ladder") {
    auto mkt = one_asset({100}, 12.0, 13.0, 1.0);
    mkt.reset();
    mkt.load_day(100);
    CostModel cm = book_cm();
    Portfolio pf(100.0, 1);
    DayBooks books(1);
    books.new_day();
    auto fills = execute_book({{0, 10}}, mkt, pf, 100, cm, books);
    REQUIRE(fills.size() == 1);
    CHECK(fills[0].quantity == 7);
    CHECK(fills[0].price == 12.0);
    CHECK(fills[0].fees == 1.0);
    CHECK(fills[0].impact == Approx(87.06 - 84.0).margin(1e-9));
    CHECK(pf.cash == Approx(100.0 - 88.06).margin(1e-9));
}

TEST_CASE("book: buy and sell same day each get full depth") {
    auto mkt = one_asset({100}, 12.0, 13.0, 11.0);
    mkt.reset();
    mkt.load_day(100);
    std::vector<Order> orders{{0, -8}, {0, 8}};
    CostModel cm = book_cm();

    Portfolio pf(1000.0, 1);
    pf.seed({{0, 20, 10.0, 90}});
    DayBooks books(1);
    books.new_day();
    auto fills = execute_book(orders, mkt, pf, 100, cm, books);
    REQUIRE(fills.size() == 2);
    CHECK(fills[0].quantity == -8);
    CHECK(fills[1].quantity == 8);

    Portfolio pf2(1000.0, 1);
    pf2.seed({{0, 20, 10.0, 90}});
    CostModel fcm = book_cm(ExecutionMode::Formula);
    auto ffills = execute(orders, mkt, pf2, 100, fcm);
    CHECK(ffills.size() == 1);
}

TEST_CASE("book: second buy continues up the same ladder") {
    auto mkt = one_asset({100}, 12.0, 13.0, 1.0);
    mkt.reset();
    mkt.load_day(100);
    CostModel cm = book_cm();
    Portfolio pf(1000.0, 1);
    DayBooks books(1);
    books.new_day();
    auto fills = execute_book({{0, 3}, {0, 3}}, mkt, pf, 100, cm, books);
    REQUIRE(fills.size() == 2);
    CHECK(cents_sum({12.06, 12.19, 12.31}) - 3600 == 56);
    CHECK(fills[0].impact == Approx(0.56).margin(1e-9));
    CHECK(fills[1].impact == Approx(1.69).margin(1e-9));
}

TEST_CASE("book: ladder is rebuilt next day") {
    auto mkt = one_asset({100, 101}, 12.0, 13.0, 1.0);
    mkt.reset();
    CostModel cm = book_cm();
    Portfolio pf(10000.0, 1);
    DayBooks books(1);
    for (Day d : {100, 101}) {
        mkt.load_day(d);
        books.new_day();
        auto fills = execute_book({{0, 8}}, mkt, pf, d, cm, books);
        REQUIRE(fills.size() == 1);
        CHECK(fills[0].quantity == 8);
        CHECK(fills[0].impact == Approx(100.0 - 96.0).margin(1e-9));
    }
}

TEST_CASE("book: buy on crossed quote charges only the floor premium") {
    auto mkt = one_asset({100}, 12.0, 11.0, 20.0);
    mkt.reset();
    mkt.load_day(100);
    CostModel cm = book_cm();
    Portfolio pf(1000.0, 1);
    DayBooks books(1);
    books.new_day();
    auto fills = execute_book({{0, 2}}, mkt, pf, 100, cm, books);
    REQUIRE(fills.size() == 1);
    CHECK(fills[0].quantity == 2);
    CHECK(fills[0].price == 12.0);
    CHECK(fills[0].impact == 16.0);
}

TEST_CASE("book: exact mode equals formula on one-sided days") {
    const std::size_t n_assets = 5;
    std::mt19937 rng(20261023);
    std::uniform_real_distribution<double> px(0.5, 300.0), up(0.0, 0.3), dn(0.0, 0.3),
        coin(0.0, 1.0);
    std::vector<Day> days;
    std::vector<PriceRow> rows;
    std::vector<MarkEvent> events;
    for (Day d = 0; d < 40; ++d) {
        days.push_back(d);
        for (AssetId a = 0; a < static_cast<AssetId>(n_assets); ++a) {
            if (d > 0 && coin(rng) < 0.1) continue;  // occasional non-print
            double m = std::round(px(rng) * 100.0) / 100.0;
            double mid = m * (1.0 + up(rng));
            double low = m * (1.0 - dn(rng));
            double c = coin(rng);
            if (c < 0.1) mid = NaN;
            else if (c < 0.2) low = NaN;
            else if (c < 0.3) low = m * (1.0 + up(rng));  // floor lifts the ask base
            else if (c < 0.35) mid = m * (1.0 - dn(rng));  // crossed ask
            rows.push_back({d, a, m, mid, low});
            events.push_back({d, a, m});
        }
    }
    MarketView mkt(n_assets, days, rows, events);
    ProductTable prods{{1, 2, 3, 4, 5}, {0, 0, 0, 0, 0}, {0, 0, 0, 0, 0}};

    RandomOneSided s1(7), s2(7);
    auto fr = run_backtest(mkt, prods, s1, book_cm(ExecutionMode::Formula), 5000.0);
    auto br = run_backtest(mkt, prods, s2, book_cm(ExecutionMode::BookExactTest), 5000.0);
    REQUIRE(fr.fills.size() > 50);
    REQUIRE(fr.fills.size() == br.fills.size());
    bool any_impact = false;
    for (std::size_t i = 0; i < fr.fills.size(); ++i) {
        const auto& f = fr.fills[i];
        const auto& b = br.fills[i];
        CHECK(f.day == b.day);
        CHECK(f.asset == b.asset);
        CHECK(f.quantity == b.quantity);
        CHECK(f.price == Approx(b.price).margin(1e-6));
        CHECK(f.fees == Approx(b.fees).margin(1e-6));
        CHECK(f.impact == Approx(b.impact).margin(1e-6));
        if (f.impact > 0.01) any_impact = true;
    }
    CHECK(any_impact);
    REQUIRE(fr.equity.size() == br.equity.size());
    for (std::size_t i = 0; i < fr.equity.size(); ++i)
        CHECK(fr.equity[i] == Approx(br.equity[i]).margin(1e-6));
}

TEST_CASE("book: impact off ignores execution mode") {
    std::vector<Day> days{100, 101, 102};
    std::vector<PriceRow> rows{
        {100, 0, 10.0, 13.0, 1.0}, {101, 0, 12.0, 16.0, 1.0}, {102, 0, 15.0, 18.0, 1.0}};
    std::vector<MarkEvent> events{{100, 0, 10.0}, {101, 0, 12.0}, {102, 0, 15.0}};
    MarketView mkt(1, days, rows, events);
    ProductTable prods{{1}, {0}, {100}};
    CostModel off;
    CostModel off_book;
    off_book.execution = ExecutionMode::Book;
    BuyAndHold s1(0), s2(0);
    auto a = run_backtest(mkt, prods, s1, off, 100.0);
    auto b = run_backtest(mkt, prods, s2, off_book, 100.0);
    REQUIRE(a.equity == b.equity);
    REQUIRE(a.days == b.days);
    REQUIRE(a.fills.size() == b.fills.size());
    for (std::size_t i = 0; i < a.fills.size(); ++i) {
        CHECK(a.fills[i].day == b.fills[i].day);
        CHECK(a.fills[i].quantity == b.fills[i].quantity);
        CHECK(a.fills[i].price == b.fills[i].price);
        CHECK(a.fills[i].fees == b.fills[i].fees);
        CHECK(a.fills[i].impact == b.fills[i].impact);
    }
}

TEST_CASE("book: hypothetical sale beyond depth extends the ramp") {
    // market 60 is in the < $200 tier: depth 3. A 5-unit hypothetical exit
    // matches the 3 resting bids, then prices units 3 and 4 on the extended
    // ramp (extrapolation, used only by hypothetical exits).
    CostModel cm = book_cm();
    DayQuote q{60.0, NaN, 50.0};
    LadderShape s = bid_shape(q, cm);
    REQUIRE(s.depth == 3);
    std::int64_t ticks = 0;
    for (std::int64_t k = 0; k < 5; ++k)
        ticks += unit_ticks(s, book::Side::Buy, k, LadderPricing::Cent);
    double expected = 5 * 60.0 - static_cast<double>(ticks) / 100.0;
    CHECK(hypothetical_sell_impact(q, cm, LadderPricing::Cent, 5) == expected);
    // At the exact ladder the extension is the formula's closed form.
    CHECK(hypothetical_sell_impact(q, cm, LadderPricing::ExactTest, 5) ==
          Approx(cm.sell_impact(60.0, 50.0, 5, 0)).margin(1e-6));
    // Within depth, too.
    CHECK(hypothetical_sell_impact(q, cm, LadderPricing::ExactTest, 2) ==
          Approx(cm.sell_impact(60.0, 50.0, 2, 0)).margin(1e-6));
}

TEST_CASE("book: hypothetical sale degenerate inputs are zero") {
    CostModel cm = book_cm();
    CHECK(hypothetical_sell_impact({60.0, NaN, NaN}, cm, LadderPricing::Cent, 5) == 0.0);
    CHECK(hypothetical_sell_impact({60.0, NaN, 50.0}, cm, LadderPricing::Cent, 0) == 0.0);
    // low >= market: flat bid book at market, no walk.
    CHECK(hypothetical_sell_impact({60.0, NaN, 70.0}, cm, LadderPricing::Cent, 5) == 0.0);
}

TEST_CASE("book: penny bid ramp extension clamps at zero ticks") {
    // Carry-over from Task 1: the >= 0 clamp in unit_ticks. market 0.02,
    // low 0.01, depth 20 (< $5 tier): unit 200 sits at 0.02 - 0.01*200.5/20
    // = -0.08025 dollars before the clamp.
    CostModel cm = book_cm();
    DayQuote q{0.02, NaN, 0.01};
    LadderShape s = bid_shape(q, cm);
    REQUIRE(s.depth == 20);
    CHECK(unit_ticks(s, book::Side::Buy, 200, LadderPricing::Cent) == 0);
    CHECK(unit_ticks(s, book::Side::Buy, 200, LadderPricing::ExactTest) == 0);
    for (std::int64_t qty : {1, 20, 21, 60, 201, 500}) {
        double imp = hypothetical_sell_impact(q, cm, LadderPricing::Cent, qty);
        CHECK(imp >= 0.0);
        CHECK(imp <= static_cast<double>(qty) * 0.02);
    }
    // Past the zero crossing every further unit fetches nothing, so the
    // impact on those units is the whole of their market value.
    double a = hypothetical_sell_impact(q, cm, LadderPricing::Cent, 300);
    double b = hypothetical_sell_impact(q, cm, LadderPricing::Cent, 400);
    CHECK(b - a == Approx(100 * 0.02).margin(1e-9));
}
