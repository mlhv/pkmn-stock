#include <catch2/catch_test_macros.hpp>

#include <algorithm>
#include <cstdint>
#include <span>
#include <string>
#include <vector>

#include "md_fixtures.hpp"
#include "pkmn_md/iextp.hpp"
#include "pkmn_md/sequence.hpp"

using namespace pkmn::md;
using namespace md_fixtures;

namespace {

std::vector<std::string> split(const Bytes& seg, TpHeader& hdr, bool& ok) {
    std::vector<std::string> out;
    ok = for_each_message(std::span<const std::uint8_t>(seg), hdr, [&](std::span<const std::uint8_t> m) {
        out.emplace_back(m.begin(), m.end());
    });
    return out;
}

}  // namespace

TEST_CASE("iextp: parses header and splits messages") {
    const Bytes seg = tp_segment(kDeepPlus, 7, {bytes_of("hello"), bytes_of("abc")});
    TpHeader hdr{};
    bool ok = false;
    auto msgs = split(seg, hdr, ok);
    REQUIRE(ok);
    REQUIRE(msgs.size() == 2);
    CHECK(msgs[0] == "hello");
    CHECK(msgs[1] == "abc");
    CHECK(hdr.version == 1);
    CHECK(hdr.protocol_id == 0x8005);
    CHECK(hdr.channel_id == 1);
    CHECK(hdr.session_id == 0x42424242u);
    CHECK(hdr.payload_len == 12);
    CHECK(hdr.msg_count == 2);
    CHECK(hdr.stream_offset == 1000);
    CHECK(hdr.first_seq == 7);
    CHECK(hdr.send_time == 1'734'964'524'000'000'000LL);
}

TEST_CASE("iextp: heartbeat (no messages) parses with zero calls") {
    const Bytes seg = tp_segment(kDeepPlus, 12, {});
    TpHeader hdr{};
    bool ok = false;
    auto msgs = split(seg, hdr, ok);
    CHECK(ok);
    CHECK(msgs.empty());
    CHECK(hdr.msg_count == 0);
    CHECK(hdr.first_seq == 12);
}

TEST_CASE("iextp: overrun returns false") {
    TpHeader hdr{};
    bool ok = true;

    SECTION("message length runs past the payload") {
        Bytes seg = tp_segment(kDeepPlus, 1, {bytes_of("hello"), bytes_of("abc")});
        seg.pop_back();  // last message now one byte short; payload_len still claims it
        auto msgs = split(seg, hdr, ok);
        CHECK_FALSE(ok);
        CHECK(msgs.empty());  // validated before any callback
    }
    SECTION("payload_len exceeds the bytes present") {
        const Bytes seg = tp_segment(kDeepPlus, 1, {bytes_of("hello")}, 100);
        split(seg, hdr, ok);
        CHECK_FALSE(ok);
    }
    SECTION("msg_count exceeds the messages in payload_len") {
        const Bytes seg = tp_segment(kDeepPlus, 1, {bytes_of("hello")}, -1, 2);
        split(seg, hdr, ok);
        CHECK_FALSE(ok);
    }
    SECTION("shorter than the 40-byte header") {
        Bytes seg = tp_segment(kDeepPlus, 1, {});
        seg.resize(39);
        split(seg, hdr, ok);
        CHECK_FALSE(ok);
    }
}

TEST_CASE("iextp: protocol ids") {
    CHECK(kDeepPlus == 0x8005);
    CHECK(kDeep == 0x8004);
    CHECK(kTops == 0x8003);  // TOPS spec v1.66 p.3, "Message Protocol ID: 0x8003"
}

TEST_CASE("sequence tracker: next, duplicate, straddle, gap, heartbeat") {
    SequenceTracker t;
    CHECK(t.observe(1, 3) == SeqVerdict::Next);
    CHECK(t.expected() == 4);
    CHECK(t.skip() == 0);

    CHECK(t.observe(1, 3) == SeqVerdict::Duplicate);
    CHECK(t.expected() == 4);
    CHECK(t.skip() == 3);
    CHECK(t.duplicates() == 3);

    CHECK(t.observe(3, 3) == SeqVerdict::Duplicate);  // 3 is old, 4 and 5 are new
    CHECK(t.expected() == 6);
    CHECK(t.skip() == 1);
    CHECK(t.duplicates() == 4);

    CHECK(t.observe(10, 1) == SeqVerdict::Gap);
    CHECK(t.gaps() == 1);
    CHECK(t.gap_messages() == 4);  // 6, 7, 8, 9
    CHECK(t.expected() == 11);
    CHECK(t.skip() == 0);

    CHECK(t.observe(11, 0) == SeqVerdict::Next);
    CHECK(t.expected() == 11);
    CHECK(t.gaps() == 1);
    CHECK(t.duplicates() == 4);
}

