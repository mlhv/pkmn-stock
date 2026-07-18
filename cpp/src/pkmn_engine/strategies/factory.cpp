#include "pkmn_engine/strategies/factory.hpp"

#include <algorithm>
#include <cmath>
#include <stdexcept>
#include <vector>

#include "pkmn_engine/strategies/buy_and_hold.hpp"
#include "pkmn_engine/strategies/cost_aware_reversion.hpp"
#include "pkmn_engine/strategies/dip_buyer.hpp"
#include "pkmn_engine/strategies/momentum.hpp"
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

namespace {

// Rejects any ParamMap key the selected strategy's factory branch doesn't
// read. A renamed/added tunable in the Python registry that this file
// doesn't consume would otherwise silently run on defaults -- divergence
// with no error. Throws std::invalid_argument (-> Python ValueError) naming
// both the stray key and the strategy.
void check_known_params(const ParamMap& params, const std::string& strategy_name,
                        const std::vector<std::string>& allowed) {
    for (const auto& [key, value] : params) {
        (void)value;
        if (std::find(allowed.begin(), allowed.end(), key) == allowed.end()) {
            throw std::invalid_argument("unknown param '" + key + "' for strategy '" +
                                        strategy_name + "'");
        }
    }
}

}  // namespace

std::unique_ptr<Strategy> make_strategy(const std::string& name, const ParamMap& params,
                                        std::int8_t universe_kind) {
    if (name == "buy-and-hold") {
        check_known_params(params, name, {});
        return std::make_unique<BuyAndHold>(universe_kind);
    }
    if (name == "sealed-accumulation") {
        check_known_params(params, name,
                           {"min_age_days", "max_age_days", "min_drawdown", "take_profit",
                            "max_positions", "budget_frac"});
        return std::make_unique<SealedAccumulation>(
            iparam(params, "min_age_days", 60), iparam(params, "max_age_days", 365),
            param(params, "min_drawdown", 0.25), param(params, "take_profit", 1.5),
            iparam(params, "max_positions", 10), param(params, "budget_frac", 0.10));
    }
    if (name == "dip-buyer") {
        check_known_params(params, name,
                           {"dip_window_days", "dip_threshold", "hold_days", "take_profit",
                            "max_positions", "budget_frac", "min_price"});
        return std::make_unique<DipBuyer>(
            iparam(params, "dip_window_days", 7), param(params, "dip_threshold", 0.30),
            iparam(params, "hold_days", 30), param(params, "take_profit", 1.25),
            iparam(params, "max_positions", 10), param(params, "budget_frac", 0.10),
            param(params, "min_price", 3.0));
    }
    if (name == "xs-momentum") {
        check_known_params(params, name,
                           {"lookback_days", "top_n", "rebalance_days", "min_price"});
        return std::make_unique<CrossSectionalMomentum>(
            iparam(params, "lookback_days", 60), iparam(params, "top_n", 10),
            iparam(params, "rebalance_days", 30), param(params, "min_price", 3.0));
    }
    if (name == "cost-aware-reversion") {
        check_known_params(params, name,
                           {"dip_window_days", "dip_threshold", "min_edge", "take_profit",
                            "max_hold_days", "max_positions", "budget_frac", "min_price"});
        return std::make_unique<CostAwareReversion>(
            iparam(params, "dip_window_days", 30), param(params, "dip_threshold", 0.25),
            param(params, "min_edge", 0.05), param(params, "take_profit", 1.25),
            iparam(params, "max_hold_days", 120), iparam(params, "max_positions", 10),
            param(params, "budget_frac", 0.10), param(params, "min_price", 3.0),
            0.1275, 1.0);  // hurdle costs: CostModel() defaults, like the registry
    }
    throw std::invalid_argument("unknown native strategy: " + name);
}

}  // namespace pkmn
