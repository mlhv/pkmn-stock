// Replays a DEEP+ capture as loopback UDP multicast, one datagram per IEX-TP
// payload, byte for byte unchanged, and answers retransmit requests over a
// TCP side channel (recovery_proto.hpp).
//
//   feed_publisher <dplus.pcap.gz> [--group 239.255.0.1] [--port 31001]
//       [--ctrl-port 31002] [--rate MSGS_PER_SEC|0=unthrottled]
//       [--drop none|random:p|burst:p:len] [--seed N]
//       [--ring-bytes N (default 268435456)] [--ram] [--packet-limit N]
//
// Flow:
//   1. Listen on TCP 127.0.0.1:ctrl-port and accept one handler.
//   2. With --ram, preload every UDP payload into memory (as iex_bench does);
//      otherwise stream the capture through zlib while sending.
//   3. Every payload goes through SegmentFilter(kDeepPlus); payloads it does
//      not accept are skipped and counted. For each accepted segment:
//        a. on seg.restart, write EpochStart(++epoch, seg.expected_before,
//           send_time) to TCP before the UDP send;
//        b. store it in the retransmit ring under `epoch`;
//        c. unless the drop injector drops it, sendto the unchanged payload;
//        d. pace to --rate messages/sec (steady_clock). Pacing counts every
//           accepted segment's messages, dropped or not, so injected loss does
//           not change the schedule.
//   4. After the last packet, write End(epoch, tracker expected).
//
// Threads: the main thread sends UDP and writes EpochStart/End; a server
// thread reads Request frames and answers each with one Retransmit frame per
// covering packet, or one Unserved frame. Every TCP write (either thread) is
// serialized by one mutex. The ring is guarded by its own mutex across store
// and collect, and collected payloads are copied into the outgoing frame
// buffer while it is held (the ring's spans die on the next store). The
// server thread ends when the handler closes TCP; the main thread then joins
// it and prints one JSON object on stdout.
//
// Exit: 0 when End was written and the handler then closed TCP; 1 if the
// handler went away before End (the stream was cut short) or on an error;
// 2 on bad usage.

#include <signal.h>
#include <sys/socket.h>

#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <exception>
#include <mutex>
#include <optional>
#include <span>
#include <string>
#include <thread>
#include <vector>

#include "net_util.hpp"
#include "pkmn_md/drop_injector.hpp"
#include "pkmn_md/feed_reader.hpp"
#include "pkmn_md/iextp.hpp"
#include "pkmn_md/pcapng.hpp"
#include "pkmn_md/recovery_proto.hpp"
#include "pkmn_md/retransmit_ring.hpp"

using namespace pkmn::md;
using clk = std::chrono::steady_clock;
using u64 = unsigned long long;

