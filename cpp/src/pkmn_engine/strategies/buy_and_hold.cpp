#include "pkmn_engine/strategies/buy_and_hold.hpp"

#include <algorithm>
#include <cmath>
#include <utility>

namespace pkmn {

std::vector<Order> BuyAndHold::on_bar(const Context& ctx) {
    // buy_and_hold.py:24-44. Python sorts all marks by product_id (stable,
    // ties in dict insertion order) then filters by kind; filter-then-
    // stable-sort commutes because both preserve relative order.
    if (entered_) return {};
    entered_ = true;

    std::vector<std::pair<AssetId, double>> universe;
    for (const auto& e : ctx.marks.entries()) {
        if (ctx.products.kind[static_cast<std::size_t>(e.key)] == kind_)
            universe.emplace_back(e.key, e.value);
    }
    std::stable_sort(universe.begin(), universe.end(), [&](const auto& a, const auto& b) {
        return ctx.products.product_id[static_cast<std::size_t>(a.first)] <
               ctx.products.product_id[static_cast<std::size_t>(b.first)];
    });
    if (universe.empty()) return {};

    double budget_per_asset = ctx.cash / static_cast<double>(universe.size());
    std::vector<Order> orders;
    for (const auto& [asset, price] : universe) {
        auto qty = static_cast<std::int64_t>(std::floor(budget_per_asset / price));
        if (qty > 0) orders.push_back(Order{asset, qty});
    }
    return orders;
}

}  // namespace pkmn
