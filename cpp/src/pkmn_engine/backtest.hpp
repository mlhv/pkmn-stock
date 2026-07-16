#pragma once

// Port of engine/backtest.py: history -> strategy -> orders -> T+1 fills ->
// equity. Metrics stay in Python (summarize() is single-sourced there).

#include <vector>

#include "pkmn_engine/costs.hpp"
#include "pkmn_engine/market.hpp"
#include "pkmn_engine/strategy.hpp"
#include "pkmn_engine/types.hpp"

namespace pkmn {

struct BacktestResult {
    std::vector<Day> days;
    std::vector<double> equity;
    std::vector<Fill> fills;
};

BacktestResult run_backtest(MarketView& market, const ProductTable& products,
                            Strategy& strategy, const CostModel& cost_model,
                            double initial_cash);

}  // namespace pkmn
