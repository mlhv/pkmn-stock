// RecoveringHandler: the in-process gap recovery core. Every case drives the
// handler with a scripted datagram sequence (the publisher's emission order,
// with drops, reorders or extra copies), serves its retransmit requests from
// a RetransmitRing, and compares the per-epoch digests against the direct
// file replay (reference_digests) of the same segments.

#include <catch2/catch_test_macros.hpp>

#include <cstdint>
#include <memory>
#include <numeric>
#include <span>
#include <string>
#include <vector>

#include "feed_fixtures.hpp"
#include "md_fixtures.hpp"
#include "pkmn_md/book_digest.hpp"
#include "pkmn_md/drop_injector.hpp"
#include "pkmn_md/feed_reader.hpp"
#include "pkmn_md/gap_recovery.hpp"
#include "pkmn_md/iextp.hpp"
#include "pkmn_md/replay.hpp"
#include "pkmn_md/retransmit_ring.hpp"

using namespace pkmn::md;
using feed_fixtures::FeedSegment;
using md_fixtures::Bytes;

namespace {

struct Req {
    std::uint32_t epoch;
    std::int64_t from, to;
    bool operator==(const Req&) const = default;
};

struct ScriptSink : RequestSink {
    std::vector<Req> reqs;
    void request(std::uint32_t epoch, std::int64_t from, std::int64_t to) override {
        reqs.push_back({epoch, from, to});
    }
};

struct Ev {
    enum class Kind { Datagram, EpochStart, End } kind;
    Bytes bytes;  // Datagram
    std::uint32_t epoch = 0;
    std::int64_t a = 0, b = 0;  // EpochStart: prev_end_seq, first_send_time; End: end_seq
};

Ev datagram(const Bytes& b) { return {Ev::Kind::Datagram, b}; }

// What the publisher emits: each non-dropped segment as a datagram, the
// EpochStart frame immediately before the first send of a new epoch (sent
// even when that segment is dropped), and End after the last.
std::vector<Ev> publisher_events(const std::vector<FeedSegment>& segs, const std::vector<bool>& drop) {
    std::vector<Ev> out;
    std::int64_t end = 1;
    for (std::size_t i = 0; i < segs.size(); ++i) {
        const FeedSegment& s = segs[i];
        if (i > 0 && s.epoch != segs[i - 1].epoch) {
            out.push_back({Ev::Kind::EpochStart, {}, s.epoch, end, s.send_time});
            end = 1;
        }
        if (!drop[i]) out.push_back(datagram(s.bytes));
        end = std::max(end, s.first_seq + s.count);
    }
    out.push_back({Ev::Kind::End, {}, segs.back().epoch, end, 0});
    return out;
}

std::size_t find_datagram(const std::vector<Ev>& evs, const Bytes& b) {
    for (std::size_t i = 0; i < evs.size(); ++i) {
        if (evs[i].kind == Ev::Kind::Datagram && evs[i].bytes == b) return i;
    }
    FAIL("datagram not found");
    return 0;
}

struct Harness {
    ScriptSink sink;
    RetransmitRing ring{std::size_t{1} << 26};
    RecoveringHandler h{sink};
    std::size_t served = 0;

