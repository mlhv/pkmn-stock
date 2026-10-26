// Retransmit frame protocol, retransmit ring, seeded drop injector.

#include <catch2/catch_test_macros.hpp>

#include <cmath>
#include <cstdint>
#include <stdexcept>
#include <vector>

#include "pkmn_md/drop_injector.hpp"
#include "pkmn_md/recovery_proto.hpp"
#include "pkmn_md/retransmit_ring.hpp"

using namespace pkmn::md;

namespace {
using Bytes = std::vector<std::uint8_t>;
std::int64_t rd64(const std::uint8_t* p) {
    std::uint64_t v = 0;
    for (int i = 0; i < 8; ++i) v |= static_cast<std::uint64_t>(p[i]) << (8 * i);
    return static_cast<std::int64_t>(v);
}
Bytes payload(std::uint8_t tag, std::size_t n = 10) { return Bytes(n, tag); }
}  // namespace

TEST_CASE("proto: every frame type round-trips, header is 12 bytes LE") {
    Bytes s;
    encode_range(s, FrameType::Request, 0x01020304, 5, 9);
    REQUIRE(s.size() == kFrameHeaderBytes + 16);
    CHECK(s[0] == 1);
    CHECK(s[1] == 0);
    CHECK(s[2] == 0);
    CHECK(s[3] == 0);
    CHECK(s[4] == 0x04);
    CHECK(s[5] == 0x03);
    CHECK(s[6] == 0x02);
    CHECK(s[7] == 0x01);
    CHECK(s[8] == 16);
    CHECK(s[9] == 0);
    const Bytes pl = {9, 8, 7};
    encode_frame(s, FrameType::Retransmit, 2, pl);
    encode_epoch_start(s, 3, 100, 200);
    encode_end(s, 3, 777);
    encode_range(s, FrameType::Unserved, 3, 1, 2);

    FrameParser p;
    p.feed(s);
    Frame f;
    REQUIRE(p.next(f));
    CHECK(f.type == FrameType::Request);
    CHECK(f.epoch == 0x01020304);
    CHECK(rd64(f.body.data()) == 5);
    CHECK(rd64(f.body.data() + 8) == 9);
    REQUIRE(p.next(f));
    CHECK(f.type == FrameType::Retransmit);
    CHECK(f.epoch == 2);
    CHECK(Bytes(f.body.begin(), f.body.end()) == pl);
    REQUIRE(p.next(f));
    CHECK(f.type == FrameType::EpochStart);
    CHECK(f.epoch == 3);
    CHECK(rd64(f.body.data()) == 100);
    CHECK(rd64(f.body.data() + 8) == 200);
    REQUIRE(p.next(f));
    CHECK(f.type == FrameType::End);
    CHECK(rd64(f.body.data()) == 777);
    REQUIRE(p.next(f));
    CHECK(f.type == FrameType::Unserved);
    CHECK(rd64(f.body.data()) == 1);
    CHECK(rd64(f.body.data() + 8) == 2);
    CHECK_FALSE(p.next(f));
}

TEST_CASE("proto: parser reassembles frames split at every byte boundary") {
    Bytes s;
    encode_range(s, FrameType::Request, 1, 10, 20);
    encode_frame(s, FrameType::Retransmit, 1, payload(0xAB, 50));
    encode_end(s, 1, 99);
    FrameParser p;
    std::vector<FrameType> got;
    std::vector<std::size_t> lens;
    for (std::uint8_t b : s) {
        p.feed(std::span<const std::uint8_t>(&b, 1));
        Frame f;
        while (p.next(f)) {
            got.push_back(f.type);
            lens.push_back(f.body.size());
        }
    }
    REQUIRE(got.size() == 3);
    CHECK(got[0] == FrameType::Request);
    CHECK(got[1] == FrameType::Retransmit);
    CHECK(got[2] == FrameType::End);
    CHECK(lens == std::vector<std::size_t>{16, 50, 8});
}

TEST_CASE("proto: unknown type throws") {
    Bytes s(kFrameHeaderBytes, 0);
    s[0] = 9;
    FrameParser p;
    p.feed(s);
    Frame f;
    CHECK_THROWS_AS(p.next(f), std::runtime_error);

    Bytes big(kFrameHeaderBytes, 0);
    big[0] = 2;
    const std::uint32_t len = 65535 + 41;
    for (int i = 0; i < 4; ++i) big[8 + i] = static_cast<std::uint8_t>(len >> (8 * i));
    FrameParser q;
    q.feed(big);
    CHECK_THROWS_AS(q.next(f), std::runtime_error);
}

