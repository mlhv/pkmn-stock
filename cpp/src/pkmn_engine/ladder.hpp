#pragma once

// Daily ladder builder: turns one asset-day's tcgcsv quote into one-unit
// modelled resting orders (synthetic, built from the day's tcgcsv quote);
// tcgcsv publishes no bids, so the bid side is an assumption the formula
// already made implicitly. The asks ramp from the buy base toward `mid`, the
// bids from `market` toward `low`, in `depth` one-unit levels whose prices are
// the midpoints of the formula's equal slices (the spec's calibration anchor).
// Requires cm.impact_enabled; book mode is never reached with impact off.

#include <cassert>
#include <cmath>
#include <cstdint>

#include "pkmn_book/book_event.hpp"
#include "pkmn_book/order_book.hpp"
#include "pkmn_engine/costs.hpp"

namespace pkmn {

enum class LadderPricing : std::uint8_t { Cent, ExactTest };

// 100 for Cent, 1'000'000'000 for ExactTest (test-only).
std::int64_t ticks_per_dollar(LadderPricing p);

struct DayQuote {
    double market, mid, low;  // NaN = missing (as MarketView)
};

struct LadderShape {
    double base, far;
    std::int64_t depth;
    bool flat;
};

// base = cm.buy_base(market, low), far = mid, depth = cm.max_daily_qty(base).
LadderShape ask_shape(const DayQuote& q, const CostModel& cm);
// base = market, far = low, depth = cm.max_daily_qty(market).
LadderShape bid_shape(const DayQuote& q, const CostModel& cm);

// Price of 0-based unit k on that side, in ticks; defined for every k >= 0
// (k >= depth is the ramp's extension, used only by hypothetical exits).
// Never negative.
book::Price unit_ticks(const LadderShape& s, book::Side resting, std::int64_t k,
                       LadderPricing p);

// Emits one one-unit Add per unit: asks k = 0..depth-1 with ids 1..depth, then
// bids with ids (1<<32)+1.., each through sink(const book::BookEvent&).
template <class Sink>
void build_day_ladder(const DayQuote& q, const CostModel& cm, LadderPricing p,
                      std::uint64_t symbol, Sink&& sink) {
    assert(cm.impact_enabled);
    book::BookEvent ev{};
    ev.kind = book::BookEvent::Kind::Add;
    ev.keep_priority = false;
    ev.symbol = symbol;
    ev.qty = 1;
    ev.ts_ns = 0;
    LadderShape a = ask_shape(q, cm);
    ev.side = book::Side::Sell;
    for (std::int64_t k = 0; k < a.depth; ++k) {
        ev.order_id = static_cast<book::OrderId>(k + 1);
        ev.price = unit_ticks(a, book::Side::Sell, k, p);
        sink(static_cast<const book::BookEvent&>(ev));
    }
    LadderShape b = bid_shape(q, cm);
    ev.side = book::Side::Buy;
    for (std::int64_t k = 0; k < b.depth; ++k) {
        ev.order_id = (static_cast<book::OrderId>(1) << 32) + static_cast<book::OrderId>(k + 1);
        ev.price = unit_ticks(b, book::Side::Buy, k, p);
        sink(static_cast<const book::BookEvent&>(ev));
    }
}

// Clears `out`, then applies the Adds.
void build_day_ladder(const DayQuote& q, const CostModel& cm, LadderPricing p,
                      book::OrderBook& out);

}  // namespace pkmn
