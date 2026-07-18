#pragma once

// Port of strategies/momentum.py. Stateless: rebalance clock derived from
// the newest Position.opened_on.

#include <cstdint>
#include <vector>

#include "pkmn_engine/strategy.hpp"

namespace pkmn {

class CrossSectionalMomentum final : public Strategy {
  public:
    CrossSectionalMomentum(std::int64_t lookback_days, std::int64_t top_n,
                           std::int64_t rebalance_days, double min_price)
        : lookback_days_(lookback_days),
          top_n_(top_n),
          rebalance_days_(rebalance_days),
          min_price_(min_price) {}

    std::vector<Order> on_bar(const Context& ctx) override;

  private:
    bool rebalance_due_(const Context& ctx) const;

    std::int64_t lookback_days_;
    std::int64_t top_n_;
    std::int64_t rebalance_days_;
    double min_price_;
};

}  // namespace pkmn
