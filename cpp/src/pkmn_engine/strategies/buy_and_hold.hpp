#pragma once

// Port of strategies/buy_and_hold.py.

#include <cstdint>
#include <vector>

#include "pkmn_engine/strategy.hpp"

namespace pkmn {

class BuyAndHold final : public Strategy {
  public:
    explicit BuyAndHold(std::int8_t kind_code) : kind_(kind_code) {}
    void reset() override { entered_ = false; }
    std::vector<Order> on_bar(const Context& ctx) override;

  private:
    std::int8_t kind_;
    bool entered_ = false;
};

}  // namespace pkmn
