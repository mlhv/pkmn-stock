#pragma once

// IEX DEEP+ (order-by-order depth, Message Protocol ID 0x8005) message
// decoder, per the IEX DEEP+ Specification v1.06. Input is one message as
// split out by for_each_message (type byte first, no length prefix).
//
// Book-affecting types decode into a BookEvent:
//   'a' Add Order       side/1 ts/2 sym/10 id/18 size/26 (u32) price/30 (i64)   38 B
//   'M' Order Modify    flags/1 (bit 0 = maintain priority), rest as 'a'        38 B
//   'R' Order Delete    ts/2 sym/10 id/18                                         26 B
//   'L' Order Executed  ts/2 sym/10 id/18 size/26 price/30 trade id/38           46 B
//   'C' Clear Book      ts/2 sym/10                                               18 B
// Every other spec type -- 'T' trade, 'B' trade break, and the administrative
// S D H I O P E -- is only counted (by_type, and short_message if shorter
// than its spec length). Any other type byte counts unknown_type and is
// skipped (the spec reserves the right to add types). Messages longer than
// their spec length are accepted and the extra bytes ignored (the spec
// reserves the right to grow messages). Does not allocate.

#include <cstdint>
#include <span>

#include "pkmn_md/events.hpp"

namespace pkmn::md {

struct DecodeStats {
    std::uint64_t messages = 0;       // every message passed in
    std::uint64_t unknown_type = 0;   // type byte not in the v1.06 spec
    std::uint64_t short_message = 0;  // shorter than its type's spec length (or empty)
    std::uint64_t bad_side = 0;       // Add Order side byte neither '8' nor '5'
    std::uint64_t by_type[256] = {};  // by type byte, known or not (empty messages excluded)
};

// True only when `out` now holds a book-affecting event (a, M, R, L, C).
// On false, `out` is unspecified.
bool decode_deep_plus(std::span<const std::uint8_t> msg, BookEvent& out, DecodeStats& st);

// Spec length of a v1.06 message type, or 0 if the type is unknown.
std::uint16_t deep_plus_spec_length(std::uint8_t type);

}  // namespace pkmn::md
