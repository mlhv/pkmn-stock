#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <span>
#include <stdexcept>
#include <string>
#include <vector>

#include "pkmn_md/sequence.hpp"
#include "pkmn_wire/wire.hpp"

namespace pkmn::wire {

struct DecodeStats {
    std::uint64_t packets = 0, messages = 0, unknown = 0, short_messages = 0, short_packets = 0, gaps = 0,
                  gap_messages = 0, duplicates = 0;
    // Packets whose magic was not "PKW1": delivered nothing, not counted in
    // `packets` or `short_packets`.
    std::uint64_t bad_magic = 0;
};

namespace detail {
enum class MsgResult { Ok, Unknown, Short };
// Decodes one message body (`body` points at the type byte, `len` bytes).
MsgResult decode_message(const unsigned char* body, std::size_t len, WireEvent& ev);
}  // namespace detail

class Decoder {
  public:
    // Decodes one packet, calling sink(const WireEvent&) per delivered
    // message. Never reads past `bytes` and never allocates.
    //  - fewer than 16 bytes, or a first_seq outside the tracker's range:
    //    short_packets++, nothing delivered.
    //  - bad magic: bad_magic++, nothing delivered.
    //  - payload_len beyond the bytes present: short_packets++, and the
    //    messages that fit are still delivered.
    //  - a message cut off by the end of the payload (or a len too small for
    //    its type): short_messages++ and the rest of the packet is dropped.
    //  - unknown type: unknown++ and skipped; len longer than the known body:
    //    the prefix is read, the rest skipped.
    //  - messages already seen (duplicate sequence numbers) are not delivered.
    template <class Sink>
    void packet(std::span<const std::byte> bytes, Sink&& sink) {
        const auto* p = reinterpret_cast<const unsigned char*>(bytes.data());
        if (bytes.size() < kHeaderBytes) {
            ++stats_.short_packets;
            return;
        }
        if (std::memcmp(p, kMagic, 4) != 0) {
            ++stats_.bad_magic;
            return;
        }
        const auto msg_count = detail::load<std::uint16_t>(p + 4);
        const auto payload_len = detail::load<std::uint16_t>(p + 6);
        const auto first_seq = detail::load<std::int64_t>(p + 8);
        if (!pkmn::md::valid_first_seq(first_seq)) {
            ++stats_.short_packets;
            return;
        }
        ++stats_.packets;
        std::size_t avail = bytes.size() - kHeaderBytes;
        if (avail < payload_len) {
            ++stats_.short_packets;
        } else {
            avail = payload_len;
        }
        seq_.observe(first_seq, msg_count);
        sync_seq_stats();
        const std::uint16_t skip = seq_.skip();

        const unsigned char* q = p + kHeaderBytes;
        std::size_t pos = 0;
        WireEvent ev;
        for (std::uint16_t i = 0; i < msg_count; ++i) {
            if (avail - pos < 2) {
                ++stats_.short_messages;
                return;
            }
            const std::size_t len = detail::load<std::uint16_t>(q + pos);
            if (len == 0 || avail - pos - 2 < len) {
                ++stats_.short_messages;
                return;
            }
            const unsigned char* body = q + pos + 2;
            pos += 2 + len;
            if (i < skip) continue;
            ev = WireEvent{};
            switch (detail::decode_message(body, len, ev)) {
                case detail::MsgResult::Ok:
                    ++stats_.messages;
                    sink(static_cast<const WireEvent&>(ev));
                    break;
                case detail::MsgResult::Unknown:
                    ++stats_.unknown;
                    break;
                case detail::MsgResult::Short:
                    ++stats_.short_messages;
                    return;
            }
        }
    }

    const DecodeStats& stats() const { return stats_; }

  private:
    void sync_seq_stats() {
        stats_.gaps = seq_.gaps();
        stats_.gap_messages = seq_.gap_messages();
        stats_.duplicates = seq_.duplicates();
    }
    pkmn::md::SequenceTracker seq_;
    DecodeStats stats_;
};

// Streams a file packet by packet: reads the 16-byte header, then payload_len
// bytes into one reused buffer. A truncated final packet is handed to the
// decoder as-is (counted in short_packets). A bad magic stops the read, since
// the stream cannot be resynchronised. Throws std::runtime_error if the file
// cannot be opened.
template <class Sink>
DecodeStats decode_file(const std::string& path, Sink&& sink) {
    std::ifstream f(path, std::ios::binary);
    if (!f) throw std::runtime_error("cannot open " + path);
    Decoder dec;
    std::vector<std::byte> buf(kHeaderBytes + 65535);
    for (;;) {
        f.read(reinterpret_cast<char*>(buf.data()), kHeaderBytes);
        const auto got = static_cast<std::size_t>(f.gcount());
        if (got == 0) break;
        if (got < kHeaderBytes) {
            dec.packet(std::span<const std::byte>(buf.data(), got), sink);
            break;
        }
        std::uint16_t payload_len;
        std::memcpy(&payload_len, buf.data() + 6, sizeof payload_len);
        f.read(reinterpret_cast<char*>(buf.data() + kHeaderBytes), payload_len);
        const auto total = kHeaderBytes + static_cast<std::size_t>(f.gcount());
        dec.packet(std::span<const std::byte>(buf.data(), total), sink);
        if (std::memcmp(buf.data(), kMagic, 4) != 0 || f.gcount() < payload_len) break;
    }
    return dec.stats();
}

}  // namespace pkmn::wire
