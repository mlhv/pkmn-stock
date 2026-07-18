#include "pkmn_engine/strategies/dip_buyer.hpp"

#include <algorithm>
#include <cmath>

namespace pkmn {

std::vector<Order> DipBuyer::on_bar(const Context& ctx) {
    std::vector<Order> orders;

    // Sells first (dip_buyer.py:56-68).
    auto held = ctx.positions.entries();
    std::stable_sort(held.begin(), held.end(), [&](const auto& a, const auto& b) {
        return ctx.products.product_id[static_cast<std::size_t>(a.key)] <
               ctx.products.product_id[static_cast<std::size_t>(b.key)];
    });
    for (const auto& e : held) {
        const double* mark = ctx.marks.find(e.key);
        bool too_old = (ctx.today - e.value.opened_on) >= hold_days_;
        bool hit_target = mark != nullptr && *mark >= e.value.avg_cost * take_profit_;
        if (too_old || hit_target) orders.push_back(Order{e.key, -e.value.quantity});
    }

    std::int64_t open_slots = max_positions_ - (static_cast<std::int64_t>(ctx.positions.size()) -
                                                static_cast<std::int64_t>(orders.size()));
    if (open_slots <= 0) return orders;

    // Entries (py:74-97): singles whose last print at-or-before
    // window_start exists (the groupby membership condition).
    Day window_start = ctx.today - static_cast<Day>(dip_window_days_);
    struct Cand {
        double ret;
        AssetId asset;
        double mark;
    };
    std::vector<Cand> candidates;
    auto n = static_cast<AssetId>(ctx.products.n_assets());
    for (AssetId a = 0; a < n; ++a) {
        if (ctx.products.kind[static_cast<std::size_t>(a)] != 1) continue;  // singles
        auto past = ctx.market.last_price_at_or_before(a, window_start);
        if (!past.has_value()) continue;
        if (ctx.positions.contains(a) || *past <= 0.0) continue;
        const double* mark = ctx.marks.find(a);
        if (mark == nullptr || *mark < min_price_) continue;
        double ret = *mark / *past - 1.0;
        if (ret <= -dip_threshold_) candidates.push_back(Cand{ret, a, *mark});
    }

    // py:99 sort(key=(ret, product_id)) — deepest dip (most negative) first.
    std::sort(candidates.begin(), candidates.end(), [&](const Cand& x, const Cand& y) {
        if (x.ret != y.ret) return x.ret < y.ret;
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
