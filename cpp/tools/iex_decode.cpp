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
#include <map>
#include <span>
#include <string>

#include "pkmn_md/deep_plus.hpp"
#include "pkmn_md/events.hpp"
#include "pkmn_md/iextp.hpp"
#include "pkmn_md/pcapng.hpp"
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
    std::uint64_t packets = 0, deep_plus_segments = 0, heartbeats = 0, overruns = 0, other_stream = 0;
    std::uint64_t book_events[5] = {};
    std::map<std::uint16_t, std::uint64_t> by_protocol;  // a handful of keys; not per message
    DecodeStats raw{}, applied{};
    SequenceTracker tracker;
    bool have_stream = false;
    std::uint32_t channel = 0, session = 0;
    std::int64_t first_seq = 0, last_seq_end = 0, max_send_time = 0;
    struct Reset {
        std::uint64_t packet;
        std::int64_t send_time, first_seq, expected_before;
    };
    constexpr std::size_t kMaxResetsShown = 16;
    Reset resets[kMaxResetsShown] = {};
    // Where duplicates begin and end, so a replayed tail can be located.
    std::uint64_t dup_first_packet = 0, dup_last_packet = 0, dup_segments = 0;
    std::int64_t dup_first_ts = 0, dup_first_seq = 0, dup_expected = 0, dup_last_ts = 0;
    BookEvent ev{};
    TpHeader hdr{};
    try {
        PcapngReader r(path);
        Packet p{};
        while ((limit == 0 || packets < limit) && r.next(p)) {
            ++packets;
            if (!parse_tp_header(p.udp_payload, hdr)) {
                ++overruns;
                continue;
            }
            ++by_protocol[hdr.protocol_id];
            if (hdr.protocol_id != kDeepPlus) continue;
            if (!have_stream) {
                have_stream = true;
                channel = hdr.channel_id;
                session = hdr.session_id;
                first_seq = hdr.first_seq;
            }
            if (hdr.channel_id != channel || hdr.session_id != session) {
                ++other_stream;
                continue;
            }
            // Pass 1: framing + raw stats. A segment that overruns is counted
            // and never reaches the tracker (its msg_count is not trusted).
            const bool ok = for_each_message(p.udp_payload, hdr, [&](std::span<const std::uint8_t> m) {
                decode_deep_plus(m, ev, raw);
            });
            if (!ok) {
                ++overruns;
                continue;
            }
            ++deep_plus_segments;
            if (hdr.msg_count == 0) ++heartbeats;
            if (is_stream_restart(hdr, tracker.expected(), max_send_time)) {
                if (tracker.resets() < kMaxResetsShown)
                    resets[tracker.resets()] = Reset{packets, hdr.send_time, hdr.first_seq, tracker.expected()};
                tracker.reset(1);
            }
            if (hdr.send_time > max_send_time) max_send_time = hdr.send_time;
            const std::int64_t expected_before = tracker.expected();
            if (tracker.observe(hdr.first_seq, hdr.msg_count) == SeqVerdict::Duplicate && hdr.msg_count > 0) {
                if (dup_segments++ == 0) {
                    dup_first_packet = packets;
                    dup_first_ts = p.ts_ns;
                    dup_first_seq = hdr.first_seq;
                    dup_expected = expected_before;
                }
                dup_last_packet = packets;
                dup_last_ts = p.ts_ns;
            }
            last_seq_end = hdr.first_seq + hdr.msg_count;
            // Pass 2: only the messages the tracker accepts.
            const std::uint16_t skip = tracker.skip();
            if (skip >= hdr.msg_count) continue;
            std::uint16_t idx = 0;
            for_each_message(p.udp_payload, hdr, [&](std::span<const std::uint8_t> m) {
                if (idx++ < skip) return;
                if (decode_deep_plus(m, ev, applied)) ++book_events[static_cast<int>(ev.kind)];
            });
        }
        const double secs = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();

        std::printf("file              %s\n", path.c_str());
        std::printf("packets           %llu%s\n", static_cast<u64>(packets),
                    limit != 0 && packets == limit ? " (limit reached)" : "");
        std::printf("skipped_frames    %llu\n", static_cast<u64>(r.skipped_frames()));
        for (const auto& [proto, n] : by_protocol)
            std::printf("protocol 0x%04x   %llu packets%s\n", proto, static_cast<u64>(n),
                        proto == kDeepPlus ? " (DEEP+)" : " (NOT DEEP+, not decoded)");
        std::uint64_t non_deep_plus = 0;
        for (const auto& [proto, n] : by_protocol)
            if (proto != kDeepPlus) non_deep_plus += n;
        std::printf("non_deep_plus     %llu\n", static_cast<u64>(non_deep_plus));
        std::printf("tp_parse_failures %llu (short header or framing overrun)\n", static_cast<u64>(overruns));
        std::printf("other_stream      %llu (channel/session differs from the first segment)\n",
                    static_cast<u64>(other_stream));
        std::printf("stream            channel %u session %u\n", channel, session);
        std::printf("segments          %llu (heartbeats %llu)\n", static_cast<u64>(deep_plus_segments),
                    static_cast<u64>(heartbeats));
        std::printf("first_seq         %lld\n", static_cast<long long>(first_seq));
        std::printf("last_seq_end      %lld (first_seq + msg_count of the last segment)\n",
                    static_cast<long long>(last_seq_end));
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
        std::fprintf(stderr, "error after %llu packets: %s\n", static_cast<u64>(packets), e.what());
        return 1;
    }
    return 0;
}
