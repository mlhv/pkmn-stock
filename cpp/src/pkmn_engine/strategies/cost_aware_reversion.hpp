#pragma once

// Port of strategies/cost_aware_reversion.py. The fee/shipping hurdle uses
// the strategy's OWN cost assumptions (Python: CostModel() defaults),
// independent of the engine's cost model — mirrored here as plain fields.

#include <cstdint>
#include <vector>

#include "pkmn_engine/strategy.hpp"

namespace pkmn {

class CostAwareReversion final : public Strategy {
  public:
    CostAwareReversion(std::int64_t dip_window_days, double dip_threshold, double min_edge,
                       double take_profit, std::int64_t max_hold_days,
                       std::int64_t max_positions, double budget_frac, double min_price,
                       double fee_rate, double shipping_per_line)
        : dip_window_days_(dip_window_days),
          dip_threshold_(dip_threshold),
          min_edge_(min_edge),
          take_profit_(take_profit),
          max_hold_days_(max_hold_days),
          max_positions_(max_positions),
          budget_frac_(budget_frac),
          min_price_(min_price),
          fee_rate_(fee_rate),
          shipping_per_line_(shipping_per_line) {}

    std::vector<Order> on_bar(const Context& ctx) override;

  private:
    std::int64_t dip_window_days_;
    double dip_threshold_;
    double min_edge_;
    double take_profit_;
    std::int64_t max_hold_days_;
    std::int64_t max_positions_;
    double budget_frac_;
    double min_price_;
    double fee_rate_;
    double shipping_per_line_;
};

}  // namespace pkmn
