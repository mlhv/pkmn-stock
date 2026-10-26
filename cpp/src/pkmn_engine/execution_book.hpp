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

// Sell-side impact of a HYPOTHETICAL sale of `qty` units on quote `q` (used to
// price a still-open lot's modelled exit in `pkmn horizon`; never a fill).
// Units 0..min(qty, depth)-1 match a freshly built bid book of modelled
// resting orders (synthetic, built from the day's tcgcsv quote); units
// depth..qty-1 are priced by unit_ticks on the same bid ramp extended past its
// last level -- extrapolation, the book's analogue of the formula's exact
// limit. Returns qty*market - notional/tpd, clamped >= 0 (as execute_book's
// sells). Every unit price is >= 0 ticks, so the result never exceeds
// qty*market. 0 for qty <= 0 or a non-positive / NaN market. Requires
// cm.impact_enabled (asserted by the ladder builder).
double hypothetical_sell_impact(const DayQuote& q, const CostModel& cm, LadderPricing p,
                                std::int64_t qty);

}  // namespace pkmn