    void deliver(const Ev& e) {
        switch (e.kind) {
            case Ev::Kind::Datagram:
                h.on_datagram(e.bytes);
                break;
            case Ev::Kind::EpochStart:
                h.on_epoch_start(e.epoch, e.a, e.b);
                break;
            case Ev::Kind::End:
                h.on_end(e.epoch, e.a);
                break;
        }
    }
    // Serve pending requests from the ring until none are left.
    void serve() {
        while (served < sink.reqs.size() && !h.failed()) {
            const Req r = sink.reqs[served++];
            std::vector<std::span<const std::uint8_t>> pkts;
            if (ring.collect(r.epoch, r.from, r.to, pkts)) {
                for (auto p : pkts) h.on_retransmit(r.epoch, p);
            } else {
                h.on_unserved(r.epoch, r.from, r.to);
            }
        }
    }
    void run(const std::vector<Ev>& evs) {
        for (const Ev& e : evs) deliver(e);
        serve();
    }
};

void fill_ring(Harness& hs, const std::vector<FeedSegment>& segs, std::size_t skip_index = SIZE_MAX) {
    for (std::size_t i = 0; i < segs.size(); ++i) {
        if (i != skip_index) hs.ring.store(segs[i].epoch, segs[i].first_seq, segs[i].count, segs[i].bytes);
    }
}

std::unique_ptr<Harness> run_events(const std::vector<FeedSegment>& segs, const std::vector<Ev>& evs) {
    auto hs = std::make_unique<Harness>();
    fill_ring(*hs, segs);
    hs->run(evs);
    return hs;
}

std::unique_ptr<Harness> run_with_loss(const std::vector<FeedSegment>& segs, const std::vector<bool>& drop) {
    return run_events(segs, publisher_events(segs, drop));
}

std::vector<EpochDigest> reference(const std::vector<FeedSegment>& segs) {
    md_fixtures::TempFile f(".pcap.gz");
    feed_fixtures::write_feed_capture(f.path, segs);
    return reference_digests(f.path);
}

std::uint64_t total_messages(const std::vector<EpochDigest>& d) {
    return std::accumulate(d.begin(), d.end(), std::uint64_t{0},
                           [](std::uint64_t s, const EpochDigest& e) { return s + e.messages; });
}

void require_matches(const RecoveringHandler& h, const std::vector<EpochDigest>& ref) {
    CHECK_FALSE(h.failed());
    CHECK(h.done());
    const auto& got = h.digests();
    REQUIRE(got.size() == ref.size());
    for (std::size_t i = 0; i < ref.size(); ++i) {
        INFO("epoch " << i);
        CHECK(got[i].digest == ref[i].digest);
        CHECK(got[i].orders == ref[i].orders);
        CHECK(got[i].messages == ref[i].messages);
    }
    CHECK(h.stats().messages_applied == total_messages(ref));
}

// One segment carrying the messages of `a` then `b`, at a's first_seq and send time.
Bytes merged(const FeedSegment& a, const FeedSegment& b) {
    std::vector<Bytes> msgs;
    TpHeader hdr{};
    for (const FeedSegment* s : {&a, &b}) {
        const bool ok = for_each_message(s->bytes, hdr, [&](std::span<const std::uint8_t> m) {
            msgs.emplace_back(m.begin(), m.end());
        });
        REQUIRE(ok);
    }
    return md_fixtures::tp_segment(kDeepPlus, a.first_seq, msgs, -1, -1, a.send_time);
}

constexpr std::size_t kRestart = 1500;

}  // namespace

TEST_CASE("recovery: fixture is two epochs with a seq-1 restart") {
    const auto segs = feed_fixtures::synthetic_feed();
    REQUIRE(segs.size() == 2000);
    CHECK(segs[kRestart].epoch == 1);
    CHECK(segs[kRestart].first_seq == 1);
    CHECK(segs[kRestart - 1].epoch == 0);
    const auto ref = reference(segs);
    REQUIRE(ref.size() == 2);
    CHECK(ref[0].orders > 0);
    CHECK(ref[1].orders > 0);

    // Every modify, delete and execute names a live order, so the direct
    // replay sees no anomaly, and each message kind actually occurs.
    md_fixtures::TempFile f(".pcap.gz");
    feed_fixtures::write_feed_capture(f.path, segs);
    FeedReader reader(f.path, kDeepPlus);
    BookSet books;
    BookEvent ev{};
    DecodeStats st;
    Segment seg;
    while (reader.next(seg)) apply_deep_plus_segment(seg, books, ev, st);
    CHECK(reader.tracker().gaps() == 0);
    const auto an = books.total_anomalies();
    CHECK(an.unknown_id == 0);
    CHECK(an.duplicate_add == 0);
    CHECK(an.exec_clamped == 0);
    for (unsigned char t : {'a', 'M', 'R', 'L', 'T'}) {
        INFO(t);
        CHECK(st.by_type[t] > 0);
    }
}

