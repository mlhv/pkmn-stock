#pragma once

// Port of engine/portfolio.py (Positions, cash, average-cost P&L).

#include <cstddef>
#include <vector>

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

    // Install pre-existing holdings before bar one (Plan 2a). Positions are
    // added in list order (insertion order is parity-relevant for equity()).
    // Touches neither cash nor realized_pnl. Throws std::invalid_argument on
    // quantity <= 0, avg_cost < 0, or a duplicate asset.
    void seed(const std::vector<SeedPosition>& holdings);

    // portfolio.py:100-108. Sums in positions insertion order (Python dict
    // iteration order) — parity-relevant because float addition is not
    // associative. Throws std::out_of_range on a missing mark (KeyError).
    double equity(const InsertionMap<double>& marks) const;

  private:
    void buy_(const Fill& f);
    void sell_(const Fill& f);
};

}  // namespace pkmn
