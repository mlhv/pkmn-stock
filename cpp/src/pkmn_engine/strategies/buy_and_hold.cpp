#include "pkmn_engine/strategies/buy_and_hold.hpp"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <numeric>
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

// screen.py: infinity (never NaN) when mid or low is missing, or mid <= 0,
// OR when the relative spread is non-positive. !(mid > 0) catches NaN and
// non-positive in one test. low == mid means a single listing (no depth) and
// low > mid is incoherent; both are suspect quotes, not the tightest ones, so
// they sort last with the undefined case rather than first. See screen.py's
// docstring for the product that made this concrete.
double relative_spread(const HistoricalQuote& q) {
    if (!(q.mid > 0.0) || std::isnan(q.low)) return kInf;
    double spread = (q.mid - q.low) / q.mid;
    return spread > 0.0 ? spread : kInf;
}

}  // namespace

std::vector<Order> BuyAndHold::on_bar(const Context& ctx) {
    // buy_and_hold.py `_universe` + `on_bar`. Python iterates ctx.marks (insertion order),
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
    // n_assets_ <= 0 means "hold nothing"; buy_and_hold.py guards identically
    // (its list slice would otherwise read a negative n_assets as "all but the
    // last N"). resolve_params pins [1, 10000], so only a direct constructor
    // call gets here.
    if (universe.empty() || n_assets_ <= 0) return {};
    if (static_cast<std::int64_t>(universe.size()) > n_assets_)
        universe.resize(static_cast<std::size_t>(n_assets_));

    // buy_and_hold.py `_affordable`: keep the largest k such that the k
    // CHEAPEST screened candidates each cost <= cash / k, in screen order.
    // Without it the deficit sweep below funds the one asset it cannot afford
    // out of everybody else's share (50% of the account in one box, measured).
    // The predicate is monotone in k, so the ascending scan finds max k.
    std::vector<std::size_t> by_price(universe.size());
    std::iota(by_price.begin(), by_price.end(), std::size_t{0});
    // Stable, price only: equal prices keep screen order, exactly Python's
    // sorted(key=(price, index)).
    std::stable_sort(by_price.begin(), by_price.end(),
                     [&](std::size_t a, std::size_t b) {
                         return universe[a].price < universe[b].price;
                     });
    std::size_t keep = 0;
    for (std::size_t rank = 1; rank <= by_price.size(); ++rank) {
        if (universe[by_price[rank - 1]].price > ctx.cash / static_cast<double>(rank)) break;
        keep = rank;
    }
    if (keep == 0) return {};
    by_price.resize(keep);
    std::sort(by_price.begin(), by_price.end());  // back to screen order
    std::vector<Candidate> affordable;
    affordable.reserve(keep);
    for (std::size_t i : by_price) affordable.push_back(universe[i]);
    universe.swap(affordable);

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
    // earliest in screen order, which is itself AssetId-tie-broken. `deficit`
    // cannot saturate at `target` any more: the affordability filter above
    // guarantees every candidate here holds >= 1 unit already.
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
