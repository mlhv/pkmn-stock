// Decodes a whole IEX HIST DEEP+ capture: IEX-TP framing, sequence tracking
// and the DEEP+ message decoder, with no book. Prints the protocol ids seen,
// the sequence tracker's gaps/duplicates, and DecodeStats twice: "raw" over
// every message in every DEEP+ segment, and "applied" over the messages the
// tracker accepts (duplicate messages skipped), which is what a book would
// consume.
//
//   iex_decode data/iex/20241223_IEXTP1_DPLS1.0.pcap.gz [--limit N]
//
// --limit N stops after N UDP packets. Packets whose protocol id is not
// 0x8005, and segments whose framing overruns (for_each_message false), are
// counted, not fatal. The tracker follows the first segment's
// (channel, session); segments from any other stream are counted and left
// out of both stat blocks.
//
// A seq-1 segment with a send_time newer than any seen is a stream restart
// (is_stream_restart): the tracker is reset to 1 and the segment applied, so
// "applied" includes the restarted stream. Each reset is printed.

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <exception>
#include <optional>
#include <span>
#include <string>

#include "pkmn_md/deep_plus.hpp"
#include "pkmn_md/events.hpp"
#include "pkmn_md/feed_reader.hpp"
#include "pkmn_md/iextp.hpp"
#include "pkmn_md/sequence.hpp"

using namespace pkmn::md;

namespace {

using u64 = unsigned long long;

const char* type_name(int t) {
    switch (t) {
        case 'S': return "system event";
        case 'D': return "security directory";
        case 'H': return "trading status";
        case 'I': return "retail liquidity";
        case 'O': return "operational halt";
        case 'P': return "short sale test";
        case 'E': return "security event";
        case 'a': return "add order";
        case 'M': return "order modify";
        case 'R': return "order delete";
        case 'L': return "order executed";
        case 'T': return "trade";
        case 'B': return "trade break";
        case 'C': return "clear book";
        default: return "UNKNOWN";
    }
}

void print_stats(const char* label, const DecodeStats& st) {
    std::printf("%s\n", label);
    std::printf("  messages        %llu\n", static_cast<u64>(st.messages));
    std::printf("  unknown_type    %llu\n", static_cast<u64>(st.unknown_type));
    std::printf("  short_message   %llu\n", static_cast<u64>(st.short_message));
    std::printf("  bad_side        %llu\n", static_cast<u64>(st.bad_side));
    for (int t = 0; t < 256; ++t) {
        if (st.by_type[t] == 0) continue;
        if (t >= 0x20 && t < 0x7f)
            std::printf("  '%c' 0x%02x %-18s %llu\n", t, t, type_name(t), static_cast<u64>(st.by_type[t]));
        else
            std::printf("  ... 0x%02x %-18s %llu\n", t, type_name(t), static_cast<u64>(st.by_type[t]));
    }
}

}  // namespace

