#include "pkmn_md/deep_plus.hpp"

#include <array>

#include "pkmn_md/iextp.hpp"

namespace pkmn::md {

namespace {

using detail::load;

// Spec v1.06 "Total Message Data length" per type, pages 13-27.
constexpr std::array<std::uint16_t, 256> kSpecLength = [] {
    std::array<std::uint16_t, 256> a{};
    a['S'] = 10;  // System Event, p.13
    a['D'] = 31;  // Security Directory, p.14
    a['H'] = 22;  // Trading Status, p.15-16
    a['I'] = 18;  // Retail Liquidity Indicator, p.17
    a['O'] = 18;  // Operational Halt Status, p.18
    a['P'] = 19;  // Short Sale Price Test Status, p.19
    a['E'] = 18;  // Security Event, p.20
    a['a'] = 38;  // Add Order, p.21
    a['M'] = 38;  // Order Modify, p.22
    a['R'] = 26;  // Order Delete, p.23
    a['L'] = 46;  // Order Executed, p.24
    a['T'] = 38;  // Trade, p.25
    a['B'] = 38;  // Trade Break, p.26
    a['C'] = 18;  // Clear Book, p.27
    return a;
}();

void common(const std::uint8_t* p, BookEvent& out) {
    out.side = book::Side::Buy;
    out.keep_priority = false;
    out.ts_ns = load<std::int64_t>(p + 2);
    out.symbol = load<std::uint64_t>(p + 10);
    out.order_id = 0;
    out.price = 0;
    out.qty = 0;
}

void id_size_price(const std::uint8_t* p, BookEvent& out) {
    out.order_id = load<std::uint64_t>(p + 18);
    out.qty = load<std::uint32_t>(p + 26);
    out.price = load<std::int64_t>(p + 30);
}

}  // namespace

std::uint16_t deep_plus_spec_length(std::uint8_t type) { return kSpecLength[type]; }

bool decode_deep_plus(std::span<const std::uint8_t> msg, BookEvent& out, DecodeStats& st) {
    ++st.messages;
    if (msg.empty()) {
        ++st.short_message;
        return false;
    }
    const std::uint8_t* p = msg.data();
    const std::uint8_t type = p[0];
    ++st.by_type[type];
    const std::uint16_t need = kSpecLength[type];
    if (need == 0) {
        ++st.unknown_type;
        return false;
    }
    if (msg.size() < need) {
        ++st.short_message;
        return false;
    }
    switch (type) {
        case 'a': {
            const std::uint8_t side = p[1];
            if (side != '8' && side != '5') {
                ++st.bad_side;
                return false;
            }
            common(p, out);
            out.kind = BookEvent::Kind::Add;
            out.side = side == '8' ? book::Side::Buy : book::Side::Sell;
            id_size_price(p, out);
            return true;
        }
        case 'M':
            common(p, out);
            out.kind = BookEvent::Kind::Modify;
            out.keep_priority = (p[1] & 0x01) != 0;
            id_size_price(p, out);
            return true;
        case 'R':
            common(p, out);
            out.kind = BookEvent::Kind::Delete;
            out.order_id = load<std::uint64_t>(p + 18);
            return true;
        case 'L':
            common(p, out);
            out.kind = BookEvent::Kind::Execute;
            id_size_price(p, out);
            return true;
        case 'C':
            common(p, out);
            out.kind = BookEvent::Kind::Clear;
            return true;
        default:
            return false;  // T, B, administrative: counted only
    }
}

}  // namespace pkmn::md
