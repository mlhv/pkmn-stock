#pragma once

// Pokemon quote wire protocol: layout constants and the decoded event. The
// authoritative description is docs/pokemon-wire-protocol.md. Everything on
// the wire is little-endian and packed. The wire carries daily quotes, not
// ladder levels; see the doc for why.

#include <bit>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <string_view>

namespace pkmn::wire {

static_assert(std::endian::native == std::endian::little, "wire fields are little-endian and loaded with memcpy");

inline constexpr std::uint32_t kNull = 0xFFFFFFFFu;  // "no price" in a cents field
inline constexpr char kMagic[4] = {'P', 'K', 'W', '1'};
inline constexpr std::size_t kHeaderBytes = 16;     // magic u32, msg_count u16, payload_len u16, first_seq i64
inline constexpr std::size_t kMaxPacketBytes = 1400;  // header + payload, UDP-ready
inline constexpr std::size_t kSubTypeBytes = 24;

// Message types and the byte count that follows the u16 length (type byte
// included), i.e. the `len` field value.
inline constexpr char kTypeInstrument = 'D', kTypeDayStart = 'S', kTypeQuote = 'Q', kTypeDayEnd = 'E',
                      kTypeTrade = 'T';
inline constexpr std::size_t kLenInstrument = 38, kLenDayStart = 5, kLenQuote = 21, kLenDayEnd = 9, kLenTrade = 21;

struct WireEvent {
    enum class Kind : std::uint8_t { Instrument, DayStart, Quote, DayEnd, Trade };
    Kind kind = Kind::Instrument;
    std::uint32_t instrument = 0;
    std::int32_t day = 0;
    std::int64_t product_id = 0;
    // Instrument only. A view INTO the packet buffer, trailing NULs trimmed;
    // valid only during the sink call. Copy it to keep it.
    std::string_view sub_type;
    std::uint32_t market = 0, low = 0, mid = 0, high = 0;  // Quote, cents, kNull = absent
    std::uint32_t price = 0, qty = 0;                      // Trade
    std::int64_t ts_ns = 0;                                // Trade
    std::uint32_t quote_count = 0;                         // DayEnd
};

namespace detail {
template <class T>
inline T load(const unsigned char* p) {
    T v;
    std::memcpy(&v, p, sizeof v);
    return v;
}
}  // namespace detail

}  // namespace pkmn::wire