TEST_CASE("recovery: lossless stream matches reference") {
    const auto segs = feed_fixtures::synthetic_feed();
    const auto hs = run_with_loss(segs, std::vector<bool>(segs.size(), false));
    require_matches(hs->h, reference(segs));
    const RecoveryStats& st = hs->h.stats();
    CHECK(st.datagrams == segs.size());
    CHECK(st.segments_applied == segs.size());
    CHECK(st.gaps == 0);
    CHECK(st.requests == 0);
    CHECK(st.duplicate_messages == 0);
    CHECK(st.parked == 0);
    CHECK(st.rejected == 0);
    CHECK(st.epochs == 2);
    CHECK(st.held_max == 0);
}

TEST_CASE("recovery: single mid-epoch gap is requested once and repaired") {
    const auto segs = feed_fixtures::synthetic_feed();
    std::vector<bool> drop(segs.size(), false);
    drop[100] = true;
    const auto hs = run_with_loss(segs, drop);
    require_matches(hs->h, reference(segs));
    CHECK(hs->h.stats().gaps == 1);
    CHECK(hs->h.stats().requests == 1);
    REQUIRE(hs->sink.reqs.size() == 1);
    CHECK(hs->sink.reqs[0] == Req{0, segs[100].first_seq, segs[101].first_seq});
    CHECK(hs->h.stats().requested_messages == segs[100].count);
    CHECK(hs->h.stats().retransmit_segments == 1);
    CHECK(hs->h.stats().duplicate_messages == 0);
    CHECK(hs->h.stats().held_max > 0);
}

TEST_CASE("recovery: out-of-order arrival fills the hole without a duplicate apply") {
    const auto segs = feed_fixtures::synthetic_feed();
    constexpr std::size_t N = 200;
    auto evs = publisher_events(segs, std::vector<bool>(segs.size(), false));
    const std::size_t i = find_datagram(evs, segs[N].bytes);
    std::swap(evs[i], evs[i + 1]);  // N+1 arrives before N

    Harness hs;
    fill_ring(hs, segs);
    for (std::size_t k = 0; k <= i; ++k) hs.deliver(evs[k]);
    REQUIRE(hs.sink.reqs.size() == 1);  // N+1 held, the hole requested
    CHECK(hs.sink.reqs[0] == Req{0, segs[N].first_seq, segs[N + 1].first_seq});
    hs.deliver(evs[i + 1]);  // late N fills the hole before the request is served
    for (std::size_t k = i + 2; k < evs.size(); ++k) hs.deliver(evs[k]);
    hs.serve();  // the retransmit of N is now a whole duplicate

    const auto ref = reference(segs);
    require_matches(hs.h, ref);
    CHECK(hs.h.stats().messages_applied == total_messages(ref));
    CHECK(hs.h.stats().gaps == 1);
    CHECK(hs.h.stats().retransmit_segments == 1);
    CHECK(hs.h.stats().duplicate_messages == segs[N].count);
}

TEST_CASE("recovery: overlapping retransmit applies each message once") {
    const auto segs = feed_fixtures::synthetic_feed();
    const auto ref = reference(segs);
    constexpr std::size_t N = 300;
    std::vector<bool> drop(segs.size(), false);
    drop[N] = true;

    SECTION("retransmit straddles already-applied messages") {
        // The ring answers [N, N+1) with one packet spanning N-1 and N.
        Harness hs;
        fill_ring(hs, segs, N);
        hs.ring.store(0, segs[N - 1].first_seq, static_cast<std::uint16_t>(segs[N - 1].count + segs[N].count),
                      merged(segs[N - 1], segs[N]));
        hs.run(publisher_events(segs, drop));
        require_matches(hs.h, ref);
        CHECK(hs.h.stats().retransmit_segments == 1);
        CHECK(hs.h.stats().duplicate_messages == segs[N - 1].count);
    }
    SECTION("retransmit makes a held packet redundant") {
        // The ring answers [N, N+1) with one packet spanning N and N+1, while
        // N+1 itself is already held.
        Harness hs;
        fill_ring(hs, segs, N);
        hs.ring.store(0, segs[N].first_seq, static_cast<std::uint16_t>(segs[N].count + segs[N + 1].count),
                      merged(segs[N], segs[N + 1]));
        hs.run(publisher_events(segs, drop));
        require_matches(hs.h, ref);
        CHECK(hs.h.stats().retransmit_segments == 1);
        CHECK(hs.h.stats().duplicate_messages == segs[N + 1].count);
    }
}