namespace {

struct Options {
    std::string path, group = "239.255.0.1", drop = "none";
    int port = 31001, ctrl_port = 31002;
    double rate = 0;
    std::uint64_t seed = 0, ring_bytes = 268435456, packet_limit = 0;
    bool ram = false;
};

int usage(const char* argv0) {
    std::fprintf(stderr,
                 "usage: %s <dplus.pcap.gz> [--group 239.255.0.1] [--port 31001] [--ctrl-port 31002]\n"
                 "       [--rate MSGS_PER_SEC|0] [--drop none|random:p|burst:p:len] [--seed N]\n"
                 "       [--ring-bytes N] [--ram] [--packet-limit N]\n",
                 argv0);
    return 2;
}

bool parse_u64(const char* s, std::uint64_t& v) {
    char* end = nullptr;
    errno = 0;
    v = std::strtoull(s, &end, 10);
    return *s != '\0' && *s != '-' && end != nullptr && *end == '\0' && errno == 0;
}

bool parse_port(const char* s, int& port) {
    std::uint64_t v = 0;
    if (!parse_u64(s, v) || v == 0 || v > 65535) return false;
    port = static_cast<int>(v);
    return true;
}

// The UDP payloads of a capture, held contiguously in file order.
struct HeldPayloads {
    std::vector<std::uint8_t> bytes;
    std::vector<std::uint64_t> ends;  // payload i is [ends[i-1], ends[i])
    std::span<const std::uint8_t> at(std::size_t i) const {
        const std::uint64_t b = i == 0 ? 0 : ends[i - 1];
        return {bytes.data() + b, static_cast<std::size_t>(ends[i] - b)};
    }
};

void load(const std::string& path, std::uint64_t limit, HeldPayloads& out) {
    PcapngReader r(path);
    Packet p{};
    while ((limit == 0 || out.ends.size() < limit) && r.next(p)) {
        out.bytes.insert(out.bytes.end(), p.udp_payload.begin(), p.udp_payload.end());
        out.ends.push_back(out.bytes.size());
        if ((out.ends.size() & ((1u << 22) - 1)) == 0)
            std::fprintf(stderr, "  loaded %zu packets, %.2f GB\n", out.ends.size(), out.bytes.size() / 1e9);
    }
}

std::int64_t get_i64(const std::uint8_t* p) {
    std::uint64_t v = 0;
    for (int i = 0; i < 8; ++i) v |= static_cast<std::uint64_t>(p[i]) << (8 * i);
    return static_cast<std::int64_t>(v);
}

// State shared by the main (send) thread and the server thread.
struct Shared {
    int tcp = -1;
    std::mutex write_mu;  // every TCP write
    std::mutex ring_mu;   // ring store and collect
    RetransmitRing ring;
    std::atomic<bool> handler_closed{false};
    // Written by the server thread only; read by main after join().
    std::uint64_t requests = 0, retransmit_packets = 0, unserved = 0;
    std::string server_error;

    explicit Shared(std::uint64_t ring_bytes) : ring(static_cast<std::size_t>(ring_bytes)) {}

    // False if the handler is gone.
    bool write(const std::vector<std::uint8_t>& frame) {
        std::lock_guard<std::mutex> g(write_mu);
        return netu::write_all(tcp, frame.data(), frame.size());
    }
};

// Reads Request frames until the handler closes TCP. Blocking reads are safe:
// the handler never blocks on its own writes, so it keeps draining what this
// thread sends, and Requests queue in the kernel while this thread writes.
void serve(Shared& sh) {
    FrameParser parser;
    std::vector<std::uint8_t> in(1 << 16), out;
    std::vector<std::span<const std::uint8_t>> spans;
    try {
        while (true) {
            const ssize_t n = ::recv(sh.tcp, in.data(), in.size(), 0);
            if (n == 0) break;
            if (n < 0) {
                if (errno == EINTR) continue;
                if (errno != ECONNRESET) sh.server_error = std::string("recv(tcp): ") + std::strerror(errno);
                break;
            }
            parser.feed({in.data(), static_cast<std::size_t>(n)});
            out.clear();
            Frame f{};
            while (parser.next(f)) {
                if (f.type != FrameType::Request || f.body.size() != 16)
                    throw std::runtime_error("unexpected frame from handler");
                const std::int64_t from = get_i64(f.body.data()), to = get_i64(f.body.data() + 8);
                ++sh.requests;
                std::lock_guard<std::mutex> g(sh.ring_mu);
                spans.clear();
                if (from <= to && sh.ring.collect(f.epoch, from, to, spans)) {
                    for (const auto& s : spans) encode_frame(out, FrameType::Retransmit, f.epoch, s);
                    sh.retransmit_packets += spans.size();
                } else {
                    encode_range(out, FrameType::Unserved, f.epoch, from, to);
                    ++sh.unserved;
                }
            }
            if (!out.empty() && !sh.write(out)) break;
        }
    } catch (const std::exception& e) {
        sh.server_error = e.what();
    }
    sh.handler_closed.store(true, std::memory_order_release);
}

}  // namespace

