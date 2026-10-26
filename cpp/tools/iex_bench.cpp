// Latency/throughput harness for DEEP+ decode plus BookSet::apply.
//
//   iex_bench <dplus.pcap.gz> --mode ram|stream [--ram-bytes N]
//
// ram: inflates the capture and holds the UDP payloads (IEX-TP segments) of
//   the first N bytes of payload (default 4 GiB; a packet that would cross N
//   is not loaded, and "ram_truncated" says so) in memory, then replays them
//   through SegmentFilter + apply_deep_plus_segment, the same acceptance,
//   restart and apply rules iex_decode and iex_oracle use (a restart clears
//   every book). Two passes over the held payloads, each from empty books:
//     1. throughput: no per-message timer; steady_clock around the whole
//        replay gives "seconds" and "msgs_per_sec". This includes IEX-TP
//        framing and sequence tracking, not just decode + apply.
//     2. latency: ticks() around each message's decode + apply, recorded in
//        a Histogram (p50/p99/p999/max, in ns). Each sample includes one
//        ticks() pair's overhead ("timer_overhead_ns") and is quantized to
//        whole counter ticks ("timer_resolution_ns"). Two means sit beside
//        the quantized percentiles:
//          "timed_region_mean_ns": sum of the per-message tick deltas /
//            messages / ticks_per_ns. The mean of exactly what the histogram
//            records (decode + apply plus one ticks() pair), not quantized:
//            whole-tick rounding of each sample averages out.
//          "latency_pass_wall_ns_per_msg": the latency pass's steady_clock
//            wall time per message. NOT the timed region: it also contains
//            SegmentFilter, two Histogram::record() calls and an llround
//            per message. It only shows what timing every message costs.
// stream: FeedReader over the gzipped file end to end, including inflate and
//   pcapng parsing; messages per second only (percentile keys are null).
//
// Output: one JSON object on stdout. Progress goes to stderr.

#include <sys/utsname.h>
#include <unistd.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <exception>
#include <span>
#include <string>
#include <vector>

#include "pkmn_md/book_set.hpp"
#include "pkmn_md/deep_plus.hpp"
#include "pkmn_md/events.hpp"
#include "pkmn_md/feed_reader.hpp"
#include "pkmn_md/iextp.hpp"
#include "pkmn_md/pcapng.hpp"
#include "pkmn_md/replay.hpp"
#include "pkmn_md/timing.hpp"

#ifndef PKMN_BENCH_FLAGS
#define PKMN_BENCH_FLAGS "unknown"
#endif
// Generated on every build (cpp/cmake/bench_build_info.cmake):
// PKMN_BENCH_GIT_SHA and PKMN_BENCH_TREE_HASH.
#include "pkmn_bench_build_info.hpp"

using namespace pkmn::md;
using clk = std::chrono::steady_clock;

#if defined(__clang__)
constexpr const char* kCompiler = "clang " __VERSION__;
#elif defined(__GNUC__)
constexpr const char* kCompiler = "g++ " __VERSION__;
#else
constexpr const char* kCompiler = "unknown";
#endif

