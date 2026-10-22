#include "pkmn_md/iextp.hpp"

namespace pkmn::md {

bool parse_tp_header(std::span<const std::uint8_t> payload, TpHeader& hdr) {
    if (payload.size() < kTpHeaderBytes) return false;
    const std::uint8_t* p = payload.data();
    using detail::load;
    hdr.version = p[0];
    // p[1] is reserved.
    hdr.protocol_id = load<std::uint16_t>(p + 2);
    hdr.channel_id = load<std::uint32_t>(p + 4);
    hdr.session_id = load<std::uint32_t>(p + 8);
    hdr.payload_len = load<std::uint16_t>(p + 12);
    hdr.msg_count = load<std::uint16_t>(p + 14);
    hdr.stream_offset = load<std::int64_t>(p + 16);
    hdr.first_seq = load<std::int64_t>(p + 24);
    hdr.send_time = load<std::int64_t>(p + 32);
    return true;
}

}  // namespace pkmn::md
