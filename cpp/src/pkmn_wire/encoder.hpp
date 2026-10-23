#pragma once

#include <cstdint>
#include <ostream>
#include <span>
#include <string>

#include "pkmn_wire/wire.hpp"

namespace pkmn::wire {

// Parallel arrays. Instruments must be ordered by (product_id, sub_type) with
// ids dense from 0 (the instrument id is the array index). Rows must be
// strictly ascending by (day, instrument). Prices are whole cents or kNull.
struct EncodeInput {
    std::span<const std::int64_t> inst_product_id;
    std::span<const std::string> inst_sub_type;
    std::span<const std::int32_t> row_day;
    std::span<const std::uint32_t> row_instrument;
    std::span<const std::uint32_t> row_market, row_low, row_mid, row_high;
};

// Writes packets (<= kMaxPacketBytes each, first_seq contiguous from 1) to
// `out`: all D, then per day S, Q..., E. Returns the messages written. Throws
// std::invalid_argument on mismatched array sizes, unsorted/duplicate rows,
// an out-of-range instrument id, unordered instruments, or a sub_type over
// 24 bytes. Validation completes before the first byte is written.
std::uint64_t encode(const EncodeInput& in, std::ostream& out);

}  // namespace pkmn::wire