TEST_CASE("ring: collect covers exact, straddling and multi-packet ranges") {
    RetransmitRing r(1000);
    r.store(0, 1, 3, payload(1));  // [1,4)
    r.store(0, 4, 2, payload(2));  // [4,6)
    r.store(0, 6, 4, payload(3));  // [6,10)
    r.store(0, 10, 0, payload(4)); // heartbeat ignored
    CHECK(r.bytes() == 30);

    std::vector<std::span<const std::uint8_t>> out;
    REQUIRE(r.collect(0, 4, 6, out));  // exact
    REQUIRE(out.size() == 1);
    CHECK(out[0][0] == 2);

    out.clear();
    REQUIRE(r.collect(0, 5, 7, out));  // straddles two packets
    REQUIRE(out.size() == 2);
    CHECK(out[0][0] == 2);
    CHECK(out[1][0] == 3);

    out.clear();
    REQUIRE(r.collect(0, 1, 10, out));  // multi
    REQUIRE(out.size() == 3);
    CHECK(out[0][0] == 1);
    CHECK(out[2][0] == 3);

    out.clear();
    CHECK_FALSE(r.collect(0, 8, 12, out));  // past the end
    CHECK(out.empty());
    CHECK_FALSE(r.collect(0, 0, 3, out));  // before the start
    CHECK(out.empty());
}

TEST_CASE("ring: a hole in the stored packets is not covered") {
    RetransmitRing r(1000);
    r.store(0, 1, 2, payload(1));  // [1,3)
    r.store(0, 5, 2, payload(2));  // [5,7)
    std::vector<std::span<const std::uint8_t>> out;
    CHECK_FALSE(r.collect(0, 1, 7, out));
    CHECK(out.empty());
}

TEST_CASE("ring: epochs are separate") {
    RetransmitRing r(1000);
    r.store(0, 1, 5, payload(0xA));
    r.store(1, 1, 5, payload(0xB));
    std::vector<std::span<const std::uint8_t>> out;
    REQUIRE(r.collect(1, 1, 6, out));
    REQUIRE(out.size() == 1);
    CHECK(out[0][0] == 0xB);
    out.clear();
    REQUIRE(r.collect(0, 1, 6, out));
    CHECK(out[0][0] == 0xA);
    out.clear();
    CHECK_FALSE(r.collect(2, 1, 6, out));
}

TEST_CASE("ring: evicted range is not covered") {
    RetransmitRing r(20);  // fits two 10-byte payloads
    r.store(0, 1, 2, payload(1));
    r.store(0, 3, 2, payload(2));
    r.store(0, 5, 2, payload(3));
    CHECK(r.evicted() == 1);
    CHECK(r.bytes() == 20);
    std::vector<std::span<const std::uint8_t>> out;
    CHECK_FALSE(r.collect(0, 1, 3, out));
    CHECK(out.empty());
    REQUIRE(r.collect(0, 3, 7, out));
    CHECK(out.size() == 2);
}

TEST_CASE("drop: same seed same pattern; random rate within 3 sigma over 1e5") {
    const auto spec = parse_drop_spec("random:0.1", 42);
    REQUIRE(spec);
    DropInjector a(*spec), b(*spec);
    constexpr int n = 100000;
    for (int i = 0; i < n; ++i) CHECK(a.drop() == b.drop());
    const double mean = 0.1 * n;
    const double sigma = std::sqrt(n * 0.1 * 0.9);
    CHECK(std::abs(static_cast<double>(a.dropped()) - mean) < 3 * sigma);
    CHECK(a.dropped() == b.dropped());
    DropInjector none(*parse_drop_spec("none", 1));
    for (int i = 0; i < 100; ++i) CHECK_FALSE(none.drop());
    CHECK(none.dropped() == 0);
}

TEST_CASE("drop: burst drops exactly burst_len consecutive") {
    const auto spec = parse_drop_spec("burst:0.01:5", 7);
    REQUIRE(spec);
    DropInjector d(*spec);
    int run = 0;
    int runs = 0;
    for (int i = 0; i < 20000; ++i) {
        if (d.drop()) {
            ++run;
        } else if (run > 0) {
            CHECK(run % 5 == 0);  // back-to-back bursts merge in multiples of 5
            ++runs;
            run = 0;
        }
    }
    CHECK(d.bursts() > 0);
    CHECK(d.dropped() == d.bursts() * 5);
    CHECK(runs > 0);
    // p = 1 starts a burst immediately and drops everything in 3s.
    DropInjector all(*parse_drop_spec("burst:1:3", 1));
    for (int i = 0; i < 9; ++i) CHECK(all.drop());
    CHECK(all.bursts() == 3);
}

TEST_CASE("drop: parse rejects p>1, len 0, garbage") {
    CHECK_FALSE(parse_drop_spec("random:1.5", 1));
    CHECK_FALSE(parse_drop_spec("random:-0.1", 1));
    CHECK_FALSE(parse_drop_spec("random:abc", 1));
    CHECK_FALSE(parse_drop_spec("burst:0.1:0", 1));
    CHECK_FALSE(parse_drop_spec("burst:0.1", 1));
    CHECK_FALSE(parse_drop_spec("burst:2:3", 1));
    CHECK_FALSE(parse_drop_spec("burst:0.1:x", 1));
    CHECK_FALSE(parse_drop_spec("garbage", 1));
    CHECK_FALSE(parse_drop_spec("", 1));
    CHECK(parse_drop_spec("random:0", 1));
    CHECK(parse_drop_spec("random:1", 1));
    const auto b = parse_drop_spec("burst:0.2:4", 9);
    REQUIRE(b);
    CHECK(b->mode == DropSpec::Mode::Burst);
    CHECK(b->burst_len == 4);
    CHECK(b->seed == 9);
}