TEST_CASE("sequence tracker: heartbeat behind or ahead of expected") {
    SequenceTracker t;
    CHECK(t.observe(1, 5) == SeqVerdict::Next);  // expected 6
    CHECK(t.observe(4, 0) == SeqVerdict::Duplicate);
    CHECK(t.duplicates() == 0);  // a heartbeat carries no messages to count
    CHECK(t.observe(8, 0) == SeqVerdict::Gap);  // 6 and 7 never arrived
    CHECK(t.gap_messages() == 2);
    CHECK(t.expected() == 8);
}

TEST_CASE("sequence tracker: default start is 1, a late start is a gap") {
    SequenceTracker t;
    CHECK(t.expected() == 1);
    CHECK(t.observe(5, 1) == SeqVerdict::Gap);
    CHECK(t.gap_messages() == 4);

    SequenceTracker joined(5);  // a live join mid-session declares where it starts
    CHECK(joined.observe(5, 1) == SeqVerdict::Next);
    CHECK(joined.gaps() == 0);
}

TEST_CASE("sequence tracker: reset changes only expected and counts resets") {
    SequenceTracker t;
    CHECK(t.observe(1, 3) == SeqVerdict::Next);
    CHECK(t.observe(1, 3) == SeqVerdict::Duplicate);
    CHECK(t.observe(10, 2) == SeqVerdict::Gap);  // expected 12, gap 6 messages
    CHECK(t.resets() == 0);
    t.reset(1);
    CHECK(t.resets() == 1);
    CHECK(t.expected() == 1);
    CHECK(t.gaps() == 1);
    CHECK(t.gap_messages() == 6);
    CHECK(t.duplicates() == 3);
    CHECK(t.observe(1, 2) == SeqVerdict::Next);
    CHECK(t.expected() == 3);
}

namespace {

TpHeader header_of(const Bytes& seg) {
    TpHeader h{};
    REQUIRE(parse_tp_header(std::span<const std::uint8_t>(seg), h));
    return h;
}

// tp_segment's fixed send time, overwritten at header offset 32.
Bytes with_send_time(Bytes seg, std::int64_t send_time) {
    for (int i = 0; i < 8; ++i) seg[32 + i] = static_cast<std::uint8_t>(static_cast<std::uint64_t>(send_time) >> (8 * i));
    return seg;
}

}  // namespace

TEST_CASE("stream restart: a newer-send-time seq-1 packet resets and is applied") {
    SequenceTracker t;
    std::int64_t max_send = 0;
    const Bytes morning = with_send_time(tp_segment(kDeepPlus, 1, {bytes_of("a"), bytes_of("b"), bytes_of("c")}), 100);
    TpHeader h = header_of(morning);
    CHECK_FALSE(is_stream_restart(h, t.expected(), max_send));  // expected is still 1: the normal start
    CHECK(t.observe(h.first_seq, h.msg_count) == SeqVerdict::Next);
    max_send = std::max(max_send, h.send_time);

    const Bytes restart = with_send_time(tp_segment(kDeepPlus, 1, {bytes_of("x"), bytes_of("y")}), 200);
    h = header_of(restart);
    REQUIRE(is_stream_restart(h, t.expected(), max_send));
    t.reset(1);
    CHECK(t.observe(h.first_seq, h.msg_count) == SeqVerdict::Next);
    CHECK(t.skip() == 0);  // every message of the restart packet is applied
    CHECK(t.expected() == 3);
    CHECK(t.resets() == 1);
    CHECK(t.duplicates() == 0);
}

TEST_CASE("stream restart: a replayed seq-1 packet with an older send time stays Duplicate") {
    SequenceTracker t;
    std::int64_t max_send = 0;
    const Bytes first = with_send_time(tp_segment(kDeepPlus, 1, {bytes_of("a"), bytes_of("b")}), 100);
    const Bytes later = with_send_time(tp_segment(kDeepPlus, 3, {bytes_of("c")}), 150);
    for (const Bytes* seg : {&first, &later}) {
        const TpHeader h = header_of(*seg);
        CHECK(t.observe(h.first_seq, h.msg_count) == SeqVerdict::Next);
        max_send = std::max(max_send, h.send_time);
    }
    for (std::int64_t replay_send : {std::int64_t{100}, std::int64_t{150}}) {  // older, and equal
        const TpHeader h = header_of(with_send_time(first, replay_send));
        CHECK_FALSE(is_stream_restart(h, t.expected(), max_send));
    }
    const TpHeader h = header_of(first);
    CHECK(t.observe(h.first_seq, h.msg_count) == SeqVerdict::Duplicate);
    CHECK(t.skip() == 2);
    CHECK(t.resets() == 0);
}
