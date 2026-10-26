#pragma once

// Retransmit channel framing. The multicast UDP payloads travel unchanged;
// epoch and control information ride only on this TCP stream.
//
// Frame: 12-byte little-endian header { u8 type, u8 reserved[3] (zero),
// u32 epoch, u32 body_len }, then the body. Bodies:
//   Request    i64 from, i64 to          half-open [from, to)
//   Retransmit the original UDP payload, unchanged
//   EpochStart i64 prev_end_seq, i64 first_send_time (epoch >= 1 is the new one)
//   End        i64 end_seq
//   Unserved   i64 from, i64 to
// Epochs count from 0; epoch 0 is implicit and has no EpochStart.

#include <cstddef>
#include <cstdint>
#include <span>
#include <vector>

namespace pkmn::md {

enum class FrameType : std::uint8_t { Request = 1, Retransmit = 2, EpochStart = 3, End = 4, Unserved = 5 };

inline constexpr std::size_t kFrameHeaderBytes = 12;
inline constexpr std::size_t kMaxFrameBody = 65535 + 40;

// Appends one frame to out.
void encode_frame(std::vector<std::uint8_t>& out, FrameType t, std::uint32_t epoch,
                  std::span<const std::uint8_t> body);
// Request or Unserved.
void encode_range(std::vector<std::uint8_t>& out, FrameType t, std::uint32_t epoch, std::int64_t from,
                  std::int64_t to);
void encode_epoch_start(std::vector<std::uint8_t>& out, std::uint32_t epoch, std::int64_t prev_end_seq,
                        std::int64_t first_send_time);
void encode_end(std::vector<std::uint8_t>& out, std::uint32_t epoch, std::int64_t end_seq);

struct Frame {
    FrameType type;
    std::uint32_t epoch;
    std::span<const std::uint8_t> body;
};

// Reassembles a TCP byte stream into frames.
class FrameParser {
public:
    void feed(std::span<const std::uint8_t> bytes);
    // Throws std::runtime_error on an unknown type or an oversized body.
    // out.body stays valid until the next feed()/next().
    bool next(Frame& out);

private:
    std::vector<std::uint8_t> buf_;
    std::size_t pos_ = 0;
};

}  // namespace pkmn::md