// A named namespace, not an anonymous one: FlameGraph's stackcollapse-perf.pl
// strips a frame from its first '(', so "(anonymous namespace)::replay<...>"
// collapses to nothing and the pass cannot be selected from the profile.
namespace bench {

using u64 = unsigned long long;

double secs_since(clk::time_point t0) { return std::chrono::duration<double>(clk::now() - t0).count(); }

std::string json_str(const std::string& s) {
    std::string o = "\"";
    for (char c : s) {
        if (c == '"' || c == '\\') {
            o += '\\';
            o += c;
        } else if (static_cast<unsigned char>(c) < 0x20) {
            o += ' ';
        } else {
            o += c;
        }
    }
    return o + "\"";
}

std::string host_string() {
    char name[256] = {};
    gethostname(name, sizeof name - 1);
    utsname u{};
    uname(&u);
    return std::string(name) + " " + u.sysname + " " + u.release + " " + u.machine;
}

struct TimerInfo {
    std::uint64_t freq_hz;
    double ticks_per_ns, resolution_ns;
    double overhead_ns;              // mean of an empty ticks() pair, over many pairs
    std::uint64_t overhead_ticks_p50, overhead_ticks_max;
    double steady_resolution_ns;     // smallest nonzero step seen between steady_clock reads
};

TimerInfo measure_timer() {
    TimerInfo t{};
    t.freq_hz = timer_frequency_hz();
    t.ticks_per_ns = ticks_per_ns();
    // One tick, from the advertised rate (the calibrated rate agrees to <1%;
    // see test_timing.cpp).
    t.resolution_ns = 1e9 / static_cast<double>(t.freq_hz);
    constexpr int kPairs = 10'000'000;
    Histogram h;
    std::uint64_t sum = 0;
    for (int i = 0; i < kPairs; ++i) {
        const std::uint64_t a = ticks();
        const std::uint64_t b = ticks();
        sum += b - a;
        h.record(b - a);
    }
    t.overhead_ns = static_cast<double>(sum) / kPairs / t.ticks_per_ns;
    t.overhead_ticks_p50 = h.percentile(50);
    t.overhead_ticks_max = h.max();
    std::int64_t best = INT64_MAX;
    for (int i = 0; i < 1'000'000; ++i) {
        const auto a = clk::now();
        auto b = clk::now();
        while (b == a) b = clk::now();
        best = std::min<std::int64_t>(best, std::chrono::duration_cast<std::chrono::nanoseconds>(b - a).count());
    }
    t.steady_resolution_ns = static_cast<double>(best);
    return t;
}

// The UDP payloads of a capture, held contiguously in file order.
struct HeldPayloads {
    std::vector<std::uint8_t> bytes;
    std::vector<std::uint64_t> ends;  // payload i is [ends[i-1], ends[i])
    std::int64_t first_ts = 0;
    bool truncated = false;
    std::span<const std::uint8_t> at(std::size_t i) const {
        const std::uint64_t b = i == 0 ? 0 : ends[i - 1];
        return {bytes.data() + b, static_cast<std::size_t>(ends[i] - b)};
    }
};

void load(const std::string& path, std::uint64_t ram_bytes, HeldPayloads& out) {
    PcapngReader r(path);
    out.bytes.reserve(static_cast<std::size_t>(ram_bytes));
    Packet p{};
    while (r.next(p)) {
        if (out.bytes.size() + p.udp_payload.size() > ram_bytes) {
            out.truncated = true;
            break;
        }
        out.bytes.insert(out.bytes.end(), p.udp_payload.begin(), p.udp_payload.end());
        out.ends.push_back(out.bytes.size());
        if ((out.ends.size() & ((1u << 22) - 1)) == 0)
            std::fprintf(stderr, "  loaded %zu packets, %.2f GB\n", out.ends.size(), out.bytes.size() / 1e9);
    }
}

struct PassResult {
    std::uint64_t messages = 0, segments = 0, restarts = 0, orders_at_end = 0, unknown_id = 0;
    double seconds = 0;
};

// noinline so a profile can attribute samples to one pass (bench_iex.sh
// --profile keeps only stacks under replay<CallStep>, the throughput pass).
template <class PerMessage>
[[gnu::noinline]] PassResult replay(const HeldPayloads& held, PerMessage&& per_message) {
    PassResult r;
    SegmentFilter filt(kDeepPlus);
    BookSet books;
    BookEvent ev{};
    DecodeStats st{};
    Segment seg{};
    const std::size_t n = held.ends.size();
    const auto t0 = clk::now();
    for (std::size_t i = 0; i < n; ++i) {
        if (!filt.accept(held.at(i), 0, i + 1, seg)) continue;
        apply_deep_plus_segment(seg, books, ev, st, per_message);
    }
    r.seconds = secs_since(t0);
    r.messages = st.messages;
    r.segments = filt.segments();
    r.restarts = books.clear_alls();
    r.orders_at_end = books.order_count();
    r.unknown_id = books.total_anomalies().unknown_id;
    return r;
}

void print_common(const std::string& path, const char* mode, const TimerInfo& t) {
    std::printf("{\n");
    std::printf("  \"host\": %s,\n", json_str(host_string()).c_str());
    std::printf("  \"compiler\": %s,\n", json_str(kCompiler).c_str());
    std::printf("  \"flags\": %s,\n", json_str(PKMN_BENCH_FLAGS).c_str());
    std::printf("  \"git_sha\": %s,\n", json_str(PKMN_BENCH_GIT_SHA).c_str());
    // SHA-256 of the cpp/ + scripts/ working-tree changes vs HEAD (diff plus
    // untracked files); null when the tree is clean.
    if (std::strlen(PKMN_BENCH_TREE_HASH) == 0)
        std::printf("  \"tree_hash\": null,\n");
    else
        std::printf("  \"tree_hash\": %s,\n", json_str(PKMN_BENCH_TREE_HASH).c_str());
    std::printf("  \"file\": %s,\n", json_str(path).c_str());
    std::printf("  \"mode\": \"%s\",\n", mode);
    std::printf("  \"timer\": \"%s\",\n",
#if defined(__aarch64__)
                "cntvct_el0 (isb before read)"
#else
                "steady_clock"
#endif
    );
    std::printf("  \"cntfrq_hz\": %llu,\n", static_cast<u64>(t.freq_hz));
    std::printf("  \"ticks_per_ns\": %.9f,\n", t.ticks_per_ns);
    std::printf("  \"timer_resolution_ns\": %.3f,\n", t.resolution_ns);
    std::printf("  \"timer_overhead_ns\": %.3f,\n", t.overhead_ns);
    std::printf("  \"timer_overhead_ticks_p50\": %llu,\n", static_cast<u64>(t.overhead_ticks_p50));
    std::printf("  \"timer_overhead_ticks_max\": %llu,\n", static_cast<u64>(t.overhead_ticks_max));
    std::printf("  \"steady_clock_resolution_ns\": %.0f,\n", t.steady_resolution_ns);
}

int usage(const char* argv0) {
    std::fprintf(stderr, "usage: %s <dplus.pcap.gz> --mode ram|stream [--ram-bytes N]\n", argv0);
    return 2;
}

}  // namespace bench

