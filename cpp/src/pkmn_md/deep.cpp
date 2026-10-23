#include "pkmn_md/deep.hpp"

#include "pkmn_md/iextp.hpp"

namespace pkmn::md {

namespace {
using detail::load;
constexpr std::size_t kPluBytes = 30;    // DEEP v1.08 p.17
constexpr std::size_t kQuoteBytes = 42;  // TOPS v1.66 p.16
}  // namespace

bool decode_deep_level(std::span<const std::uint8_t> msg, LevelUpdate& out) {
    if (msg.size() < kPluBytes) return false;
    const std::uint8_t* p = msg.data();
    if (p[0] == '8') {
        out.side = book::Side::Buy;
    } else if (p[0] == '5') {
        out.side = book::Side::Sell;
    } else {
        return false;
    }
    out.event_complete = (p[1] & 0x01) != 0;
    out.ts_ns = load<std::int64_t>(p + 2);
    out.symbol = load<std::uint64_t>(p + 10);
    out.size = load<std::uint32_t>(p + 18);
    out.price = load<std::int64_t>(p + 22);
    return true;
}

bool decode_tops_quote(std::span<const std::uint8_t> msg, TopQuote& out) {
    if (msg.size() < kQuoteBytes || msg[0] != 'Q') return false;
    const std::uint8_t* p = msg.data();
    out.flags = p[1];
    out.ts_ns = load<std::int64_t>(p + 2);
    out.symbol = load<std::uint64_t>(p + 10);
    out.bid_size = load<std::uint32_t>(p + 18);
    out.bid = load<std::int64_t>(p + 22);
    out.ask = load<std::int64_t>(p + 30);
    out.ask_size = load<std::uint32_t>(p + 38);
    return true;
}

}  // namespace pkmn::md
