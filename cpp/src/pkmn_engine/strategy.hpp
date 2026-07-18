#pragma once

// Port of engine/strategy.py: read-only Context in, Orders out (T+1 fills).
// C++ strategies receive const refs (Python copies for safety; const
// enforces the same contract at compile time).

#include <vector>

#include "pkmn_engine/market.hpp"
#include "pkmn_engine/types.hpp"

namespace pkmn {

struct Context {
    Day today;
    const MarketView& market;  // history via bounded queries (<= today)
    const ProductTable& products;
    const InsertionMap<Position>& positions;
    double cash;
    const InsertionMap<double>& marks;
};

class Strategy {
  public:
    virtual ~Strategy() = default;
    virtual std::vector<Order> on_bar(const Context& ctx) = 0;
    virtual void reset() {}
};

}  // namespace pkmn
