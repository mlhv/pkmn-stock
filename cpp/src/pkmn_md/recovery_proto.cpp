#include "pkmn_md/recovery_proto.hpp"

#include <stdexcept>

namespace pkmn::md {

namespace {

void put32(std::vector<std::uint8_t>& o, std::uint32_t v) {
    for (int i = 0; i < 4; ++i) o.push_back(static_cast<std::uint8_t>(v >> (8 * i)));
}
void put64(std::vector<std::uint8_t>& o, std::int64_t sv) {
    auto v = static_cast<std::uint64_t>(sv);
    for (int i = 0; i < 8; ++i) o.push_back(static_cast<std::uint8_t>(v >> (8 * i)));
}
std::uint32_t get32(const std::uint8_t* p) {
    std::uint32_t v = 0;
    for (int i = 0; i < 4; ++i) v |= static_cast<std::uint32_t>(p[i]) << (8 * i);
    return v;
}

}  // namespace

void encode_frame(std::vector<std::uint8_t>& out, FrameType t, std::uint32_t epoch,
                  std::span<const std::uint8_t> body) {
    out.reserve(out.size() + kFrameHeaderBytes + body.size());
    out.push_back(static_cast<std::uint8_t>(t));
    out.push_back(0);
    out.push_back(0);
    out.push_back(0);
    put32(out, epoch);
    put32(out, static_cast<std::uint32_t>(body.size()));
    out.insert(out.end(), body.begin(), body.end());
}

void encode_range(std::vector<std::uint8_t>& out, FrameType t, std::uint32_t epoch, std::int64_t from,
                  std::int64_t to) {
    std::vector<std::uint8_t> b;
    put64(b, from);
    put64(b, to);
    encode_frame(out, t, epoch, b);
}

void encode_epoch_start(std::vector<std::uint8_t>& out, std::uint32_t epoch, std::int64_t prev_end_seq,
                        std::int64_t first_send_time) {
    std::vector<std::uint8_t> b;
    put64(b, prev_end_seq);
    put64(b, first_send_time);
    encode_frame(out, FrameType::EpochStart, epoch, b);
}

void encode_end(std::vector<std::uint8_t>& out, std::uint32_t epoch, std::int64_t end_seq) {
    std::vector<std::uint8_t> b;
    put64(b, end_seq);
    encode_frame(out, FrameType::End, epoch, b);
}

void FrameParser::feed(std::span<const std::uint8_t> bytes) {
    if (pos_ > 0) {
        buf_.erase(buf_.begin(), buf_.begin() + static_cast<std::ptrdiff_t>(pos_));
        pos_ = 0;
    }
    buf_.insert(buf_.end(), bytes.begin(), bytes.end());
}

bool FrameParser::next(Frame& out) {
    const std::size_t avail = buf_.size() - pos_;
    if (avail < kFrameHeaderBytes) return false;
    const std::uint8_t* h = buf_.data() + pos_;
    if (h[0] < 1 || h[0] > 5) throw std::runtime_error("recovery frame: unknown type");
    const std::uint32_t len = get32(h + 8);
    if (len > kMaxFrameBody) throw std::runtime_error("recovery frame: body too large");
    if (avail < kFrameHeaderBytes + len) return false;
    out.type = static_cast<FrameType>(h[0]);
    out.epoch = get32(h + 4);
    out.body = std::span<const std::uint8_t>(h + kFrameHeaderBytes, len);
    pos_ += kFrameHeaderBytes + len;
    return true;
}

}  // namespace pkmn::md
