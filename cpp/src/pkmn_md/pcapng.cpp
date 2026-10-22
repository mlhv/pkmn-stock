#include "pkmn_md/pcapng.hpp"

#include <zlib.h>

#include <algorithm>
#include <cstring>
#include <stdexcept>

namespace pkmn::md {

namespace {

constexpr std::uint32_t kShb = 0x0A0D0D0A;
constexpr std::uint32_t kIdb = 1;
constexpr std::uint32_t kEpb = 6;
constexpr std::uint32_t kMagicLe = 0x1A2B3C4D;
constexpr std::uint32_t kMagicSwapped = 0x4D3C2B1A;  // the magic of a big-endian section
// Larger than any sane block (snaplen is 256 KiB); stops a corrupt length
// from turning into a multi-GB allocation.
constexpr std::uint32_t kMaxBlock = 64u << 20;
constexpr std::size_t kInflateIn = 256u << 10;
constexpr std::uint16_t kLinkEthernet = 1;

std::uint16_t le16(const std::uint8_t* p) { return static_cast<std::uint16_t>(p[0] | (p[1] << 8)); }
std::uint32_t le32(const std::uint8_t* p) {
    return static_cast<std::uint32_t>(p[0]) | (static_cast<std::uint32_t>(p[1]) << 8) |
           (static_cast<std::uint32_t>(p[2]) << 16) | (static_cast<std::uint32_t>(p[3]) << 24);
}
std::uint64_t le64(const std::uint8_t* p) {
    return static_cast<std::uint64_t>(le32(p)) | (static_cast<std::uint64_t>(le32(p + 4)) << 32);
}
std::uint16_t be16(const std::uint8_t* p) { return static_cast<std::uint16_t>((p[0] << 8) | p[1]); }

}  // namespace

// Byte source: the file as-is, or inflated through zlib. read() fills up to
// `cap` bytes and returns 0 only when no more bytes will come. A gzip input
// that ends before its trailer is not an error here: read() returns what it
// produced and sets `truncated`, so the reader can still hand out every
// complete block before it reports the cut.
struct PcapngReader::Source {
    std::FILE* f = nullptr;
    bool gzip = false;
    bool truncated = false;
    bool done = false;
    // gzip state
    z_stream zs{};
    bool zs_live = false;
    bool member_end = false;  // inflate hit Z_STREAM_END; maybe another member follows
    std::vector<std::uint8_t> in;

    ~Source() {
        if (zs_live) inflateEnd(&zs);
        if (f != nullptr) std::fclose(f);
    }

    void io_check(const std::string& path) const {
        if (std::ferror(f)) throw std::runtime_error("pcapng: read error (" + path + ")");
    }

    bool refill(const std::string& path) {
        const std::size_t got = std::fread(in.data(), 1, in.size(), f);
        io_check(path);
        zs.next_in = in.data();
        zs.avail_in = static_cast<uInt>(got);
        return got > 0;
    }

