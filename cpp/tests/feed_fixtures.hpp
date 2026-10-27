#pragma once

// Test-only synthetic DEEP+ feed for the feed-handler tests: a seeded,
// deterministic run of IEX-TP segments carrying adds, modifies, deletes,
// executes and a few non-book trade messages over 20 symbols, with one
// stream restart (seq back to 1, books reset) and strictly increasing send
// times. Only live order ids are ever modified, deleted or executed, so a
// direct file replay of the segments is a meaningful reference. The gap
// recovery tests (test_gap_recovery.cpp) drive it in-process; the socket
// test writes the same segments to a .pcap.gz with write_feed_capture.

#include <cstdint>
#include <random>
#include <string>
#include <vector>

#include "md_fixtures.hpp"
#include "pkmn_md/iextp.hpp"

namespace feed_fixtures {

using md_fixtures::Bytes;

inline constexpr std::int64_t kFeedBaseTime = 1'734'964'524'000'000'000LL;

struct FeedSegment {
    Bytes bytes;  // the whole IEX-TP UDP payload
    std::uint32_t epoch;
    std::int64_t first_seq;
    std::uint16_t count;
    std::int64_t send_time;
};

// DEEP+ Order Modify 'M' (38 B): flags/1 ts/2 sym/10 id/18 size/26 price/30.
inline Bytes dp_modify(const char* sym, bool keep_priority, std::uint64_t id, std::uint32_t size, std::int64_t price,
                       std::int64_t ts) {
    Bytes m = {'M', static_cast<std::uint8_t>(keep_priority ? 1 : 0)};
    md_fixtures::put_le64(m, static_cast<std::uint64_t>(ts));
    md_fixtures::put_sym(m, sym);
    md_fixtures::put_le64(m, id);
    md_fixtures::put_le32(m, size);
    md_fixtures::put_le64(m, static_cast<std::uint64_t>(price));
    return m;
}

// DEEP+ Order Delete 'R' (26 B): ts/2 sym/10 id/18.
inline Bytes dp_delete(const char* sym, std::uint64_t id, std::int64_t ts) {
    Bytes m = {'R', 0};
    md_fixtures::put_le64(m, static_cast<std::uint64_t>(ts));
    md_fixtures::put_sym(m, sym);
    md_fixtures::put_le64(m, id);
    return m;
}

// DEEP+ Order Executed 'L' (46 B): ts/2 sym/10 id/18 size/26 price/30 trade id/38.
inline Bytes dp_execute(const char* sym, std::uint64_t id, std::uint32_t size, std::int64_t price,
                        std::uint64_t trade_id, std::int64_t ts) {
    Bytes m = {'L', 0};
    md_fixtures::put_le64(m, static_cast<std::uint64_t>(ts));
    md_fixtures::put_sym(m, sym);
    md_fixtures::put_le64(m, id);
    md_fixtures::put_le32(m, size);
    md_fixtures::put_le64(m, static_cast<std::uint64_t>(price));
    md_fixtures::put_le64(m, trade_id);
    return m;
}

// DEEP+ Trade 'T' (38 B), not book-affecting: only counted by the decoder,
// but still an applied message for the per-epoch message counts.
inline Bytes dp_trade(const char* sym, std::int64_t ts) {
    Bytes m = {'T', 0};
    md_fixtures::put_le64(m, static_cast<std::uint64_t>(ts));
    md_fixtures::put_sym(m, sym);
    m.resize(38, 0);
    return m;
}

// `n` segments, 1-4 messages each, with segment `restart_at` beginning a new
// epoch at seq 1 (0 = no restart). Send times rise by 1 us per segment.
inline std::vector<FeedSegment> synthetic_feed(std::size_t n = 2000, std::size_t restart_at = 1500,
                                               std::uint64_t seed = 1) {
    static const char* const kSyms[20] = {"AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH", "III", "JJJ",
                                          "KKK", "LLL", "MMM", "NNN", "OOO", "PPP", "QQQ", "RRR", "SSS", "TTT"};
    struct Live {
        std::uint64_t id;
        int sym;
        std::uint32_t qty;
        std::int64_t price;
    };
    std::mt19937_64 rng(seed);
    auto pick = [&](std::uint64_t k) { return static_cast<std::size_t>(rng() % k); };
    std::vector<Live> live;
    std::vector<FeedSegment> out;
    out.reserve(n);
    std::uint32_t epoch = 0;
    std::int64_t seq = 1;
    std::uint64_t next_id = 1, trade_id = 1;
    for (std::size_t i = 0; i < n; ++i) {
        if (restart_at != 0 && i == restart_at) {
            ++epoch;
            seq = 1;
            live.clear();  // the restart clears every book; old ids are gone
        }
        const std::int64_t t = kFeedBaseTime + static_cast<std::int64_t>(i) * 1000;
        std::vector<Bytes> msgs;
        const std::size_t k = 1 + pick(4);
        for (std::size_t j = 0; j < k; ++j) {
            const std::size_t op = pick(100);
            if (live.size() < 5 || op < 45) {
                const int s = static_cast<int>(pick(20));
                const char side = pick(2) == 0 ? '8' : '5';
                const auto qty = static_cast<std::uint32_t>(100 * (1 + pick(10)));
                const std::int64_t price = 100'000 + static_cast<std::int64_t>(pick(50)) * 100;
                msgs.push_back(md_fixtures::dp_add(kSyms[s], side, next_id, qty, price, t));
                live.push_back({next_id++, s, qty, price});
                continue;
            }
            const std::size_t li = pick(live.size());
            Live& o = live[li];
            if (op < 60) {
                o.qty = static_cast<std::uint32_t>(100 * (1 + pick(10)));
                o.price = 100'000 + static_cast<std::int64_t>(pick(50)) * 100;
                msgs.push_back(dp_modify(kSyms[o.sym], pick(2) == 0, o.id, o.qty, o.price, t));
            } else if (op < 75) {
                msgs.push_back(dp_delete(kSyms[o.sym], o.id, t));
                live[li] = live.back();
                live.pop_back();
            } else if (op < 92) {
                const std::uint32_t q =
                    o.qty <= 100 ? o.qty : static_cast<std::uint32_t>(100 * (1 + pick(o.qty / 100)));
                msgs.push_back(dp_execute(kSyms[o.sym], o.id, q, o.price, trade_id++, t));
                o.qty -= q;
                if (o.qty == 0) {
                    live[li] = live.back();
                    live.pop_back();
                }
            } else {
                msgs.push_back(dp_trade(kSyms[o.sym], t));
            }
        }
        const auto count = static_cast<std::uint16_t>(msgs.size());
        out.push_back({md_fixtures::tp_segment(pkmn::md::kDeepPlus, seq, msgs, -1, -1, t), epoch, seq, count, t});
        seq += count;
    }
    return out;
}

// Two segments, one epoch, whose books end empty: seq 1-2 add AAA id 1 at
// `aaa_price` and BBB id 2, seq 3-4 delete both. Two such feeds with
// different prices have equal end-of-epoch digests (empty books, 4 messages)
// but different books after seq 1 and 2 (seq 3 deletes AAA, leaving BBB).
inline std::vector<FeedSegment> empty_ending_feed(std::int64_t aaa_price) {
    const std::int64_t t0 = kFeedBaseTime, t1 = kFeedBaseTime + 1000;
    const Bytes s1 = md_fixtures::tp_segment(pkmn::md::kDeepPlus, 1,
                                             {md_fixtures::dp_add("AAA", '8', 1, 100, aaa_price, t0),
                                              md_fixtures::dp_add("BBB", '5', 2, 200, 150'000, t0)},
                                             -1, -1, t0);
    const Bytes s2 =
        md_fixtures::tp_segment(pkmn::md::kDeepPlus, 3, {dp_delete("AAA", 1, t1), dp_delete("BBB", 2, t1)}, -1, -1, t1);
    return {{s1, 0, 1, 2, t0}, {s2, 0, 3, 2, t1}};
}

// The segments as a gzipped pcapng capture, in order, one UDP packet each.
inline void write_feed_capture(const std::string& path, const std::vector<FeedSegment>& segs) {
    std::vector<std::string> payloads;
    std::vector<std::uint64_t> ts;
    payloads.reserve(segs.size());
    ts.reserve(segs.size());
    for (std::size_t i = 0; i < segs.size(); ++i) {
        payloads.emplace_back(segs[i].bytes.begin(), segs[i].bytes.end());
        ts.push_back(i + 1);
    }
    md_fixtures::write_gzip(path, md_fixtures::capture(payloads, ts));
}

}  // namespace feed_fixtures
