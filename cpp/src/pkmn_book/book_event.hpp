#pragma once

// Feed-neutral book event: what a market-data decoder (or the Pokemon ladder
// builder) hands the book layer. One caller-owned instance is overwritten per
// message, so decoding never allocates.

#include <cstdint>

#include "pkmn_book/order_book.hpp"

namespace pkmn::book {

struct BookEvent {
    enum class Kind : std::uint8_t { Add, Modify, Delete, Execute, Clear };
    Kind kind;
    Side side;           // meaningful for Add only (Buy otherwise)
    bool keep_priority;  // meaningful for Modify only (false otherwise)
    std::uint64_t symbol;  // 8 ASCII bytes of the symbol field, little-endian load
    OrderId order_id;      // 0 for Clear
    Price price;           // Add/Modify: booking price; Execute: trade price; else 0
    std::int64_t qty;      // Add: size; Modify: new total; Execute: executed; else 0
    std::int64_t ts_ns;    // feed timestamp, ns since the Unix epoch
};

}  // namespace pkmn::book
