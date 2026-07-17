#include "pkmn_engine/strategies/momentum.hpp"

#include <algorithm>
#include <cmath>
#include <stdexcept>

#include "pkmn_engine/numeric.hpp"

namespace pkmn {

bool CrossSectionalMomentum::rebalance_due_(const Context& ctx) const {
    // momentum.py:55-67: flat portfolio evaluates every bar; else newest
    // opened_on approximates the last rebalance buy.
    if (ctx.positions.size() == 0) return true;
    Day newest = kNullDay;
    for (const auto& e : ctx.positions.entries()) newest = std::max(newest, e.value.opened_on);
    return (ctx.today - newest) >= rebalance_days_;
}

std::vector<Order> CrossSectionalMomentum::on_bar(const Context& ctx) {
    if (!rebalance_due_(ctx)) return {};

    // momentum.py:73-92: rank singles by trailing return.
    Day window_start = ctx.today - static_cast<Day>(lookback_days_);
    struct Mom {
        double ret;
        AssetId asset;
        double mark;
    };
    std::vector<Mom> momentum;
    auto n = static_cast<AssetId>(ctx.products.n_assets());
    for (AssetId a = 0; a < n; ++a) {
        if (ctx.products.kind[static_cast<std::size_t>(a)] != 1) continue;
        auto past = ctx.market.last_price_at_or_before(a, window_start);
        if (!past.has_value()) continue;  // groupby membership
        const double* mark = ctx.marks.find(a);
        if (mark == nullptr || *mark < min_price_ || *past <= 0.0) continue;
        momentum.push_back(Mom{*mark / *past - 1.0, a, *mark});
    }
    // py:91 sort(key=(-ret, product_id)); asset id closes exact ties.
    std::sort(momentum.begin(), momentum.end(), [&](const Mom& x, const Mom& y) {
        if (x.ret != y.ret) return x.ret > y.ret;
        auto px = ctx.products.product_id[static_cast<std::size_t>(x.asset)];
        auto py_ = ctx.products.product_id[static_cast<std::size_t>(y.asset)];
        if (px != py_) return px < py_;
        return x.asset < y.asset;
    });
    // target dict: insertion order = ranking order (py:92)
    InsertionMap<double> target(ctx.products.n_assets());
    auto keep = std::min<std::size_t>(momentum.size(), static_cast<std::size_t>(top_n_));
    for (std::size_t i = 0; i < keep; ++i) target.set(momentum[i].asset, momentum[i].mark);

    std::vector<Order> orders;
    // Sells first (py:95-99): everything not in the target.
    auto held = ctx.positions.entries();
    std::stable_sort(held.begin(), held.end(), [&](const auto& a, const auto& b) {
        return ctx.products.product_id[static_cast<std::size_t>(a.key)] <
               ctx.products.product_id[static_cast<std::size_t>(b.key)];
    });
    for (const auto& e : held) {
        if (!target.contains(e.key)) orders.push_back(Order{e.key, -e.value.quantity});
    }

    if (target.size() == 0) return orders;

    // py:104-107: equity from ctx marks; held asset without a mark is a bug.
    // position_value_sum uses Neumaier compensated summation, matching
    // Python's sum() exactly (see numeric.hpp) — sum first, cash added
    // after, same as `ctx.cash + sum(...)`.
    double equity = ctx.cash + position_value_sum(ctx.positions, ctx.marks);
    double per_name = equity / static_cast<double>(target.size());

    // py:110-115: buys sorted by product_id (stable over target insertion
    // order = ranking order).
    auto targets = target.entries();
    std::stable_sort(targets.begin(), targets.end(), [&](const auto& a, const auto& b) {
        return ctx.products.product_id[static_cast<std::size_t>(a.key)] <
               ctx.products.product_id[static_cast<std::size_t>(b.key)];
    });
    for (const auto& t : targets) {
        const Position* held_pos = ctx.positions.find(t.key);
        double held_value =
            held_pos ? static_cast<double>(held_pos->quantity) * t.value : 0.0;
        auto qty = static_cast<std::int64_t>(std::floor((per_name - held_value) / t.value));
        if (qty > 0) orders.push_back(Order{t.key, qty});
    }
    return orders;
}

}  // namespace pkmn
