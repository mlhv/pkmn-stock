#include "pkmn_engine/strategies/factory.hpp"

#include <cmath>
#include <stdexcept>

#include "pkmn_engine/strategies/buy_and_hold.hpp"
#include "pkmn_engine/strategies/dip_buyer.hpp"
#include "pkmn_engine/strategies/sealed_accumulation.hpp"

namespace pkmn {

double param(const ParamMap& p, const std::string& key, double dflt) {
    auto it = p.find(key);
    return it == p.end() ? dflt : it->second;
}

std::int64_t iparam(const ParamMap& p, const std::string& key, std::int64_t dflt) {
    auto it = p.find(key);
    // optuna int params arrive as exact doubles; llround is the safe cast.
    return it == p.end() ? dflt : static_cast<std::int64_t>(std::llround(it->second));
}

std::unique_ptr<Strategy> make_strategy(const std::string& name, const ParamMap& params,
                                        std::int8_t universe_kind) {
    if (name == "buy-and-hold") return std::make_unique<BuyAndHold>(universe_kind);
    if (name == "sealed-accumulation") {
        return std::make_unique<SealedAccumulation>(
            iparam(params, "min_age_days", 60), iparam(params, "max_age_days", 365),
            param(params, "min_drawdown", 0.25), param(params, "take_profit", 1.5),
            iparam(params, "max_positions", 10), param(params, "budget_frac", 0.10));
    }
    if (name == "dip-buyer") {
        return std::make_unique<DipBuyer>(
            iparam(params, "dip_window_days", 7), param(params, "dip_threshold", 0.30),
            iparam(params, "hold_days", 30), param(params, "take_profit", 1.25),
            iparam(params, "max_positions", 10), param(params, "budget_frac", 0.10),
            param(params, "min_price", 3.0));
    }
    throw std::invalid_argument("unknown native strategy: " + name);
}

}  // namespace pkmn
