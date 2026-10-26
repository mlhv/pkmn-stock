#pragma once

// Test-only byte builders for market-data fixtures: pcapng blocks wrapping
// Ethernet/IPv4/UDP frames, plus helpers to write them raw or gzipped. Every
// multi-byte pcapng field is little-endian (the only byte order the reader
// supports); network headers are big-endian as on the wire.

#include <unistd.h>
#include <zlib.h>

#include <atomic>
#include <cstdint>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace md_fixtures {

using Bytes = std::vector<std::uint8_t>;

inline void put_le16(Bytes& b, std::uint16_t v) {
    b.push_back(static_cast<std::uint8_t>(v));
    b.push_back(static_cast<std::uint8_t>(v >> 8));
}
inline void put_le32(Bytes& b, std::uint32_t v) {
    for (int i = 0; i < 4; ++i) b.push_back(static_cast<std::uint8_t>(v >> (8 * i)));
}
inline void put_le64(Bytes& b, std::uint64_t v) {
    for (int i = 0; i < 8; ++i) b.push_back(static_cast<std::uint8_t>(v >> (8 * i)));
}
inline void put_be16(Bytes& b, std::uint16_t v) {
    b.push_back(static_cast<std::uint8_t>(v >> 8));
    b.push_back(static_cast<std::uint8_t>(v));
}
inline void append(Bytes& dst, const Bytes& src) { dst.insert(dst.end(), src.begin(), src.end()); }
inline Bytes bytes_of(std::string_view s) { return Bytes(s.begin(), s.end()); }
inline void pad4(Bytes& b) {
    while (b.size() % 4 != 0) b.push_back(0);
}

// Generic block: type, total length, body (padded to 4), total length again.
inline Bytes block(std::uint32_t type, Bytes body) {
    pad4(body);
    const auto total = static_cast<std::uint32_t>(body.size() + 12);
    Bytes b;
    put_le32(b, type);
    put_le32(b, total);
    append(b, body);
    put_le32(b, total);
    return b;
}

// Section Header Block, little-endian, version 1.0, unknown section length.
inline Bytes shb() {
    Bytes body;
    put_le32(body, 0x1A2B3C4D);
    put_le16(body, 1);
    put_le16(body, 0);
    put_le64(body, ~std::uint64_t{0});
    return block(0x0A0D0D0A, body);
}

// The same SHB as written by a big-endian host.
inline Bytes shb_big_endian() {
    Bytes b = {0x0A, 0x0D, 0x0D, 0x0A, 0x00, 0x00, 0x00, 0x1C, 0x1A, 0x2B, 0x3C, 0x4D,
               0x00, 0x01, 0x00, 0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
               0x00, 0x00, 0x00, 0x1C};
    return b;
}

// Interface Description Block. tsresol < 0 omits the if_tsresol option.
inline Bytes idb(std::uint16_t link_type = 1, int tsresol = 6) {
    Bytes body;
    put_le16(body, link_type);
    put_le16(body, 0);
    put_le32(body, 262144);  // snaplen
    if (tsresol >= 0) {
        put_le16(body, 9);  // if_tsresol
        put_le16(body, 1);
        body.push_back(static_cast<std::uint8_t>(tsresol));
        pad4(body);
        put_le16(body, 0);  // opt_endofopt
        put_le16(body, 0);
    }
    return block(1, body);
}

// Enhanced Packet Block carrying `frame` (captured length = frame size).
inline Bytes epb(std::uint64_t ts, const Bytes& frame, std::uint32_t if_id = 0) {
    Bytes body;
    put_le32(body, if_id);
    put_le32(body, static_cast<std::uint32_t>(ts >> 32));
    put_le32(body, static_cast<std::uint32_t>(ts));
    put_le32(body, static_cast<std::uint32_t>(frame.size()));
    put_le32(body, static_cast<std::uint32_t>(frame.size()));
    append(body, frame);
    return block(6, body);
}

// Ethernet + IPv4 + `ip_payload`, with `vlan_tags` 802.1Q tags. Short frames
// are zero-padded to the 60-byte Ethernet minimum, as on a real wire, so the
// reader must take the payload length from the headers, not the frame size.
inline Bytes ipv4_frame(std::uint8_t protocol, const Bytes& ip_payload, int vlan_tags = 0) {
    Bytes f(12, 0xAA);  // dst + src MAC
    for (int i = 0; i < vlan_tags; ++i) {
        put_be16(f, 0x8100);
        put_be16(f, 0x0064);  // VLAN id 100
    }
    put_be16(f, 0x0800);
    const auto ip_total = static_cast<std::uint16_t>(20 + ip_payload.size());
    f.push_back(0x45);  // version 4, IHL 5
    f.push_back(0);
    put_be16(f, ip_total);
    put_be16(f, 0);       // id
    put_be16(f, 0x4000);  // DF, offset 0
    f.push_back(64);      // ttl
    f.push_back(protocol);
    put_be16(f, 0);  // checksum (unchecked)
    for (std::uint8_t a : {10, 0, 0, 1, 233, 215, 21, 4}) f.push_back(a);
    append(f, ip_payload);
    while (f.size() < 60) f.push_back(0);
    return f;
}