int main(int argc, char** argv) {
    Options o;
    for (int i = 1; i < argc; ++i) {
        const std::string a = argv[i];
        const bool has = i + 1 < argc;
        if (a == "--group" && has) {
            o.group = argv[++i];
        } else if (a == "--port" && has) {
            if (!parse_port(argv[++i], o.port)) return usage(argv[0]);
        } else if (a == "--ctrl-port" && has) {
            if (!parse_port(argv[++i], o.ctrl_port)) return usage(argv[0]);
        } else if (a == "--rate" && has) {
            std::uint64_t r = 0;
            if (!parse_u64(argv[++i], r)) return usage(argv[0]);
            o.rate = static_cast<double>(r);
        } else if (a == "--drop" && has) {
            o.drop = argv[++i];
        } else if (a == "--seed" && has) {
            if (!parse_u64(argv[++i], o.seed)) return usage(argv[0]);
        } else if (a == "--ring-bytes" && has) {
            if (!parse_u64(argv[++i], o.ring_bytes) || o.ring_bytes == 0) return usage(argv[0]);
        } else if (a == "--packet-limit" && has) {
            if (!parse_u64(argv[++i], o.packet_limit)) return usage(argv[0]);
        } else if (a == "--ram") {
            o.ram = true;
        } else if (o.path.empty() && a.rfind("--", 0) != 0) {
            o.path = a;
        } else {
            return usage(argv[0]);
        }
    }
    const std::optional<DropSpec> spec = parse_drop_spec(o.drop, o.seed);
    if (o.path.empty() || !spec) return usage(argv[0]);
    ::signal(SIGPIPE, SIG_IGN);

    try {
        sockaddr_in dest{};
        netu::Fd udp = netu::mcast_sender(o.group, o.port, dest);
        netu::Fd listener = netu::tcp_listen(o.ctrl_port);
        std::fprintf(stderr, "listening on 127.0.0.1:%d\n", o.ctrl_port);
        netu::Fd tcp = netu::tcp_accept(listener.get());
        listener.reset();
        std::fprintf(stderr, "handler connected\n");

        HeldPayloads held;
        if (o.ram) {
            std::fprintf(stderr, "loading payloads...\n");
            load(o.path, o.packet_limit, held);
        }
        std::optional<PcapngReader> reader;
        if (!o.ram) reader.emplace(o.path);

        Shared sh(o.ring_bytes);
        sh.tcp = tcp.get();
        std::thread server(serve, std::ref(sh));

        SegmentFilter filter(kDeepPlus);
        DropInjector drops(*spec);
        Segment seg{};
        std::vector<std::uint8_t> frame;
        std::uint32_t epoch = 0;
        u64 packets = 0, skipped = 0, segments_sent = 0, messages_sent = 0, send_retries = 0;
        u64 accepted = 0, paced_messages = 0;
        bool cut_short = false;
        std::string error;
        std::optional<clk::time_point> t0;
        clk::time_point t_last{};

        try {
            std::size_t idx = 0;
            while (true) {
                std::span<const std::uint8_t> payload;
                if (o.ram) {
                    if (idx >= held.ends.size()) break;
                    payload = held.at(idx++);
                } else {
                    if (o.packet_limit != 0 && packets >= o.packet_limit) break;
                    Packet p{};
                    if (!reader->next(p)) break;
                    payload = p.udp_payload;
                }
                ++packets;
                if ((packets & ((1u << 22) - 1)) == 0)
                    std::fprintf(stderr, "  sent %llu packets, %llu messages\n", packets, messages_sent);
                if (sh.handler_closed.load(std::memory_order_acquire)) {
                    cut_short = true;
                    break;
                }
                if (!filter.accept(payload, 0, packets, seg)) {
                    ++skipped;
                    continue;
                }
                ++accepted;
                if (!t0) t0 = clk::now();
                if (seg.restart) {
                    frame.clear();
                    encode_epoch_start(frame, ++epoch, seg.expected_before, seg.hdr.send_time);
                    if (!sh.write(frame)) {
                        cut_short = true;
                        break;
                    }
                }
                {
                    std::lock_guard<std::mutex> g(sh.ring_mu);
                    sh.ring.store(epoch, seg.hdr.first_seq, seg.hdr.msg_count, seg.payload);
                }
                if (!drops.drop()) {
                    while (::sendto(udp.get(), seg.payload.data(), seg.payload.size(), 0,
                                    reinterpret_cast<const sockaddr*>(&dest), sizeof dest) < 0) {
                        if (errno == EINTR) continue;
                        if (errno != ENOBUFS && errno != EAGAIN) netu::fail("sendto");
                        ++send_retries;
                        std::this_thread::sleep_for(std::chrono::microseconds(50));
                    }
                    ++segments_sent;
                    messages_sent += seg.hdr.msg_count;
                }
                paced_messages += seg.hdr.msg_count;
                if (o.rate > 0) {
                    const auto target =
                        *t0 + std::chrono::nanoseconds(static_cast<std::int64_t>(paced_messages * 1e9 / o.rate));
                    for (auto now = clk::now(); now < target; now = clk::now()) {
                        if (target - now > std::chrono::microseconds(200))
                            std::this_thread::sleep_for(target - now - std::chrono::microseconds(100));
                    }
                }
            }
            t_last = clk::now();
            if (!cut_short) {
                frame.clear();
                encode_end(frame, epoch, filter.tracker().expected());
                if (!sh.write(frame)) cut_short = true;
            }
        } catch (const std::exception& e) {
            error = e.what();
            t_last = clk::now();
        }
        if (!error.empty()) ::shutdown(tcp.get(), SHUT_RDWR);  // unblocks the server thread
        server.join();
        if (error.empty() && !sh.server_error.empty()) error = "server: " + sh.server_error;

        const std::int64_t send_wall_ns =
            t0 ? std::chrono::duration_cast<std::chrono::nanoseconds>(t_last - *t0).count() : 0;
        std::uint64_t evicted = 0;
        {
            std::lock_guard<std::mutex> g(sh.ring_mu);
            evicted = sh.ring.evicted();
        }
        std::printf("{\n");
        std::printf("  \"packets_read\": %llu,\n", packets);
        std::printf("  \"skipped\": %llu,\n", skipped);
        std::printf("  \"segments\": %llu,\n", accepted);
        std::printf("  \"segments_sent\": %llu,\n", segments_sent);
        std::printf("  \"messages_sent\": %llu,\n", messages_sent);
        std::printf("  \"dropped\": %llu,\n", static_cast<u64>(drops.dropped()));
        std::printf("  \"bursts\": %llu,\n", static_cast<u64>(drops.bursts()));
        std::printf("  \"epochs\": %llu,\n", accepted == 0 ? 0ULL : static_cast<u64>(epoch) + 1);
        std::printf("  \"retransmit_requests\": %llu,\n", static_cast<u64>(sh.requests));
        std::printf("  \"retransmit_packets\": %llu,\n", static_cast<u64>(sh.retransmit_packets));
        std::printf("  \"unserved\": %llu,\n", static_cast<u64>(sh.unserved));
        std::printf("  \"ring_evicted\": %llu,\n", static_cast<u64>(evicted));
        std::printf("  \"ring_bytes\": %llu,\n", static_cast<u64>(o.ring_bytes));
        std::printf("  \"send_retries\": %llu,\n", send_retries);
        std::printf("  \"send_wall_ns\": %lld,\n", static_cast<long long>(send_wall_ns));
        std::printf("  \"rate_target\": %.0f,\n", o.rate);
        std::printf("  \"ram\": %s,\n", o.ram ? "true" : "false");
        std::printf("  \"end_sent\": %s\n", cut_short || !error.empty() ? "false" : "true");
        std::printf("}\n");
        std::fflush(stdout);
        if (!error.empty()) {
            std::fprintf(stderr, "error: %s\n", error.c_str());
            return 1;
        }
        if (cut_short) {
            std::fprintf(stderr, "error: handler closed the control channel before End\n");
            return 1;
        }
        return 0;
    } catch (const std::exception& e) {
        std::fprintf(stderr, "error: %s\n", e.what());
        return 1;
    }
}
