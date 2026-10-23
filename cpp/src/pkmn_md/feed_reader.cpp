#include "pkmn_md/feed_reader.hpp"

namespace pkmn::md {

FeedReader::FeedReader(const std::string& path, std::uint16_t protocol_id)
    : reader_(path), protocol_id_(protocol_id) {}

bool FeedReader::next(Segment& out) {
    Packet p{};
    while (!limit_reached() && reader_.next(p)) {
        ++packets_;
        TpHeader& hdr = out.hdr;
        if (!parse_tp_header(p.udp_payload, hdr)) {
            ++tp_parse_failures_;
            continue;
        }
        ++by_protocol_[hdr.protocol_id];
        if (hdr.protocol_id != protocol_id_) continue;
        if (!have_stream_) {
            have_stream_ = true;
            channel_ = hdr.channel_id;
            session_ = hdr.session_id;
            first_seq_ = hdr.first_seq;
        }
        if (hdr.channel_id != channel_ || hdr.session_id != session_) {
            ++other_stream_;
            continue;
        }
        // Framing check before the tracker: an overrunning segment's
        // msg_count is not trusted.
        if (!for_each_message(p.udp_payload, hdr, [](std::span<const std::uint8_t>) {})) {
            ++tp_parse_failures_;
            continue;
        }
        ++segments_;
        if (hdr.msg_count == 0) ++heartbeats_;
        out.expected_before = tracker_.expected();
        out.restart = is_stream_restart(hdr, tracker_.expected(), max_send_time_);
        if (out.restart) tracker_.reset(1);
        if (hdr.send_time > max_send_time_) max_send_time_ = hdr.send_time;
        out.verdict = tracker_.observe(hdr.first_seq, hdr.msg_count);
        out.skip = tracker_.skip();
        last_seq_end_ = hdr.first_seq + hdr.msg_count;
        out.payload = p.udp_payload;
        out.capture_ts_ns = p.ts_ns;
        out.packet_no = packets_;
        return true;
    }
    return false;
}

}  // namespace pkmn::md
