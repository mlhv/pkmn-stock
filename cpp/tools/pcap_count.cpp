// Smoke test over a real capture: streams the whole file through
// PcapngReader and prints packet count, UDP payload bytes, skipped frames,
// skipped Simple Packet Blocks, decompressed size and the first/last
// timestamps.
//
//   pcap_count data/iex/20241223_IEXTP1_DPLS1.0.pcap.gz

#include <chrono>
#include <cstdint>
#include <cstdio>
#include <ctime>
#include <exception>
#include <string>

#include "pkmn_md/pcapng.hpp"

namespace {

std::string iso(std::int64_t ts_ns) {
    const std::time_t s = static_cast<std::time_t>(ts_ns / 1'000'000'000);
    std::tm tm{};
    gmtime_r(&s, &tm);
    char buf[64];
    std::snprintf(buf, sizeof buf, "%04d-%02d-%02dT%02d:%02d:%02d.%09lldZ", tm.tm_year + 1900, tm.tm_mon + 1,
                  tm.tm_mday, tm.tm_hour, tm.tm_min, tm.tm_sec,
                  static_cast<long long>(ts_ns % 1'000'000'000));
    return buf;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc != 2) {
        std::fprintf(stderr, "usage: %s <capture.pcapng[.gz]>\n", argv[0]);
        return 2;
    }
    const auto t0 = std::chrono::steady_clock::now();
    std::uint64_t packets = 0, payload_bytes = 0, out_of_order = 0;
    std::int64_t first = 0, last = 0, prev = 0;
    try {
        pkmn::md::PcapngReader r(argv[1]);
        pkmn::md::Packet p{};
        while (r.next(p)) {
            if (packets == 0) first = p.ts_ns;
            if (packets > 0 && p.ts_ns < prev) ++out_of_order;
            prev = last = p.ts_ns;
            ++packets;
            payload_bytes += p.udp_payload.size();
        }
        const double secs = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
        std::printf("packets          %llu\n", static_cast<unsigned long long>(packets));
        std::printf("udp_payload_bytes %llu\n", static_cast<unsigned long long>(payload_bytes));
        std::printf("skipped_frames   %llu\n", static_cast<unsigned long long>(r.skipped_frames()));
        std::printf("skipped_spbs     %llu (Simple Packet Blocks: no timestamp)\n",
                    static_cast<unsigned long long>(r.skipped_simple_packets()));
        std::printf("decompressed     %llu\n", static_cast<unsigned long long>(r.bytes_consumed()));
        std::printf("ts_backwards     %llu\n", static_cast<unsigned long long>(out_of_order));
        std::printf("first_ts         %lld (%s)\n", static_cast<long long>(first), iso(first).c_str());
        std::printf("last_ts          %lld (%s)\n", static_cast<long long>(last), iso(last).c_str());
        std::printf("wall_seconds     %.1f\n", secs);
    } catch (const std::exception& e) {
        std::fprintf(stderr, "error after %llu packets: %s\n", static_cast<unsigned long long>(packets), e.what());
        return 1;
    }
    return 0;
}
