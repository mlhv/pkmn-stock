#pragma once

// IEX DEEP (aggregated depth, Message Protocol ID 0x8004) Price Level Update
// and IEX TOPS (top of book, 0x8003) Quote Update decoders. Input is one
// message as split out by for_each_message (type byte first). Both accept
// messages longer than their spec length and ignore the tail (the specs
// reserve the right to grow messages). Do not allocate.
//
// DEEP v1.08 p.17, Price Level Update '8' (0x38) buy / '5' (0x35) sell, 30 B:
//   type/0 event flags/1 ts/2 sym/10 size/18 (u32) price/22 (i64)
//   Event Flags 0x1 = event processing complete (the book transition is
//   done); 0x0 = in transition. A size of 0 removes the level.
// TOPS v1.66 p.16, Quote Update 'Q' (0x51), 42 B:
//   type/0 flags/1 ts/2 sym/10 bid size/18 (u32) bid price/22 (i64)
//   ask price/30 (i64) ask size/38 (u32)
//   Flags (Appendix A p.26): 0x80 symbol halted/paused/unavailable,
//   0x40 pre/post-market session.

#include <cstdint>
#include <span>

#include "pkmn_book/order_book.hpp"

namespace pkmn::md {

struct LevelUpdate {
    std::uint64_t symbol;  // 8 ASCII bytes, little-endian load (as BookEvent)
    book::Side side;
    book::Price price;
    std::int64_t size;
    std::int64_t ts_ns;
    bool event_complete;
};

// True iff `msg` is a DEEP '8'/'5' of at least 30 bytes; `out` is then filled.
bool decode_deep_level(std::span<const std::uint8_t> msg, LevelUpdate& out);

struct TopQuote {
    std::uint64_t symbol;
    book::Price bid, ask;
    std::int64_t bid_size, ask_size;
    std::int64_t ts_ns;
    std::uint8_t flags;  // Quote Update flags byte (not in the plan's interface; kept for diagnosis)
};

// True iff `msg` is a TOPS 'Q' of at least 42 bytes; `out` is then filled.
bool decode_tops_quote(std::span<const std::uint8_t> msg, TopQuote& out);

}  // namespace pkmn::md
