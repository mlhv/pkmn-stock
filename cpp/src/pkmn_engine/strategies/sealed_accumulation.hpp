#pragma once

// Port of strategies/sealed_accumulation.py.

#include <cstdint>
#include <vector>

#include "pkmn_engine/strategy.hpp"

namespace pkmn {

class SealedAccumulation final : public Strategy {
  public:
    SealedAccumulation(std::int64_t min_age_days, std::int64_t max_age_days,
                       double min_drawdown, double take_profit, std::int64_t max_positions,
                       double budget_frac)
        : min_age_days_(min_age_days),
          max_age_days_(max_age_days),
          min_drawdown_(min_drawdown),
          take_profit_(take_profit),
          max_positions_(max_positions),
          budget_frac_(budget_frac) {}

    std::vector<Order> on_bar(const Context& ctx) override;

  private:
    std::int64_t min_age_days_;
    std::int64_t max_age_days_;
    double min_drawdown_;
    double take_profit_;
    std::int64_t max_positions_;
    double budget_frac_;
};

}  // namespace pkmn
