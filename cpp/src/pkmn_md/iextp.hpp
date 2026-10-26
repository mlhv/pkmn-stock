#pragma once

// IEX Transport Protocol (IEX-TP) framing: one UDP payload is a 40-byte
// segment header followed by `msg_count` messages, each prefixed by its
// little-endian u16 length. The header layout is struct "<BBHIIHHqqq"
// (version, reserved, protocol id, channel id, session id, payload length,
// message count, stream offset, first sequence number, send time), verified
// on real HIST packets 2026-10-22.
//
// Sequence numbers: `first_seq` is the sequence number of the first message
// in the segment; the i-th message delivered by for_each_message has
// sequence first_seq + i. A heartbeat carries no messages (msg_count 0) and
// first_seq is the next sequence number to expect.

#include <bit>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <span>

namespace pkmn::md {

static_assert(std::endian::native == std::endian::little,
              "IEX-TP fields are little-endian and are loaded with memcpy");

struct TpHeader {
    std::uint8_t version;
    std::uint16_t protocol_id;
    std::uint32_t channel_id, session_id;
    std::uint16_t payload_len, msg_count;
    std::int64_t stream_offset, first_seq, send_time;
};

// Message Protocol IDs: DEEP+ spec v1.06 p.4, DEEP spec v1.08 p.3, TOPS spec v1.66 p.3.
inline constexpr std::uint16_t kDeepPlus = 0x8005, kDeep = 0x8004, kTops = 0x8003;
inline constexpr std::size_t kTpHeaderBytes = 40;

namespace detail {
template <class T>
inline T load(const std::uint8_t* p) {
    T v;
    std::memcpy(&v, p, sizeof v);
    return v;
}
}  // namespace detail

// Fills `hdr` from the first 40 bytes. False if fewer than 40 bytes.
bool parse_tp_header(std::span<const std::uint8_t> payload, TpHeader& hdr);

// Parses the header into `hdr`, then calls f(std::span<const std::uint8_t>)
// once per message, in order. False -- with no callback made -- if the
// segment is shorter than its header, if 40 + payload_len exceeds the bytes
// present, or if msg_count length-prefixed messages do not fit inside
// payload_len. Bytes in payload_len after the last counted message are
// ignored. Validation runs before the first callback, so a false return
// never leaves the caller with half a segment applied. Does not allocate.
template <class F>
bool for_each_message(std::span<const std::uint8_t> payload, TpHeader& hdr, F&& f) {
    if (!parse_tp_header(payload, hdr)) return false;
    if (payload.size() - kTpHeaderBytes < hdr.payload_len) return false;
    const std::uint8_t* const body = payload.data() + kTpHeaderBytes;
    const std::size_t len = hdr.payload_len;
    std::size_t off = 0;
    for (std::uint16_t i = 0; i < hdr.msg_count; ++i) {
        if (len - off < 2) return false;
        const std::size_t n = detail::load<std::uint16_t>(body + off);
        if (len - off - 2 < n) return false;
        off += 2 + n;
    }
    off = 0;
    for (std::uint16_t i = 0; i < hdr.msg_count; ++i) {
        const std::size_t n = detail::load<std::uint16_t>(body + off);
        f(std::span<const std::uint8_t>(body + off + 2, n));
        off += 2 + n;
    }
    return true;
}

// Caller-side restart policy for SequenceTracker (see sequence.hpp). True
// iff this segment starts a new sequence epoch: first_seq == 1 while the
// tracker expects something later, and a send_time newer than any segment
// seen so far (`max_send_time_seen`). Then the caller does tracker.reset(1),
// observe() (which yields Next) and applies the whole segment.
//   Restart: IEX restarted the feed and numbers from 1 again, under the same
//     session id (2024-12-23 11:39 ET) -- the send time moves forward.
//   Replay: a seq-1 segment whose send_time is not newer than anything seen
//     is an old segment arriving again (e.g. at a merge seam); it is not a
//     restart and stays Duplicate.
inline bool is_stream_restart(const TpHeader& hdr, std::int64_t expected, std::int64_t max_send_time_seen) {
    return hdr.first_seq == 1 && expected > 1 && hdr.send_time > max_send_time_seen;
}

}  // namespace pkmn::md
