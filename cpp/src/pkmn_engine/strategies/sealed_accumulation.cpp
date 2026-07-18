#include "pkmn_engine/strategies/sealed_accumulation.hpp"

#include <algorithm>
#include <cmath>

namespace pkmn {

std::vector<Order> SealedAccumulation::on_bar(const Context& ctx) {
    std::vector<Order> orders;

    // Sells first (sealed_accumulation.py:43-46): positions in insertion
    // order, stable-sorted by product_id — Python's sorted() over dict items.
    auto held = ctx.positions.entries();
    std::stable_sort(held.begin(), held.end(), [&](const auto& a, const auto& b) {
        return ctx.products.product_id[static_cast<std::size_t>(a.key)] <
               ctx.products.product_id[static_cast<std::size_t>(b.key)];
    });
    for (const auto& e : held) {
        const double* mark = ctx.marks.find(e.key);
        if (mark != nullptr && *mark >= e.value.avg_cost * take_profit_)
            orders.push_back(Order{e.key, -e.value.quantity});
    }

    // sealed_accumulation.py:48-50
    std::int64_t open_slots = max_positions_ - (static_cast<std::int64_t>(ctx.positions.size()) -
                                                static_cast<std::int64_t>(orders.size()));
    if (open_slots <= 0) return orders;

    // Candidate scan (py:52-80). Iterating asset_id ascending visits assets
    // in (product_id, sub_type) order; the deterministic tie-break the sort
    // below completes. peak_until = groupby market.max over history<=today.
    struct Cand {
        double drawdown;
        AssetId asset;
        double mark;
    };
    std::vector<Cand> candidates;
    auto n = static_cast<AssetId>(ctx.products.n_assets());
    for (AssetId a = 0; a < n; ++a) {
        auto ai = static_cast<std::size_t>(a);
        if (ctx.products.kind[ai] != 0) continue;  // sealed only
        Day rel = ctx.products.released_on[ai];
        if (rel == kNullDay) continue;  // Python: null comparison is false
        if (!(rel <= ctx.today - min_age_days_ && rel >= ctx.today - max_age_days_)) continue;
        auto peak = ctx.market.peak_until(a, ctx.today);
        if (!peak.has_value()) continue;  // no history row => not in groupby
        if (ctx.positions.contains(a) || *peak <= 0.0) continue;
        const double* mark = ctx.marks.find(a);
        if (mark == nullptr) continue;
        double drawdown = 1.0 - *mark / *peak;
        if (drawdown >= min_drawdown_) candidates.push_back(Cand{drawdown, a, *mark});
    }

    // py:83 sort(key=(-drawdown, product_id)); asset id closes exact ties.
    std::sort(candidates.begin(), candidates.end(), [&](const Cand& x, const Cand& y) {
        if (x.drawdown != y.drawdown) return x.drawdown > y.drawdown;
        auto px = ctx.products.product_id[static_cast<std::size_t>(x.asset)];
        auto py_ = ctx.products.product_id[static_cast<std::size_t>(y.asset)];
        if (px != py_) return px < py_;
        return x.asset < y.asset;
    });

    // py:84-91: qty>0 filter happens BEFORE the open_slots cutoff.
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
