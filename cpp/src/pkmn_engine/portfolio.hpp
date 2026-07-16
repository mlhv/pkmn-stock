#pragma once

// Port of engine/portfolio.py (Positions, cash, average-cost P&L).

#include <cstddef>

#include "pkmn_engine/types.hpp"

namespace pkmn {

class Portfolio {
  public:
    Portfolio(double cash, std::size_t n_assets) : cash(cash), positions(n_assets) {}

    double cash;
    double realized_pnl = 0.0;
    InsertionMap<Position> positions;

    // portfolio.py:64-71 + Fill.__post_init__ validation (portfolio.py:34-40).
    void apply(const Fill& f);

    // portfolio.py:100-108. Sums in positions insertion order (Python dict
    // iteration order) — parity-relevant because float addition is not
    // associative. Throws std::out_of_range on a missing mark (KeyError).
    double equity(const InsertionMap<double>& marks) const;

  private:
    void buy_(const Fill& f);
    void sell_(const Fill& f);
};

}  // namespace pkmn
