#pragma once

// Port of engine/execution.py ExecutionSimulator.execute.

#include <vector>

#include "pkmn_engine/costs.hpp"
#include "pkmn_engine/market.hpp"
#include "pkmn_engine/portfolio.hpp"
#include "pkmn_engine/types.hpp"

namespace pkmn {

// Fill `orders` against the day's prints (market must be load_day(day)-ed),
// applying fills to the portfolio. Per-asset daily liquidity cap shared
// across orders and sides (execution.py:44-72).
std::vector<Fill> execute(const std::vector<Order>& orders, const MarketView& market,
                          Portfolio& portfolio, Day day, const CostModel& cm);

}  // namespace pkmn
