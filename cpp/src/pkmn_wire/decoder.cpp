#include "pkmn_wire/decoder.hpp"

namespace pkmn::wire::detail {

MsgResult decode_message(const unsigned char* body, std::size_t len, WireEvent& ev) {
    const unsigned char* b = body + 1;  // past the type byte
    switch (static_cast<char>(body[0])) {
        case kTypeInstrument:
            if (len < kLenInstrument) return MsgResult::Short;
            ev.kind = WireEvent::Kind::Instrument;
            ev.instrument = load<std::uint32_t>(b);
            ev.product_id = load<std::int64_t>(b + 4);
            {
                std::size_t n = b[12];
                if (n > kSubTypeBytes) n = kSubTypeBytes;
                while (n > 0 && b[13 + n - 1] == 0) --n;
                ev.sub_type = std::string_view(reinterpret_cast<const char*>(b + 13), n);
            }
            return MsgResult::Ok;
        case kTypeDayStart:
            if (len < kLenDayStart) return MsgResult::Short;
            ev.kind = WireEvent::Kind::DayStart;
            ev.day = load<std::int32_t>(b);
            return MsgResult::Ok;
        case kTypeQuote:
            if (len < kLenQuote) return MsgResult::Short;
            ev.kind = WireEvent::Kind::Quote;
            ev.instrument = load<std::uint32_t>(b);
            ev.market = load<std::uint32_t>(b + 4);
            ev.low = load<std::uint32_t>(b + 8);
            ev.mid = load<std::uint32_t>(b + 12);
            ev.high = load<std::uint32_t>(b + 16);
            return MsgResult::Ok;
        case kTypeDayEnd:
            if (len < kLenDayEnd) return MsgResult::Short;
            ev.kind = WireEvent::Kind::DayEnd;
            ev.day = load<std::int32_t>(b);
            ev.quote_count = load<std::uint32_t>(b + 4);
            return MsgResult::Ok;
        case kTypeTrade:
            if (len < kLenTrade) return MsgResult::Short;
            ev.kind = WireEvent::Kind::Trade;
            ev.instrument = load<std::uint32_t>(b);
            ev.price = load<std::uint32_t>(b + 4);
            ev.qty = load<std::uint32_t>(b + 8);
            ev.ts_ns = load<std::int64_t>(b + 12);
            return MsgResult::Ok;
        default:
            return MsgResult::Unknown;
    }
}

}  // namespace pkmn::wire::detail
