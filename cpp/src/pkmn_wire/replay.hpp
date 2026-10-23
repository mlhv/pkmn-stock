#pragma once

// Replay path: decoded wire events -> one modelled resting-order ladder
// (synthetic, built from the day's tcgcsv quote) per Quote, through the SAME
// builder the backtester uses (pkmn::build_day_ladder). The wire carries
// cents; the quote handed to the builder is cents / 100.0, null -> NaN, so a
// warehouse value that is a whole number of cents (the encoder refuses any
// other) reaches the builder as the same double the backtester reads.

#include <cstddef>
#include <cstdint>
#include <limits>
#include <string>
#include <vector>

#include "pkmn_book/order_book.hpp"
#include "pkmn_engine/costs.hpp"
#include "pkmn_engine/ladder.hpp"
#include "pkmn_wire/wire.hpp"

namespace pkmn::wire {

namespace detail {
inline void fnv(std::uint64_t& h, std::uint64_t v, int bytes) {
    for (int i = 0; i < bytes; ++i) {
        h ^= static_cast<std::uint8_t>(v >> (8 * i));
        h *= 0x100000001b3ULL;
    }
}
}  // namespace detail

// FNV-1a 64 over the whole book: a side tag ('S'), then every ask level best
// first as (price i64, qty i64, orders u32) little-endian, then 'B' and every
// bid level the same way.
inline std::uint64_t ladder_digest(const book::OrderBook& b) {
    std::uint64_t h = 0xcbf29ce484222325ULL;
    std::vector<book::Level> levels;
    for (book::Side side : {book::Side::Sell, book::Side::Buy}) {
        detail::fnv(h, side == book::Side::Sell ? 'S' : 'B', 1);
        b.depth(side, std::numeric_limits<std::size_t>::max(), levels);
        for (const book::Level& l : levels) {
            detail::fnv(h, static_cast<std::uint64_t>(l.price), 8);
            detail::fnv(h, static_cast<std::uint64_t>(l.qty), 8);
            detail::fnv(h, l.orders, 4);
        }
    }
    return h;
}

inline double cents_to_dollars(std::uint32_t c) {
    return c == kNull ? std::numeric_limits<double>::quiet_NaN() : static_cast<double>(c) / 100.0;
}

inline DayQuote to_day_quote(const WireEvent& q) {
    return DayQuote{cents_to_dollars(q.market), cents_to_dollars(q.mid), cents_to_dollars(q.low)};
}

struct InstrumentInfo {
    std::int64_t product_id = 0;
    std::string sub_type;
};

// A decoder sink. Keeps the instrument table and the current day; for every
// Quote with a non-null market it rebuilds `book()` (one reused OrderBook)
// and calls on_ladder(day, instrument, book). Quotes with a null market build
// nothing (the backtester never prices one: the quality gates quarantine
// them). With ladders off it only counts.
template <class OnLadder>
class LadderReplay {
  public:
    LadderReplay(const CostModel& cm, LadderPricing pricing, bool ladders, OnLadder on_ladder)
        : cm_(cm), pricing_(pricing), ladders_(ladders), on_(on_ladder) {}

    void operator()(const WireEvent& ev) {
        switch (ev.kind) {
            case WireEvent::Kind::Instrument:
                if (ev.instrument >= instruments_.size()) instruments_.resize(ev.instrument + 1);
                instruments_[ev.instrument] = InstrumentInfo{ev.product_id, std::string(ev.sub_type)};
                break;
            case WireEvent::Kind::DayStart:
                day_ = ev.day;
                ++days_;
                break;
            case WireEvent::Kind::Quote:
                ++quotes_;
                if (ev.market == kNull) {
                    ++null_market_;
                    break;
                }
                if (!ladders_) break;
                build_day_ladder(to_day_quote(ev), cm_, pricing_, book_);
                ++ladders_built_;
                on_(day_, ev.instrument, static_cast<const book::OrderBook&>(book_));
                break;
            case WireEvent::Kind::DayEnd:
            case WireEvent::Kind::Trade:
                break;
        }
    }

    const std::vector<InstrumentInfo>& instruments() const { return instruments_; }
    std::uint64_t quotes() const { return quotes_; }
    std::uint64_t days() const { return days_; }
    std::uint64_t null_market() const { return null_market_; }
    std::uint64_t ladders_built() const { return ladders_built_; }

  private:
    CostModel cm_;
    LadderPricing pricing_;
    bool ladders_;
    OnLadder on_;
    book::OrderBook book_;
    std::vector<InstrumentInfo> instruments_;
    std::int32_t day_ = 0;
    std::uint64_t quotes_ = 0, days_ = 0, null_market_ = 0, ladders_built_ = 0;
};

}  // namespace pkmn::wire