int main(int argc, char** argv) {
    std::string path;
    std::uint64_t limit = 0;
    for (int i = 1; i < argc; ++i) {
        if (std::strcmp(argv[i], "--limit") == 0 && i + 1 < argc) {
            limit = std::strtoull(argv[++i], nullptr, 10);
        } else if (path.empty() && argv[i][0] != '-') {
            path = argv[i];
        } else {
            path.clear();
            break;
        }
    }
    if (path.empty()) {
        std::fprintf(stderr, "usage: %s <capture.pcapng[.gz]> [--limit N]\n", argv[0]);
        return 2;
    }

    const auto t0 = std::chrono::steady_clock::now();
    std::uint64_t book_events[5] = {};
    DecodeStats raw{}, applied{};
    struct Reset {
        std::uint64_t packet;
        std::int64_t send_time, first_seq, expected_before;
    };
    constexpr std::size_t kMaxResetsShown = 16;
    Reset resets[kMaxResetsShown] = {};
    std::size_t n_resets = 0;
    // Where duplicates begin and end, so a replayed tail can be located.
    std::uint64_t dup_first_packet = 0, dup_last_packet = 0, dup_segments = 0;
    std::int64_t dup_first_ts = 0, dup_first_seq = 0, dup_expected = 0, dup_last_ts = 0;
    BookEvent ev{};
    TpHeader hdr{};
    std::optional<FeedReader> feed;  // outside the try so an error can report how far it got
    try {
        FeedReader& fr = feed.emplace(path, kDeepPlus);
        fr.set_packet_limit(limit);
        Segment seg{};
        while (fr.next(seg)) {
            // Pass 1: raw stats over every message of the segment.
            for_each_message(seg.payload, hdr, [&](std::span<const std::uint8_t> m) { decode_deep_plus(m, ev, raw); });
            if (seg.restart && n_resets < kMaxResetsShown)
                resets[n_resets++] = Reset{seg.packet_no, seg.hdr.send_time, seg.hdr.first_seq, seg.expected_before};
            if (seg.verdict == SeqVerdict::Duplicate && seg.hdr.msg_count > 0) {
                if (dup_segments++ == 0) {
                    dup_first_packet = seg.packet_no;
                    dup_first_ts = seg.capture_ts_ns;
                    dup_first_seq = seg.hdr.first_seq;
                    dup_expected = seg.expected_before;
                }
                dup_last_packet = seg.packet_no;
                dup_last_ts = seg.capture_ts_ns;
            }
            // Pass 2: only the messages the tracker accepts.
            for_each_applied(seg, [&](std::span<const std::uint8_t> m) {
                if (decode_deep_plus(m, ev, applied)) ++book_events[static_cast<int>(ev.kind)];
            });
        }
        const std::uint64_t packets = fr.packets();
        const SequenceTracker& tracker = fr.tracker();
        const auto& by_protocol = fr.by_protocol();
        const double secs = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();

        std::printf("file              %s\n", path.c_str());
        std::printf("packets           %llu%s\n", static_cast<u64>(packets),
                    limit != 0 && packets == limit ? " (limit reached)" : "");
        std::printf("skipped_frames    %llu\n", static_cast<u64>(fr.skipped_frames()));
        for (const auto& [proto, n] : by_protocol)
            std::printf("protocol 0x%04x   %llu packets%s\n", proto, static_cast<u64>(n),
                        proto == kDeepPlus ? " (DEEP+)" : " (NOT DEEP+, not decoded)");
        std::uint64_t non_deep_plus = 0;
        for (const auto& [proto, n] : by_protocol)
            if (proto != kDeepPlus) non_deep_plus += n;
        std::printf("non_deep_plus     %llu\n", static_cast<u64>(non_deep_plus));
        std::printf("tp_parse_failures %llu (short header or framing overrun)\n", static_cast<u64>(fr.tp_parse_failures()));
        std::printf("other_stream      %llu (channel/session differs from the first segment)\n",
                    static_cast<u64>(fr.other_stream()));
        std::printf("stream            channel %u session %u\n", fr.channel(), fr.session());
        std::printf("segments          %llu (heartbeats %llu)\n", static_cast<u64>(fr.segments()),
                    static_cast<u64>(fr.heartbeats()));
        std::printf("first_seq         %lld\n", static_cast<long long>(fr.first_seq()));
        std::printf("last_seq_end      %lld (first_seq + msg_count of the last segment)\n",
                    static_cast<long long>(fr.last_seq_end()));
        std::printf("tracker expected  %lld\n", static_cast<long long>(tracker.expected()));
        std::printf("tracker gaps      %llu (%llu messages missing)\n", static_cast<u64>(tracker.gaps()),
                    static_cast<u64>(tracker.gap_messages()));
        std::printf("tracker dups      %llu messages\n", static_cast<u64>(tracker.duplicates()));
        std::printf("tracker resets    %llu\n", static_cast<u64>(tracker.resets()));
        for (std::size_t i = 0; i < tracker.resets() && i < kMaxResetsShown; ++i)
            std::printf("  reset           packet %llu send_time %lld first_seq %lld (was expecting %lld)\n",
                        static_cast<u64>(resets[i].packet), static_cast<long long>(resets[i].send_time),
                        static_cast<long long>(resets[i].first_seq), static_cast<long long>(resets[i].expected_before));
        std::printf("dup segments      %llu (with messages)\n", static_cast<u64>(dup_segments));
        if (dup_segments > 0) {
            std::printf("first dup         packet %llu ts %lld first_seq %lld while expecting %lld\n",
                        static_cast<u64>(dup_first_packet), static_cast<long long>(dup_first_ts),
                        static_cast<long long>(dup_first_seq), static_cast<long long>(dup_expected));
            std::printf("last dup          packet %llu ts %lld\n", static_cast<u64>(dup_last_packet),
                        static_cast<long long>(dup_last_ts));
        }
        print_stats("raw (every message in every DEEP+ segment):", raw);
        print_stats("applied (duplicate messages skipped):", applied);
        std::printf("book events (applied)\n");
        const char* kinds[5] = {"add", "modify", "delete", "execute", "clear"};
        for (int k = 0; k < 5; ++k) std::printf("  %-8s %llu\n", kinds[k], static_cast<u64>(book_events[k]));
        std::printf("wall_seconds      %.1f\n", secs);
    } catch (const std::exception& e) {
        std::fprintf(stderr, "error after %llu packets: %s\n", static_cast<u64>(feed ? feed->packets() : 0), e.what());
        return 1;
    }
    return 0;
}
