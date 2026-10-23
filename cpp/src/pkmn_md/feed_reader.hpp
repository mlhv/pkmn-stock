#pragma once

// One IEX HIST feed, read segment by segment: pcapng -> IEX-TP header ->
// protocol and stream filter -> framing check -> stream-restart policy ->
// sequence tracker. This is the driver loop iex_decode and iex_oracle share,
// so both apply exactly the same rules:
//
//   * Packets whose IEX-TP header does not parse, and segments whose framing
//     overruns (for_each_message false), are counted as tp_parse_failures and
//     never reach the tracker (their msg_count is not trusted).
//   * Segments of another Message Protocol ID are counted (by_protocol) and
//     skipped.
//   * The first accepted segment fixes the (channel, session); segments from
//     any other stream are counted as other_stream and skipped.
//   * A seq-1 segment with a send_time newer than any seen is a stream restart
//     (is_stream_restart): the tracker is reset to 1 and Segment::restart is
//     set, so the caller can drop state built from the old epoch.
//   * The caller applies messages with index >= Segment::skip (see
//     for_each_applied); duplicate messages are never applied twice.
//
// Heartbeats (msg_count 0) are returned like any other segment.

#include <cstdint>
#include <map>
#include <span>
#include <string>

#include "pkmn_md/iextp.hpp"
#include "pkmn_md/pcapng.hpp"
#include "pkmn_md/sequence.hpp"

namespace pkmn::md {

struct Segment {
    TpHeader hdr;
    std::span<const std::uint8_t> payload;  // the whole UDP payload; valid until the next next()
    std::int64_t capture_ts_ns;             // pcapng capture time, NOT feed time
    std::uint64_t packet_no;                // 1-based UDP packet index in the file
    SeqVerdict verdict;
    std::uint16_t skip;            // leading messages already seen (tracker.skip())
    bool restart;                  // this segment began a new sequence epoch
    std::int64_t expected_before;  // tracker.expected() before this segment (before any reset)
};

// The per-packet half of FeedReader: everything after the pcapng layer.
// Feed it every UDP payload in file order; it applies the protocol filter,
// stream filter, framing check, restart policy and sequence tracker
// described above. FeedReader is a PcapngReader plus one of these; iex_bench
// drives one directly over payloads held in memory, so a replay from RAM
// applies exactly the rules a streamed read does.
class SegmentFilter {
  public:
    explicit SegmentFilter(std::uint16_t protocol_id) : protocol_id_(protocol_id) {}

    // True if `udp_payload` is an accepted segment; `out` is then filled
    // (out.payload aliases `udp_payload`). `packet_no` is the caller's
    // 1-based packet index, copied into out.packet_no.
    bool accept(std::span<const std::uint8_t> udp_payload, std::int64_t capture_ts_ns, std::uint64_t packet_no,
                Segment& out);

    std::uint64_t tp_parse_failures() const { return tp_parse_failures_; }
    std::uint64_t other_stream() const { return other_stream_; }
    std::uint64_t segments() const { return segments_; }
    std::uint64_t heartbeats() const { return heartbeats_; }
    const std::map<std::uint16_t, std::uint64_t>& by_protocol() const { return by_protocol_; }
    bool have_stream() const { return have_stream_; }
    std::uint32_t channel() const { return channel_; }
    std::uint32_t session() const { return session_; }
    std::int64_t first_seq() const { return first_seq_; }
    std::int64_t last_seq_end() const { return last_seq_end_; }
    const SequenceTracker& tracker() const { return tracker_; }

  private:
    std::uint16_t protocol_id_;
    std::uint64_t tp_parse_failures_ = 0, other_stream_ = 0, segments_ = 0, heartbeats_ = 0;
    std::map<std::uint16_t, std::uint64_t> by_protocol_;  // a handful of keys
    SequenceTracker tracker_;
    bool have_stream_ = false;
    std::uint32_t channel_ = 0, session_ = 0;
    std::int64_t first_seq_ = 0, last_seq_end_ = 0, max_send_time_ = 0;
};

class FeedReader {
  public:
    // Throws std::runtime_error as PcapngReader does.
    FeedReader(const std::string& path, std::uint16_t protocol_id);

    // Stop after this many UDP packets (0 = no limit).
    void set_packet_limit(std::uint64_t n) { limit_ = n; }

    // Next accepted segment of `protocol_id` on the followed stream. False at
    // EOF or when the packet limit is reached.
    bool next(Segment& out);

    std::uint64_t packets() const { return packets_; }
    bool limit_reached() const { return limit_ != 0 && packets_ >= limit_; }
    std::uint64_t skipped_frames() const { return reader_.skipped_frames(); }
    std::uint64_t tp_parse_failures() const { return filter_.tp_parse_failures(); }
    std::uint64_t other_stream() const { return filter_.other_stream(); }
    std::uint64_t segments() const { return filter_.segments(); }
    std::uint64_t heartbeats() const { return filter_.heartbeats(); }
    const std::map<std::uint16_t, std::uint64_t>& by_protocol() const { return filter_.by_protocol(); }
    bool have_stream() const { return filter_.have_stream(); }
    std::uint32_t channel() const { return filter_.channel(); }
    std::uint32_t session() const { return filter_.session(); }
    std::int64_t first_seq() const { return filter_.first_seq(); }
    std::int64_t last_seq_end() const { return filter_.last_seq_end(); }
    const SequenceTracker& tracker() const { return filter_.tracker(); }

  private:
    PcapngReader reader_;
    SegmentFilter filter_;
    std::uint64_t limit_ = 0, packets_ = 0;
};

// Calls f(message) for each message of `seg` with index >= seg.skip, in order.
template <class F>
void for_each_applied(const Segment& seg, F&& f) {
    if (seg.skip >= seg.hdr.msg_count) return;
    TpHeader h{};
    std::uint16_t idx = 0;
    for_each_message(seg.payload, h, [&](std::span<const std::uint8_t> m) {
        if (idx++ >= seg.skip) f(m);
    });
}

}  // namespace pkmn::md
