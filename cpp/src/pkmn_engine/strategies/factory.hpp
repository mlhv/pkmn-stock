#pragma once

// Strategy construction keyed by registry name + optuna params map.
// Missing params fall back to the Python constructor defaults.

#include <cstdint>
#include <map>
#include <memory>
#include <string>

#include "pkmn_engine/strategy.hpp"

namespace pkmn {

using ParamMap = std::map<std::string, double>;

// Throws std::invalid_argument for unknown names (-> Python ValueError).
// universe_kind (0 sealed, 1 single) is used only by "buy-and-hold".
std::unique_ptr<Strategy> make_strategy(const std::string& name, const ParamMap& params,
                                        std::int8_t universe_kind);

// Helpers shared by strategy constructors.
double param(const ParamMap& p, const std::string& key, double dflt);
std::int64_t iparam(const ParamMap& p, const std::string& key, std::int64_t dflt);

}  // namespace pkmn
