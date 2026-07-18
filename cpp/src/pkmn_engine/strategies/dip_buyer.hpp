#pragma once

// Port of strategies/dip_buyer.py. Stateless: exit timing from
// Position.opened_on (always set by engine fills).

#include <cstdint>
#include <vector>

#include "pkmn_engine/strategy.hpp"

namespace pkmn {

class DipBuyer final : public Strategy {
  public:
    DipBuyer(std::int64_t dip_window_days, double dip_threshold, std::int64_t hold_days,
             double take_profit, std::int64_t max_positions, double budget_frac,
             double min_price)
        : dip_window_days_(dip_window_days),
          dip_threshold_(dip_threshold),
          hold_days_(hold_days),
          take_profit_(take_profit),
          max_positions_(max_positions),
          budget_frac_(budget_frac),
          min_price_(min_price) {}

    std::vector<Order> on_bar(const Context& ctx) override;

  private:
    std::int64_t dip_window_days_;
    double dip_threshold_;
    std::int64_t hold_days_;
    double take_profit_;
    std::int64_t max_positions_;
    double budget_frac_;
    double min_price_;
};

}  // namespace pkmn
