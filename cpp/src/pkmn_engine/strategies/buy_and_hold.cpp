#include "pkmn_engine/strategies/buy_and_hold.hpp"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <vector>

namespace pkmn {

namespace {

constexpr double kInf = std::numeric_limits<double>::infinity();

// One screened candidate. Mirrors the (asset, price) pair plus the two
// research/screen.py sort keys the Python side looks up from rank_assets.
struct Candidate {
    AssetId asset;
    double price;
    std::int64_t n_prints;
    double spread;
};

// screen.py: infinity (never NaN) when mid or low is missing, or mid <= 0.
// !(mid > 0) catches NaN and non-positive in one test.
double relative_spread(const HistoricalQuote& q) {
    if (!(q.mid > 0.0) || std::isnan(q.low)) return kInf;
    return (q.mid - q.low) / q.mid;
}

}  // namespace

std::vector<Order> BuyAndHold::on_bar(const Context& ctx) {
    // buy_and_hold.py:66-100. Python iterates ctx.marks (insertion order),
    // filters by kind and price > 0, then sorts on the screen key; the C++
    // marks map iterates in the same insertion order, and filter-then-sort
    // commutes with sort-then-filter because both preserve relative order.
    if (entered_) return {};
    entered_ = true;

    std::vector<Candidate> universe;
    for (const auto& e : ctx.marks.entries()) {
        if (ctx.products.kind[static_cast<std::size_t>(e.key)] != kind_) continue;
        if (!(e.value > 0.0)) continue;
        auto n_prints = ctx.market.print_count_at_or_before(e.key, ctx.today);
        auto spread = relative_spread(ctx.market.quote_at_or_before(e.key, ctx.today));
        universe.push_back(Candidate{e.key, e.value, n_prints, spread});
    }
    // n_prints desc, spread asc, then AssetId asc. types.hpp:11-13: ids are
    // assigned in (product_id, sub_type) order, so one integer compare IS the
    // Python key's trailing (product_id, sub_type) pair.
    std::stable_sort(universe.begin(), universe.end(), [](const Candidate& a, const Candidate& b) {
        if (a.n_prints != b.n_prints) return a.n_prints > b.n_prints;
        if (a.spread != b.spread) return a.spread < b.spread;
        return a.asset < b.asset;
    });
    // n_assets_ is validated >= 1 upstream (research/backtest_run.py, factory.cpp).
    if (universe.empty() || n_assets_ <= 0) return {};
    if (static_cast<std::int64_t>(universe.size()) > n_assets_)
        universe.resize(static_cast<std::size_t>(n_assets_));

    double target = ctx.cash / static_cast<double>(universe.size());
    std::vector<std::int64_t> qty(universe.size());
    double spent = 0.0;
    for (std::size_t i = 0; i < universe.size(); ++i) {
        qty[i] = static_cast<std::int64_t>(std::floor(target / universe[i].price));
        spent += static_cast<double>(qty[i]) * universe[i].price;
    }
    double cash = ctx.cash - spent;

    // Deficit sweep: buy one unit at a time of whichever screened asset is
    // furthest below its target dollar weight. Strict > means ties go to the
    // earliest in screen order, which is itself AssetId-tie-broken.
    while (true) {
        std::ptrdiff_t best = -1;
        double best_deficit = 0.0;
        for (std::size_t i = 0; i < universe.size(); ++i) {
            if (universe[i].price > cash) continue;
            double deficit = target - static_cast<double>(qty[i]) * universe[i].price;
            if (deficit > best_deficit) {
                best_deficit = deficit;
                best = static_cast<std::ptrdiff_t>(i);
            }
        }
        if (best < 0) break;
        auto b = static_cast<std::size_t>(best);
        qty[b] += 1;
        cash -= universe[b].price;
    }

    std::vector<Order> orders;
    for (std::size_t i = 0; i < universe.size(); ++i) {
        if (qty[i] > 0) orders.push_back(Order{universe[i].asset, qty[i]});
    }
    return orders;
}

}  // namespace pkmn
