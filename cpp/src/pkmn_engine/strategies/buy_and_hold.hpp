#pragma once

// Port of strategies/buy_and_hold.py.

#include <cstdint>
#include <vector>

#include "pkmn_engine/strategy.hpp"

namespace pkmn {

class BuyAndHold final : public Strategy {
  public:
    // n_assets defaults to 100, matching BuyAndHold.__init__'s default on the
    // Python side — the two must move together or parity breaks.
    explicit BuyAndHold(std::int8_t kind_code, std::int64_t n_assets = 100)
        : kind_(kind_code), n_assets_(n_assets) {}
    void reset() override { entered_ = false; }
    std::vector<Order> on_bar(const Context& ctx) override;

  private:
    std::int8_t kind_;
    std::int64_t n_assets_;
    bool entered_ = false;
};

}  // namespace pkmn
