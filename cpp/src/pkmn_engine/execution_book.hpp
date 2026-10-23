#pragma once

// Book execution (Plan B): strategy orders fill against each asset-day's
// ladder of modelled resting orders (synthetic, built from the day's tcgcsv
// quote; see ladder.hpp) instead of the walk-the-spread closed form. Same
// order loop, skip rules and T+1 timing as execute(); Fill.price stays the
// print. Only reached with cm.impact_enabled and a non-Formula mode.

#include <cstddef>
#include <cstdint>
#include <memory>
#include <vector>

#include "pkmn_book/order_book.hpp"
#include "pkmn_engine/costs.hpp"
#include "pkmn_engine/ladder.hpp"
#include "pkmn_engine/market.hpp"
#include "pkmn_engine/portfolio.hpp"
#include "pkmn_engine/types.hpp"

namespace pkmn {

// Per-run pool of OrderBooks, one per asset that has an order today. Books
// are allocated on first use and reused across days; a per-asset "built on
// day epoch" marker makes new_day() O(1). Not thread-safe: one per
// run_backtest call.
class DayBooks {
  public:
    explicit DayBooks(std::size_t n_assets);
    void new_day();  // marks every book stale; storage is reused
    // Builds the asset's ladder on first touch today, then returns the same book.
    book::OrderBook& get(AssetId a, const DayQuote& q, const CostModel& cm, LadderPricing p);

  private:
    std::vector<std::unique_ptr<book::OrderBook>> books_;
    std::vector<std::uint32_t> built_;  // epoch the book was last built in
    std::uint32_t epoch_ = 1;           // built_ starts at 0: everything stale
};

// ExecutionMode::Book -> Cent, BookExactTest -> ExactTest.
LadderPricing ladder_pricing(ExecutionMode m);

// Fill `orders` against today's ladders (market must be load_day(day)-ed and
// books.new_day() called for this day), applying fills to the portfolio.
// Each side's remaining depth is what is left in its book: there is no shared
// per-asset counter across sides.
std::vector<Fill> execute_book(const std::vector<Order>& orders, const MarketView& market,
                               Portfolio& portfolio, Day day, const CostModel& cm,
                               DayBooks& books);

}  // namespace pkmn
