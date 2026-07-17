#include "pkmn_engine/strategies/factory.hpp"

#include <cmath>
#include <stdexcept>

#include "pkmn_engine/strategies/buy_and_hold.hpp"

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
    (void)params;
    if (name == "buy-and-hold") return std::make_unique<BuyAndHold>(universe_kind);
    throw std::invalid_argument("unknown native strategy: " + name);
}

}  // namespace pkmn
