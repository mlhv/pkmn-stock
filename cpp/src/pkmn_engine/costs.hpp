#pragma once

// Port of engine/costs.py. Every arithmetic expression mirrors the Python
// operation order exactly (bit-for-bit parity contract).

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <utility>
#include <vector>

namespace pkmn {

struct CostModel {
    double fee_rate = 0.1275;
    double shipping_per_line = 1.0;
    // (price threshold, units per asset per day); strict < per tier.
    std::vector<std::pair<double, std::int64_t>> liquidity_tiers = {
        {5.0, 20}, {50.0, 8}, {200.0, 3}};
    std::int64_t fallback_max_qty = 1;
    bool impact_enabled = false;

    // costs.py `max_daily_qty` — strict <: a price exactly at a threshold
    // falls to the NEXT tier. Takes the price TRANSACTED at (buy_base on a
    // buy), never automatically the print.
    std::int64_t max_daily_qty(double price) const {
        for (const auto& [threshold, qty] : liquidity_tiers) {
            if (price < threshold) return qty;
        }
        return fallback_max_qty;
    }

    // costs.py `buy_base` — the price a buy actually executes at. NaN low =
    // Python None (missing). Gated on impact_enabled because that is where
    // the premium is booked; with the model off the floor must not size, cap
    // or constrain anything. Single definition: the executor sizes off it,
    // buy_impact walks from it, and it picks the buy-side liquidity tier.
    double buy_base(double market, double low) const {
        if (!impact_enabled || std::isnan(low)) return market;
        return std::max(market, low);
    }

    // costs.py `buy_impact` — mid/low NaN = Python None (missing). `market`
    // is sales-derived and `low` is the cheapest current listing, so
    // market < low is ordinary: the fill starts at buy_base and the shortfall
    // is charged as impact, with the walk to `mid` starting from there at the
    // base's depth tier.
    double buy_impact(double market, double mid, double low, std::int64_t qty,
                      std::int64_t used) const {
        if (!impact_enabled || qty <= 0) return 0.0;
        double base = buy_base(market, low);
        double premium = (base - market) * static_cast<double>(qty);
        return premium + impact_(base, mid, base, qty, used);
    }

    // costs.py `sell_impact`. Deliberately NOT symmetric with buy_impact:
    // tcgcsv publishes no bid series to floor a sell against, so the price
    // transacted at IS the print and the tier comes off `market`.
    double sell_impact(double market, double low, std::int64_t qty, std::int64_t used) const {
        return impact_(market, market, low, qty, used);
    }

  private:
    // costs.py `_impact`. Python: spread * qty * (2 * used + qty) / (2 * q_cap)
    // — evaluated left-to-right; (2*used + qty) and (2*q_cap) are exact
    // int-to-double conversions at these magnitudes. `tier_price` is the
    // price transacted at, not the print.
    double impact_(double tier_price, double upper, double lower, std::int64_t qty,
                   std::int64_t used) const {
        if (!impact_enabled || qty <= 0 || std::isnan(upper) || std::isnan(lower)) return 0.0;
        double spread = upper - lower;
        if (spread <= 0.0) return 0.0;
        std::int64_t q_cap = max_daily_qty(tier_price);
        return spread * static_cast<double>(qty) * static_cast<double>(2 * used + qty) /
               static_cast<double>(2 * q_cap);
    }
};

}  // namespace pkmn