TEST_CASE("recovery: restart packet dropped") {
    const auto segs = feed_fixtures::synthetic_feed();
    REQUIRE(segs[kRestart + 1].first_seq > 1);
    std::vector<bool> drop(segs.size(), false);
    drop[kRestart] = true;
    auto evs = publisher_events(segs, drop);
    const std::size_t d = find_datagram(evs, segs[kRestart + 1].bytes);
    REQUIRE(evs[d - 1].kind == Ev::Kind::EpochStart);
    std::swap(evs[d - 1], evs[d]);  // the next epoch-1 datagram beats its EpochStart

    const auto hs = run_events(segs, evs);
    require_matches(hs->h, reference(segs));
    CHECK(hs->h.stats().parked == 1);
    REQUIRE(hs->sink.reqs.size() == 1);
    CHECK(hs->sink.reqs[0] == Req{1, 1, segs[kRestart + 1].first_seq});
    CHECK(hs->h.stats().epochs == 2);
}

TEST_CASE("recovery: tail loss before restart and at end") {
    const auto segs = feed_fixtures::synthetic_feed();
    std::vector<bool> drop(segs.size(), false);
    const std::size_t last0 = kRestart - 1, last1 = segs.size() - 1;
    drop[last0] = true;
    drop[last1] = true;
    const auto hs = run_with_loss(segs, drop);
    require_matches(hs->h, reference(segs));
    REQUIRE(hs->sink.reqs.size() == 2);
    CHECK(hs->sink.reqs[0] == Req{0, segs[last0].first_seq, segs[last0].first_seq + segs[last0].count});
    CHECK(hs->sink.reqs[1] == Req{1, segs[last1].first_seq, segs[last1].first_seq + segs[last1].count});
    CHECK(hs->h.stats().gaps == 2);
}

TEST_CASE("recovery: merge-seam duplicate is not an epoch") {
    // An old-send-time copy of segment 10, sent again late: once inside
    // epoch 0 and once after the restart. Neither copy is in the reference
    // fixture (the direct replay would read the post-restart one as a gap).
    const auto segs = feed_fixtures::synthetic_feed();
    auto evs = publisher_events(segs, std::vector<bool>(segs.size(), false));
    const std::size_t late0 = find_datagram(evs, segs[500].bytes);
    evs.insert(evs.begin() + static_cast<std::ptrdiff_t>(late0 + 1), datagram(segs[10].bytes));
    const std::size_t late1 = find_datagram(evs, segs[kRestart + 5].bytes);
    evs.insert(evs.begin() + static_cast<std::ptrdiff_t>(late1 + 1), datagram(segs[10].bytes));

    const auto hs = run_events(segs, evs);
    require_matches(hs->h, reference(segs));
    const RecoveryStats& st = hs->h.stats();
    CHECK(st.duplicate_messages == 2u * segs[10].count);
    CHECK(st.parked == 0);
    CHECK(st.gaps == 0);
    CHECK(st.epochs == 2);
}