    std::size_t read(std::uint8_t* dst, std::size_t cap, const std::string& path) {
        if (done || cap == 0) return 0;
        if (!gzip) {
            const std::size_t got = std::fread(dst, 1, cap, f);
            io_check(path);
            if (got == 0) done = true;
            return got;
        }
        zs.next_out = dst;
        zs.avail_out = static_cast<uInt>(std::min<std::size_t>(cap, 1u << 30));
        const uInt want = zs.avail_out;
        while (zs.avail_out > 0) {
            if (zs.avail_in == 0 && !refill(path)) {
                // Input exhausted: clean only if the last member was complete.
                if (!member_end) truncated = true;
                done = true;
                break;
            }
            if (member_end) {  // concatenated gzip members (gzip(1) allows them)
                inflateReset(&zs);
                member_end = false;
            }
            const int ret = inflate(&zs, Z_NO_FLUSH);
            if (ret == Z_STREAM_END) {
                member_end = true;
            } else if (ret != Z_OK && ret != Z_BUF_ERROR) {
                throw std::runtime_error(std::string("pcapng: corrupt gzip stream: ") +
                                         (zs.msg != nullptr ? zs.msg : "inflate error") + " (" + path + ")");
            }
        }
        return want - zs.avail_out;
    }
};

PcapngReader::PcapngReader(const std::string& path, std::size_t buffer_bytes)
    : path_(path), src_(std::make_unique<Source>()), buf_(std::max<std::size_t>(buffer_bytes, 64)) {
    src_->f = std::fopen(path.c_str(), "rb");
    if (src_->f == nullptr) throw std::runtime_error("pcapng: cannot open " + path);
    std::uint8_t magic[2] = {0, 0};
    const std::size_t got = std::fread(magic, 1, 2, src_->f);
    src_->io_check(path);
    if (got == 0) throw std::runtime_error("pcapng: empty file (" + path + ")");
    if (std::fseek(src_->f, 0, SEEK_SET) != 0) throw std::runtime_error("pcapng: cannot seek " + path);
    src_->gzip = got == 2 && magic[0] == 0x1f && magic[1] == 0x8b;
    if (src_->gzip) {
        src_->in.resize(kInflateIn);
        if (inflateInit2(&src_->zs, 16 + MAX_WBITS) != Z_OK)
            throw std::runtime_error("pcapng: inflateInit2 failed (" + path + ")");
        src_->zs_live = true;
    }
}

PcapngReader::~PcapngReader() = default;

void PcapngReader::fail(const std::string& what) const { throw std::runtime_error("pcapng: " + what + " (" + path_ + ")"); }

bool PcapngReader::ensure(std::size_t n) {
    if (available() >= n) return true;
    if (begin_ > 0) {  // slide the unconsumed tail to the front
        std::memmove(buf_.data(), buf_.data() + begin_, available());
        end_ -= begin_;
        begin_ = 0;
    }
    if (n > buf_.size()) buf_.resize(std::max(n, buf_.size() * 2));  // oversized block only
    while (available() < n) {
        const std::size_t got = src_->read(buf_.data() + end_, buf_.size() - end_, path_);
        if (got == 0) break;
        end_ += got;
    }
    return available() >= n;
}

bool PcapngReader::next(Packet& out) {
    for (;;) {
        if (!ensure(8)) {
            if (src_->truncated)
                fail("gzip stream truncated at offset " + std::to_string(consumed_) +
                     " (input ended before the gzip trailer)");
            if (available() > 0)
                fail("truncated block header at offset " + std::to_string(consumed_) + ": " +
                     std::to_string(available()) + " of 8 bytes");
            if (!in_section_) fail("not a pcapng file: no Section Header Block");
            return false;
        }
        const std::uint32_t type = le32(buf_.data() + begin_);
        if (type == kShb) {
            if (!ensure(12)) fail("truncated Section Header Block at offset " + std::to_string(consumed_));
            const std::uint32_t magic = le32(buf_.data() + begin_ + 8);
            if (magic == kMagicSwapped)
                fail("big-endian pcapng section at offset " + std::to_string(consumed_) + " is not supported");
            if (magic != kMagicLe) fail("bad byte-order magic at offset " + std::to_string(consumed_));
        } else if (!in_section_) {
            fail("not a pcapng file: first block is not a Section Header Block");
        }
        const std::uint32_t len = le32(buf_.data() + begin_ + 4);
        if (len < 12 || len % 4 != 0 || len > kMaxBlock || (type == kShb && len < 28))
            fail("corrupt block length " + std::to_string(len) + " at offset " + std::to_string(consumed_));
        if (!ensure(len)) {
            if (src_->truncated)
                fail("gzip stream truncated inside the block at offset " + std::to_string(consumed_) +
                     " (input ended before the gzip trailer)");
            fail("truncated block at offset " + std::to_string(consumed_) + ": need " + std::to_string(len) +
                 " bytes, " + std::to_string(available()) + " remain");
        }
        const std::uint8_t* b = buf_.data() + begin_;
        if (le32(b + len - 4) != len)
            fail("block length mismatch at offset " + std::to_string(consumed_) + " (corrupt framing)");

        bool emitted = false;
        if (type == kShb) {
            in_section_ = true;
            interfaces_.clear();  // interface ids are per section
        } else if (type == kIdb) {
            parse_idb(b, len);
        } else if (type == kEpb) {
            emitted = parse_epb(b, len, out);
        }  // anything else: skipped by length
        begin_ += len;
        consumed_ += len;
        if (emitted) return true;  // out.udp_payload still points into buf_
    }
}

void PcapngReader::parse_idb(const std::uint8_t* b, std::uint32_t len) {
    if (len < 20) fail("Interface Description Block too short at offset " + std::to_string(consumed_));
    Interface itf{le16(b + 8), false, 6, 0};
    const std::uint8_t* p = b + 16;
    const std::uint8_t* end = b + len - 4;
    while (end - p >= 4) {
        const std::uint16_t code = le16(p);
        const std::uint16_t olen = le16(p + 2);
        if (code == 0) break;
        if (olen > end - p - 4) fail("corrupt IDB option at offset " + std::to_string(consumed_));
        if (code == 9 && olen >= 1) {  // if_tsresol
            itf.binary = (p[4] & 0x80) != 0;
            itf.exp = static_cast<std::uint8_t>(p[4] & 0x7f);
            if ((!itf.binary && itf.exp > 18) || (itf.binary && itf.exp > 63))
                fail("unsupported if_tsresol at offset " + std::to_string(consumed_));
        } else if (code == 14 && olen == 8) {  // if_tsoffset, seconds
            itf.offset_s = static_cast<std::int64_t>(le64(p + 4));
        }
        p += 4 + ((olen + 3u) & ~3u);
    }
    interfaces_.push_back(itf);
}

std::int64_t PcapngReader::to_ns(const Interface& itf, std::uint64_t ts) const {
    std::uint64_t ns;
    if (itf.binary) {
        ns = static_cast<std::uint64_t>((static_cast<unsigned __int128>(ts) * 1'000'000'000u) >> itf.exp);
    } else {
        std::uint64_t scale = 1;
        for (int i = 0; i < (itf.exp <= 9 ? 9 - itf.exp : itf.exp - 9); ++i) scale *= 10;
        ns = itf.exp <= 9 ? ts * scale : ts / scale;
    }
    return static_cast<std::int64_t>(ns) + itf.offset_s * 1'000'000'000;
}

bool PcapngReader::parse_epb(const std::uint8_t* b, std::uint32_t len, Packet& out) {
    if (len < 32) fail("Enhanced Packet Block too short at offset " + std::to_string(consumed_));
    const std::uint32_t if_id = le32(b + 8);
    if (if_id >= interfaces_.size())
        fail("packet on undeclared interface " + std::to_string(if_id) + " at offset " + std::to_string(consumed_));
    const Interface& itf = interfaces_[if_id];
    const std::uint32_t cap = le32(b + 20);
    if (cap > len - 32) fail("captured length overruns its block at offset " + std::to_string(consumed_));
    const std::uint8_t* f = b + 28;

    auto skip = [this] {
        ++skipped_;
        return false;
    };
    if (itf.link_type != kLinkEthernet || cap < 14) return skip();
    std::size_t off = 12;
    std::uint16_t ethertype = be16(f + off);
    while (ethertype == 0x8100 || ethertype == 0x88A8) {  // 802.1Q / QinQ tags
        off += 4;
        if (off + 2 > cap) return skip();
        ethertype = be16(f + off);
    }
    off += 2;
    if (ethertype != 0x0800) return skip();
    const std::uint8_t* ip = f + off;
    const std::size_t rem = cap - off;
    if (rem < 20 || (ip[0] >> 4) != 4) return skip();
    const std::size_t ihl = static_cast<std::size_t>(ip[0] & 0x0f) * 4;
    if (ihl < 20 || (be16(ip + 6) & 0x3fff) != 0 || ip[9] != 17) return skip();  // fragments, non-UDP
    // Lengths come from the headers, not the frame: Ethernet pads short frames.
    const std::size_t ip_total = be16(ip + 2);
    if (ip_total < ihl + 8 || ip_total > rem) return skip();
    const std::uint8_t* udp = ip + ihl;
    const std::size_t udp_len = be16(udp + 4);
    if (udp_len < 8 || ihl + udp_len > ip_total) return skip();
    out.ts_ns = to_ns(itf, (static_cast<std::uint64_t>(le32(b + 12)) << 32) | le32(b + 16));
    out.udp_payload = std::span<const std::uint8_t>(udp + 8, udp_len - 8);
    return true;
}

}  // namespace pkmn::md
