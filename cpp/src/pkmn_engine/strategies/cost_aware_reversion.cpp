#include "pkmn_engine/strategies/cost_aware_reversion.hpp"

#include <algorithm>
#include <cmath>

namespace pkmn {

std::vector<Order> CostAwareReversion::on_bar(const Context& ctx) {
    std::vector<Order> orders;

    // Sells first (cost_aware_reversion.py:57-69).
    auto held = ctx.positions.entries();
    std::stable_sort(held.begin(), held.end(), [&](const auto& a, const auto& b) {
        return ctx.products.product_id[static_cast<std::size_t>(a.key)] <
               ctx.products.product_id[static_cast<std::size_t>(b.key)];
    });
    for (const auto& e : held) {
        const double* mark = ctx.marks.find(e.key);
        bool too_old = (ctx.today - e.value.opened_on) >= max_hold_days_;
        bool hit_target = mark != nullptr && *mark >= e.value.avg_cost * take_profit_;
        if (too_old || hit_target) orders.push_back(Order{e.key, -e.value.quantity});
    }

    std::int64_t open_slots = max_positions_ - (static_cast<std::int64_t>(ctx.positions.size()) -
                                                static_cast<std::int64_t>(orders.size()));
    if (open_slots <= 0) return orders;

    // Entries (py:75-97): ALL assets (no kind filter), window high over
    // [window_start, today].
    Day window_start = ctx.today - static_cast<Day>(dip_window_days_);
    struct Cand {
        double neg_dip;  // Python stores -dip as the sort key
        AssetId asset;
        double mark;
    };
    std::vector<Cand> candidates;
    auto n = static_cast<AssetId>(ctx.products.n_assets());
    for (AssetId a = 0; a < n; ++a) {
        auto high = ctx.market.max_in_window(a, window_start, ctx.today);
        if (!high.has_value()) continue;  // groupby membership
        if (ctx.positions.contains(a)) continue;
        const double* mark = ctx.marks.find(a);
        if (mark == nullptr || *mark < min_price_ || *high <= 0.0) continue;
        double dip = 1.0 - *mark / *high;
        if (dip < dip_threshold_) continue;
        double rebound = *high / *mark - 1.0;
        // py:94: fee_rate + 2 * shipping / mark (left-to-right precedence)
        double hurdle = fee_rate_ + 2.0 * shipping_per_line_ / *mark;
        if (rebound < hurdle + min_edge_) continue;
        candidates.push_back(Cand{-dip, a, *mark});
    }

    // py:99 sort(key=(-dip, product_id)) ascending.
    std::sort(candidates.begin(), candidates.end(), [&](const Cand& x, const Cand& y) {
        if (x.neg_dip != y.neg_dip) return x.neg_dip < y.neg_dip;
        auto px = ctx.products.product_id[static_cast<std::size_t>(x.asset)];
        auto py_ = ctx.products.product_id[static_cast<std::size_t>(y.asset)];
        if (px != py_) return px < py_;
        return x.asset < y.asset;
    });

    double budget = ctx.cash * budget_frac_;
    std::int64_t taken = 0;
    for (const auto& c : candidates) {
        if (taken >= open_slots) break;
        auto qty = static_cast<std::int64_t>(std::floor(budget / c.mark));
        if (qty > 0) {
            orders.push_back(Order{c.asset, qty});
            ++taken;
        }
    }
    return orders;
}

}  // namespace pkmn