TEST_CASE("recovery: unserved request is fatal") {
    const auto segs = feed_fixtures::synthetic_feed();
    constexpr std::size_t N = 400;
    std::vector<bool> drop(segs.size(), false);
    drop[N] = true;
    Harness hs;
    fill_ring(hs, segs, N);  // the ring no longer holds N
    hs.run(publisher_events(segs, drop));
    CHECK(hs.h.failed());
    CHECK_FALSE(hs.h.done());
    CHECK(hs.h.stats().unrecoverable == 1);

    // Nothing is applied after the failure, from either channel.
    const std::uint64_t applied = hs.h.stats().messages_applied;
    hs.h.on_retransmit(0, segs[N].bytes);
    hs.h.on_datagram(segs[N].bytes);
    CHECK(hs.h.stats().messages_applied == applied);
    CHECK(hs.h.failed());
    CHECK_FALSE(hs.h.done());
}

TEST_CASE("recovery: seeded 5% random loss over 2,000 synthetic segments matches reference") {
    const auto segs = feed_fixtures::synthetic_feed();
    const auto ref = reference(segs);
    for (const char* spec : {"random:0.05", "burst:0.01:25"}) {
        INFO(spec);
        const auto ds = parse_drop_spec(spec, 7);
        REQUIRE(ds.has_value());
        DropInjector inj(*ds);
        std::vector<bool> drop(segs.size());
        for (std::size_t i = 0; i < segs.size(); ++i) drop[i] = inj.drop();
        REQUIRE(inj.dropped() > 0);
        const auto hs = run_with_loss(segs, drop);
        require_matches(hs->h, ref);
        CHECK(hs->h.stats().gaps > 0);
        CHECK(hs->h.stats().unrecoverable == 0);
        CHECK(hs->h.stats().epochs == 2);
        CHECK(hs->h.stats().datagrams == segs.size() - inj.dropped());
    }
}

TEST_CASE("recovery: rejected datagram is a gap") {
    const auto segs = feed_fixtures::synthetic_feed();
    constexpr std::size_t N = 600;
    auto evs = publisher_events(segs, std::vector<bool>(segs.size(), false));
    const std::size_t i = find_datagram(evs, segs[N].bytes);
    evs[i].bytes.resize(evs[i].bytes.size() - 3);  // framing overruns: payload_len > bytes present

    const auto hs = run_events(segs, evs);
    require_matches(hs->h, reference(segs));
    CHECK(hs->h.stats().rejected == 1);
    CHECK(hs->h.stats().gaps == 1);
    REQUIRE(hs->sink.reqs.size() == 1);
    CHECK(hs->sink.reqs[0] == Req{0, segs[N].first_seq, segs[N + 1].first_seq});
}

TEST_CASE("recovery: rejected retransmit is fatal") {
    const auto segs = feed_fixtures::synthetic_feed();
    constexpr std::size_t N = 700;
    std::vector<bool> drop(segs.size(), false);
    drop[N] = true;
    Harness hs;
    fill_ring(hs, segs, N);
    Bytes bad = segs[N].bytes;
    bad.resize(bad.size() - 3);  // framing overruns
    hs.ring.store(0, segs[N].first_seq, segs[N].count, bad);
    hs.run(publisher_events(segs, drop));
    CHECK(hs.h.failed());
    CHECK_FALSE(hs.h.done());
    CHECK(hs.h.stats().unrecoverable == 1);
    CHECK(hs.h.stats().rejected == 1);

    SECTION("an unannounced epoch is fatal too") {
        Harness h2;
        h2.h.on_datagram(segs[0].bytes);
        h2.h.on_retransmit(5, segs[1].bytes);
        CHECK(h2.h.failed());
        CHECK(h2.h.stats().unrecoverable == 1);
    }
}

TEST_CASE("recovery: overshooting the announced end is fatal") {
    const auto segs = feed_fixtures::synthetic_feed();
    auto evs = publisher_events(segs, std::vector<bool>(segs.size(), false));
    REQUIRE(evs.back().kind == Ev::Kind::End);
    evs.back().a -= 1;  // End announces one message fewer than was sent
    const auto hs = run_events(segs, evs);
    CHECK(hs->h.failed());
    CHECK_FALSE(hs->h.done());
    CHECK(hs->h.stats().unrecoverable == 1);
    CHECK(hs->h.digests().size() == 1);  // epoch 0 finalized, epoch 1 never
}