using namespace bench;

int main(int argc, char** argv) {
    std::string path, mode;
    std::uint64_t ram_bytes = std::uint64_t{4} << 30;
    for (int i = 1; i < argc; ++i) {
        if (std::strcmp(argv[i], "--mode") == 0 && i + 1 < argc) {
            mode = argv[++i];
        } else if (std::strcmp(argv[i], "--ram-bytes") == 0 && i + 1 < argc) {
            char* end = nullptr;
            ram_bytes = std::strtoull(argv[++i], &end, 10);
            if (end == nullptr || *end != '\0' || ram_bytes == 0) return usage(argv[0]);
        } else if (path.empty() && argv[i][0] != '-') {
            path = argv[i];
        } else {
            return usage(argv[0]);
        }
    }
    if (path.empty() || (mode != "ram" && mode != "stream")) return usage(argv[0]);

    try {
        std::fprintf(stderr, "measuring timer...\n");
        const TimerInfo t = measure_timer();

        if (mode == "stream") {
            const auto t0 = clk::now();
            FeedReader fr(path, kDeepPlus);
            BookSet books;
            BookEvent ev{};
            DecodeStats st{};
            Segment seg{};
            while (fr.next(seg)) apply_deep_plus_segment(seg, books, ev, st);
            const double secs = secs_since(t0);
            print_common(path, "stream", t);
            std::printf("  \"packets\": %llu,\n", static_cast<u64>(fr.packets()));
            std::printf("  \"segments\": %llu,\n", static_cast<u64>(fr.segments()));
            std::printf("  \"restarts\": %llu,\n", static_cast<u64>(books.clear_alls()));
            std::printf("  \"orders_at_end\": %zu,\n", books.order_count());
            std::printf("  \"messages\": %llu,\n", static_cast<u64>(st.messages));
            std::printf("  \"seconds\": %.3f,\n", secs);
            std::printf("  \"msgs_per_sec\": %.0f,\n", static_cast<double>(st.messages) / secs);
            std::printf("  \"p50_ns\": null,\n  \"p99_ns\": null,\n  \"p999_ns\": null,\n  \"max_ns\": null\n}\n");
            return 0;
        }

        std::fprintf(stderr, "loading payloads (cap %llu bytes)...\n", static_cast<u64>(ram_bytes));
        const auto tl = clk::now();
        HeldPayloads held;
        load(path, ram_bytes, held);
        const double load_secs = secs_since(tl);

        std::fprintf(stderr, "throughput pass...\n");
        const PassResult thr = replay(held, CallStep{});

        std::fprintf(stderr, "latency pass...\n");
        Histogram ns_hist, tick_hist;
        const double tpn = t.ticks_per_ns;
        std::uint64_t tick_sum = 0;
        const PassResult lat = replay(held, [&](auto&& step) {
            const std::uint64_t a = ticks();
            step();
            const std::uint64_t d = ticks() - a;
            tick_sum += d;
            tick_hist.record(d);
            ns_hist.record(static_cast<std::uint64_t>(std::llround(static_cast<double>(d) / tpn)));
        });
        if (lat.messages != thr.messages || lat.orders_at_end != thr.orders_at_end) {
            std::fprintf(stderr, "error: the two passes disagree (%llu vs %llu messages)\n",
                         static_cast<u64>(thr.messages), static_cast<u64>(lat.messages));
            return 1;
        }

        print_common(path, "ram", t);
        std::printf("  \"ram_bytes\": %llu,\n", static_cast<u64>(ram_bytes));
        std::printf("  \"held_bytes\": %zu,\n", held.bytes.size());
        std::printf("  \"held_packets\": %zu,\n", held.ends.size());
        std::printf("  \"ram_truncated\": %s,\n", held.truncated ? "true" : "false");
        std::printf("  \"load_seconds\": %.3f,\n", load_secs);
        std::printf("  \"segments\": %llu,\n", static_cast<u64>(thr.segments));
        std::printf("  \"restarts\": %llu,\n", static_cast<u64>(thr.restarts));
        std::printf("  \"orders_at_end\": %llu,\n", static_cast<u64>(thr.orders_at_end));
        std::printf("  \"unknown_id\": %llu,\n", static_cast<u64>(thr.unknown_id));
        std::printf("  \"messages\": %llu,\n", static_cast<u64>(thr.messages));
        std::printf("  \"seconds\": %.3f,\n", thr.seconds);
        std::printf("  \"msgs_per_sec\": %.0f,\n", static_cast<double>(thr.messages) / thr.seconds);
        std::printf("  \"batch_ns_per_msg\": %.2f,\n", thr.seconds * 1e9 / static_cast<double>(thr.messages));
        std::printf("  \"timed_region_mean_ns\": %.2f,\n",
                    static_cast<double>(tick_sum) / static_cast<double>(lat.messages) / tpn);
        std::printf("  \"latency_pass_wall_seconds\": %.3f,\n", lat.seconds);
        std::printf("  \"latency_pass_wall_ns_per_msg\": %.2f,\n",
                    lat.seconds * 1e9 / static_cast<double>(lat.messages));
        std::printf("  \"p50_ticks\": %llu,\n", static_cast<u64>(tick_hist.percentile(50)));
        std::printf("  \"p99_ticks\": %llu,\n", static_cast<u64>(tick_hist.percentile(99)));
        std::printf("  \"p999_ticks\": %llu,\n", static_cast<u64>(tick_hist.percentile(99.9)));
        std::printf("  \"max_ticks\": %llu,\n", static_cast<u64>(tick_hist.max()));
        std::printf("  \"p50_ns\": %llu,\n", static_cast<u64>(ns_hist.percentile(50)));
        std::printf("  \"p99_ns\": %llu,\n", static_cast<u64>(ns_hist.percentile(99)));
        std::printf("  \"p999_ns\": %llu,\n", static_cast<u64>(ns_hist.percentile(99.9)));
        std::printf("  \"max_ns\": %llu\n", static_cast<u64>(ns_hist.max()));
        std::printf("}\n");
    } catch (const std::exception& e) {
        std::fprintf(stderr, "error: %s\n", e.what());
        return 1;
    }
    return 0;
}