inline Bytes udp_frame(const Bytes& payload, int vlan_tags = 0) {
    Bytes udp;
    put_be16(udp, 10378);
    put_be16(udp, 10378);
    put_be16(udp, static_cast<std::uint16_t>(8 + payload.size()));
    put_be16(udp, 0);
    append(udp, payload);
    return ipv4_frame(17, udp, vlan_tags);
}

inline Bytes tcp_frame() { return ipv4_frame(6, Bytes(20, 0x11)); }

// Ethernet carrying ARP (not IPv4).
inline Bytes arp_frame() {
    Bytes f(12, 0xBB);
    put_be16(f, 0x0806);
    while (f.size() < 60) f.push_back(0);
    return f;
}

// SHB + IDB(tsresol) + one EPB per payload, timestamps ts[i].
inline Bytes capture(const std::vector<std::string>& payloads, const std::vector<std::uint64_t>& ts,
                     int tsresol = 6) {
    Bytes b = shb();
    append(b, idb(1, tsresol));
    for (std::size_t i = 0; i < payloads.size(); ++i) append(b, epb(ts[i], udp_frame(bytes_of(payloads[i]))));
    return b;
}

// IEX-TP segment: the 40-byte header ("<BBHIIHHqqq": version, reserved,
// protocol id, channel id, session id, payload length, message count, stream
// offset, first sequence number, send time) followed by each message with
// its little-endian u16 length prefix. `payload_len_override` >= 0 replaces
// the computed payload length, to build malformed segments.
inline Bytes tp_segment(std::uint16_t protocol_id, std::int64_t first_seq, const std::vector<Bytes>& msgs,
                        int payload_len_override = -1, int msg_count_override = -1,
                        std::int64_t send_time = 1'734'964'524'000'000'000) {
    Bytes body;
    for (const Bytes& m : msgs) {
        put_le16(body, static_cast<std::uint16_t>(m.size()));
        append(body, m);
    }
    Bytes b;
    b.push_back(1);  // version
    b.push_back(0);  // reserved
    put_le16(b, protocol_id);
    put_le32(b, 1);           // channel id
    put_le32(b, 0x42424242);  // session id
    put_le16(b, static_cast<std::uint16_t>(payload_len_override >= 0 ? payload_len_override : body.size()));
    put_le16(b, static_cast<std::uint16_t>(msg_count_override >= 0 ? msg_count_override : msgs.size()));
    put_le64(b, 1000);  // stream offset
    put_le64(b, static_cast<std::uint64_t>(first_seq));
    put_le64(b, static_cast<std::uint64_t>(send_time));  // send time
    append(b, body);
    return b;
}

// Eight-byte space-padded symbol field.
inline void put_sym(Bytes& b, const char* s) {
    std::string v(s);
    v.resize(8, ' ');
    append(b, bytes_of(v));
}

// DEEP+ Add Order 'a' (38 B): side/1 ts/2 sym/10 id/18 size/26 price/30.
inline Bytes dp_add(const char* sym, char side, std::uint64_t id, std::uint32_t size, std::int64_t price,
                    std::int64_t ts) {
    Bytes m = {'a', static_cast<std::uint8_t>(side)};
    put_le64(m, static_cast<std::uint64_t>(ts));
    put_sym(m, sym);
    put_le64(m, id);
    put_le32(m, size);
    put_le64(m, static_cast<std::uint64_t>(price));
    return m;
}

// Unique path in the system temp dir; the caller removes it (TempFile does).
inline std::string temp_path(const std::string& suffix) {
    static std::atomic<int> counter{0};
    auto p = std::filesystem::temp_directory_path() /
             ("pkmn_md_test_" + std::to_string(::getpid()) + "_" + std::to_string(counter++) + suffix);
    return p.string();
}

struct TempFile {
    std::string path;
    explicit TempFile(const std::string& suffix) : path(temp_path(suffix)) {}
    ~TempFile() {
        std::error_code ec;
        std::filesystem::remove(path, ec);
    }
    TempFile(const TempFile&) = delete;
    TempFile& operator=(const TempFile&) = delete;
};

inline void write_raw(const std::string& path, const Bytes& b) {
    std::ofstream f(path, std::ios::binary | std::ios::trunc);
    f.write(reinterpret_cast<const char*>(b.data()), static_cast<std::streamsize>(b.size()));
    if (!f) throw std::runtime_error("write_raw failed: " + path);
}

inline void write_gzip(const std::string& path, const Bytes& b) {
    gzFile g = gzopen(path.c_str(), "wb");
    if (g == nullptr) throw std::runtime_error("gzopen failed: " + path);
    if (!b.empty() && gzwrite(g, b.data(), static_cast<unsigned>(b.size())) != static_cast<int>(b.size())) {
        gzclose(g);
        throw std::runtime_error("gzwrite failed: " + path);
    }
    if (gzclose(g) != Z_OK) throw std::runtime_error("gzclose failed: " + path);
}

inline Bytes read_file(const std::string& path) {
    std::ifstream f(path, std::ios::binary);
    return Bytes(std::istreambuf_iterator<char>(f), std::istreambuf_iterator<char>());
}

// Drop the last `n` bytes of a file in place (a cut-short download).
inline void chop(const std::string& path, std::size_t n) {
    Bytes b = read_file(path);
    b.resize(b.size() - n);
    write_raw(path, b);
}

}  // namespace md_fixtures
