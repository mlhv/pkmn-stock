#include "pkmn_engine/execution.hpp"

#include <algorithm>
#include <cmath>
#include <optional>

namespace pkmn {

namespace {

// execution.py `_fill_buy`
std::optional<Fill> fill_buy(const Order& order, double market, const Portfolio& pf, Day day,
                             std::int64_t cap_left, std::int64_t used, const MarketView& mkt,
                             const CostModel& cm) {
    double mid = mkt.mid(order.asset);  // NaN when the asset has no quote today
    double low = mkt.low(order.asset);  // NaN when the asset has no quote today
    // A buy cannot execute below the cheapest current listing; `market` is
    // sales-derived and can sit under it (see CostModel::buy_base, which also
    // explains the impact_enabled gate and is the single definition of this
    // price — `cap_left` was tiered on the same number).
    double base = cm.buy_base(market, low);
    std::int64_t qty = std::min(order.quantity, cap_left);
    // afford: qty * base + shipping_per_line + walk-impact(qty) <= cash.
    // With impact ON, qty*base <= qty*market + impact(qty), so `base` is a
    // valid upper bound: it only changes how many times the decrement loop
    // below runs, never where it stops. With impact OFF the true constraint is
    // qty*market + shipping <= cash and a `base` clip would bind BELOW it;
    // hence the gate above.
    auto affordable =
        static_cast<std::int64_t>(std::floor((pf.cash - cm.shipping_per_line) / base));
    qty = std::min(qty, std::max<std::int64_t>(affordable, 0));
    double impact = cm.buy_impact(market, mid, low, qty, used);
    double cost = static_cast<double>(qty) * market + cm.shipping_per_line + impact;
    while (qty > 0 && cost > pf.cash) {
        --qty;
        impact = cm.buy_impact(market, mid, low, qty, used);
        cost = static_cast<double>(qty) * market + cm.shipping_per_line + impact;
    }
    if (qty <= 0) return std::nullopt;
    return Fill{day, order.asset, qty, market, cm.shipping_per_line, impact};
}

// execution.py `_fill_sell`
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
        // execution.py `execute` — NaN = didn't print (Python None); <= 0
        // defensive skip.
        if (std::isnan(px) || px <= 0.0 || order.quantity == 0) continue;
        std::int64_t used = 0;
        if (const auto* u = filled_today.find(order.asset)) used = *u;
        // execution.py: the depth cap is tiered on the price actually paid —
        // the floored base on a buy, the print on a sell (no floor there).
        double tier_price =
            order.quantity > 0 ? cm.buy_base(px, market.low(order.asset)) : px;
        std::int64_t cap_left = cm.max_daily_qty(tier_price) - used;
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
