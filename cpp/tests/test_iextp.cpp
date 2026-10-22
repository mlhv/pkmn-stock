#include <catch2/catch_test_macros.hpp>

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
