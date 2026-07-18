#include "pkmn_engine/execution.hpp"

#include <algorithm>
#include <cmath>
#include <optional>

namespace pkmn {

namespace {

// execution.py:74-104
std::optional<Fill> fill_buy(const Order& order, double market, const Portfolio& pf, Day day,
                             std::int64_t cap_left, std::int64_t used, const MarketView& mkt,
                             const CostModel& cm) {
    std::int64_t qty = std::min(order.quantity, cap_left);
    // afford: qty * market + shipping_per_line + impact(qty) <= cash
    auto affordable =
        static_cast<std::int64_t>(std::floor((pf.cash - cm.shipping_per_line) / market));
    qty = std::min(qty, std::max<std::int64_t>(affordable, 0));
    double mid = mkt.mid(order.asset);  // NaN when the asset has no quote today
    double impact = cm.buy_impact(market, mid, qty, used);
    double cost = static_cast<double>(qty) * market + cm.shipping_per_line + impact;
    while (qty > 0 && cost > pf.cash) {
        --qty;
        impact = cm.buy_impact(market, mid, qty, used);
        cost = static_cast<double>(qty) * market + cm.shipping_per_line + impact;
    }
    if (qty <= 0) return std::nullopt;
    return Fill{day, order.asset, qty, market, cm.shipping_per_line, impact};
}

// execution.py:106-132
std::optional<Fill> fill_sell(const Order& order, double market, const Portfolio& pf, Day day,
                              std::int64_t cap_left, std::int64_t used, const MarketView& mkt,
                              const CostModel& cm) {
    const Position* pos = pf.positions.find(order.asset);
    if (pos == nullptr) return std::nullopt;
    std::int64_t qty = std::min({-order.quantity, pos->quantity, cap_left});
    if (qty <= 0) return std::nullopt;
    // Python: qty * market * fee_rate + shipping — left-to-right.
    double fees =
        static_cast<double>(qty) * market * cm.fee_rate + cm.shipping_per_line;
    double low = mkt.low(order.asset);
    double impact = cm.sell_impact(market, low, qty, used);
    return Fill{day, order.asset, -qty, market, fees, impact};
}

}  // namespace

std::vector<Fill> execute(const std::vector<Order>& orders, const MarketView& market,
                          Portfolio& portfolio, Day day, const CostModel& cm) {
    std::vector<Fill> fills;
    InsertionMap<std::int64_t> filled_today(market.n_assets());
    for (const auto& order : orders) {
        double px = market.price(order.asset);
        // execution.py:53-57 — NaN = didn't print (Python None); <= 0
        // defensive skip.
        if (std::isnan(px) || px <= 0.0 || order.quantity == 0) continue;
        std::int64_t used = 0;
        if (const auto* u = filled_today.find(order.asset)) used = *u;
        std::int64_t cap_left = cm.max_daily_qty(px) - used;
        if (cap_left <= 0) continue;
        auto fill = order.quantity > 0
                        ? fill_buy(order, px, portfolio, day, cap_left, used, market, cm)
                        : fill_sell(order, px, portfolio, day, cap_left, used, market, cm);
        if (fill.has_value()) {
            portfolio.apply(*fill);
            fills.push_back(*fill);
            std::int64_t filled = fill->quantity < 0 ? -fill->quantity : fill->quantity;
            filled_today.set(order.asset, used + filled);
        }
    }
    return fills;
}

}  // namespace pkmn
