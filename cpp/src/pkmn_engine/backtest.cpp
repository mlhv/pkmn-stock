#include "pkmn_engine/backtest.hpp"

#include "pkmn_engine/execution.hpp"
#include "pkmn_engine/portfolio.hpp"

namespace pkmn {

BacktestResult run_backtest(MarketView& market, const ProductTable& products,
                            Strategy& strategy, const CostModel& cost_model,
                            double initial_cash) {
    // backtest.py:50-102, same step order per day.
    strategy.reset();
    market.reset();
    Portfolio portfolio(initial_cash, market.n_assets());
    BacktestResult out;
    std::vector<Order> pending;
    for (Day day : market.days()) {
        // 1. Yesterday's orders fill at today's actually-printed prices.
        market.load_day(day);
        auto fills = execute(pending, market, portfolio, day, cost_model);
        out.fills.insert(out.fills.end(), fills.begin(), fills.end());
        pending.clear();
        // 2. Strategy sees history <= today, emits orders for tomorrow.
        const auto& marks = market.marks_until(day);
        Context ctx{day, market, products, portfolio.positions, portfolio.cash, marks};
        pending = strategy.on_bar(ctx);
        // 3. Mark-to-market equity.
        out.days.push_back(day);
        out.equity.push_back(portfolio.equity(marks));
    }
    return out;
}

}  // namespace pkmn
