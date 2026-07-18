#pragma once

// Port of engine/costs.py. Every arithmetic expression mirrors the Python
// operation order exactly (bit-for-bit parity contract).

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

    // costs.py:55-61 — strict <: a price exactly at a threshold falls to
    // the NEXT tier.
    std::int64_t max_daily_qty(double market) const {
        for (const auto& [threshold, qty] : liquidity_tiers) {
            if (market < threshold) return qty;
        }
        return fallback_max_qty;
    }

    // costs.py:63-72 — mid NaN = Python None (missing).
    double buy_impact(double market, double mid, std::int64_t qty, std::int64_t used) const {
        return impact_(market, mid, market, qty, used);
    }

    // costs.py:74-76
    double sell_impact(double market, double low, std::int64_t qty, std::int64_t used) const {
        return impact_(market, market, low, qty, used);
    }

  private:
    // costs.py:78-87. Python: spread * qty * (2 * used + qty) / (2 * q_cap)
    // — evaluated left-to-right; (2*used + qty) and (2*q_cap) are exact
    // int-to-double conversions at these magnitudes.
    double impact_(double market, double upper, double lower, std::int64_t qty,
                   std::int64_t used) const {
        if (!impact_enabled || qty <= 0 || std::isnan(upper) || std::isnan(lower)) return 0.0;
        double spread = upper - lower;
        if (spread <= 0.0) return 0.0;
        std::int64_t q_cap = max_daily_qty(market);
        return spread * static_cast<double>(qty) * static_cast<double>(2 * used + qty) /
               static_cast<double>(2 * q_cap);
    }
};

}  // namespace pkmn
