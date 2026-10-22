#pragma once

// Streaming pcapng reader for IEX HIST captures (gzipped pcapng, several GB
// decompressed). Never writes a decompressed copy to disk: the file is
// inflated through zlib into one in-memory window and each returned packet
// is a span into that window, not a copy.
//
// Format coverage: Section Header (little-endian only), Interface
// Description (Ethernet link type, if_tsresol option, default 10^-6),
// Enhanced Packet. Every other block type is skipped by its length. Frames
// are stripped of Ethernet (plus any 802.1Q/QinQ tags), IPv4 and UDP; a frame
// that is not unfragmented IPv4/UDP on Ethernet is skipped and counted.
//
// Window: a contiguous buffer (default 4 MiB) refilled by sliding the
// unconsumed tail to the front, so a block is always contiguous. It grows
// only when one block is larger than the whole buffer; steady-state next()
// does not allocate.

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <memory>
#include <span>
#include <string>
#include <vector>

namespace pkmn::md {

struct Packet {
    std::int64_t ts_ns;  // capture timestamp, ns since the Unix epoch
    std::span<const std::uint8_t> udp_payload;
};

class PcapngReader {
  public:
    static constexpr std::size_t kDefaultBuffer = std::size_t{4} << 20;

    // Gzip vs raw is detected by the gzip magic (1f 8b), not the extension.
    // Throws std::runtime_error if the file cannot be opened or is empty.
    // `buffer_bytes` exists so tests can force refills and growth with small
    // inputs; production callers keep the default.
    explicit PcapngReader(const std::string& path, std::size_t buffer_bytes = kDefaultBuffer);
    ~PcapngReader();
    PcapngReader(const PcapngReader&) = delete;
    PcapngReader& operator=(const PcapngReader&) = delete;

    // Next UDP packet. False at clean EOF (end of input exactly at a block
    // boundary). Throws std::runtime_error, naming the decompressed byte
    // offset, on a truncated block or gzip stream ("truncated"), corrupt
    // framing, a big-endian section, or a non-pcapng file. The span is valid
    // until the next call.
    bool next(Packet& out);

    // Decompressed offset just past the last block consumed.
    std::uint64_t bytes_consumed() const { return consumed_; }
    // Enhanced Packet Blocks skipped as not IPv4/UDP on Ethernet.
    std::uint64_t skipped_frames() const { return skipped_; }

  private:
    struct Source;  // raw fread or zlib inflate; hides zlib from this header
    struct Interface {
        std::uint16_t link_type;
        bool binary;          // if_tsresol high bit: 2^-exp, else 10^-exp
        std::uint8_t exp;
        std::int64_t offset_s;  // if_tsoffset
    };

    // Ensure `n` contiguous unconsumed bytes are in the window; false if the
    // input ends first (fewer bytes remain).
    bool ensure(std::size_t n);
    std::size_t available() const { return end_ - begin_; }
    [[noreturn]] void fail(const std::string& what) const;
    void parse_idb(const std::uint8_t* b, std::uint32_t len);
    bool parse_epb(const std::uint8_t* b, std::uint32_t len, Packet& out);
    std::int64_t to_ns(const Interface& itf, std::uint64_t ts) const;

    std::string path_;
    std::unique_ptr<Source> src_;
    std::vector<std::uint8_t> buf_;
    std::size_t begin_ = 0, end_ = 0;  // unconsumed window [begin_, end_)
    std::uint64_t consumed_ = 0;       // decompressed offset of buf_[begin_]
    std::uint64_t skipped_ = 0;
    bool in_section_ = false;
    std::vector<Interface> interfaces_;
};

}  // namespace pkmn::md
