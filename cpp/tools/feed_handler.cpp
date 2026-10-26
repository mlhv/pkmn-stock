// DEEP+ feed handler over loopback multicast with TCP gap recovery. Joins the
// group, connects the control channel to feed_publisher, and feeds both into
// RecoveringHandler (gap_recovery.hpp), which holds packets behind a gap,
// requests missing ranges and applies messages to its books in sequence
// order. Its per-epoch book digests are compared with iex_digest's direct
// file replay when --expect is given.
//
//   feed_handler [--group 239.255.0.1] [--port 31001] [--ctrl-host 127.0.0.1]
//       [--ctrl-port 31002] [--rcvbuf BYTES (default 7500000)]
//       [--expect DIGESTS_FILE] [--timeout-s 600]
//
// Flow:
//   1. Join the group first, then connect TCP (retrying until the publisher
//      listens), so no datagram can be sent before the handler is listening.
//   2. A poll() loop over the TCP and UDP sockets, both non-blocking:
//        a. TCP first: read everything available and parse frames.
//           Retransmit and Unserved frames go to the core at once. EpochStart
//           and End are deferred until the UDP socket has been drained to
//           EAGAIN (or for at most kCtrlDeferMs): the publisher writes them
//           right before or after the multicast packets they bound, so the
//           datagrams already queued in the kernel would otherwise be
//           requested as a missing epoch tail and then arrive as duplicates.
//           Deferring only delays an announcement; the core accepts epoch
//           frames in any order relative to datagrams.
//        b. UDP: up to kUdpBatch datagrams per pass with recvmsg into a
//           65,535-byte buffer. A datagram with MSG_TRUNC is treated as not
//           received (counted in `truncated` and in `rejected`), never fed to
//           the core, so its hole is requested like any loss.
//        c. Request frames from the core's RequestSink are appended to an
//           outbound buffer and flushed whenever the socket is writable
//           (POLLOUT). The handler never blocks on a TCP write, so it always
//           keeps reading what the publisher sends: no write-write deadlock.
//   3. Stop on done(), failed(), the publisher closing TCP, or the timeout.
//      Close TCP (the publisher's server thread then exits), print one JSON
//      object on stdout.
//
// Exit: 0 done and every digest matched (or no --expect); 1 done but a digest
// differs (the first differing epoch is printed); 2 unrecoverable (failed()),
// publisher closed early, timeout, or an error.

#include <poll.h>
#include <signal.h>
#include <sys/socket.h>

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <exception>
#include <optional>
#include <span>
#include <string>
#include <thread>
#include <vector>

#include "net_util.hpp"
#include "pkmn_md/book_digest.hpp"
#include "pkmn_md/gap_recovery.hpp"
#include "pkmn_md/recovery_proto.hpp"

using namespace pkmn::md;
using clk = std::chrono::steady_clock;
using u64 = unsigned long long;

namespace {

constexpr int kUdpBatch = 512;
constexpr std::size_t kUdpBuf = 65535;
constexpr int kCtrlDeferMs = 20;

struct Options {
    std::string group = "239.255.0.1", ctrl_host = "127.0.0.1", expect;
    int port = 31001, ctrl_port = 31002, rcvbuf = 7500000;
    std::uint64_t timeout_s = 600;
};

int usage(const char* argv0) {
    std::fprintf(stderr,
                 "usage: %s [--group 239.255.0.1] [--port 31001] [--ctrl-host 127.0.0.1] [--ctrl-port 31002]\n"
                 "       [--rcvbuf BYTES] [--expect DIGESTS_FILE] [--timeout-s 600]\n",
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

std::int64_t get_i64(const std::uint8_t* p) {
    std::uint64_t v = 0;
    for (int i = 0; i < 8; ++i) v |= static_cast<std::uint64_t>(p[i]) << (8 * i);
    return static_cast<std::int64_t>(v);
}

// Outbound Request frames, flushed without ever blocking.
class Outbox : public RequestSink {
  public:
    void request(std::uint32_t epoch, std::int64_t from, std::int64_t to) override {
        encode_range(buf_, FrameType::Request, epoch, from, to);
    }
    bool pending() const { return off_ < buf_.size(); }
    // Writes what the socket takes now. False if the peer is gone.
    bool flush(int fd) {
        while (off_ < buf_.size()) {
            const ssize_t w = ::send(fd, buf_.data() + off_, buf_.size() - off_, 0);
            if (w < 0) {
                if (errno == EINTR) continue;
                if (errno == EAGAIN || errno == EWOULDBLOCK) break;
                if (errno == EPIPE || errno == ECONNRESET) return false;
                netu::fail("send(tcp)");
            }
            off_ += static_cast<std::size_t>(w);
        }
        if (off_ == buf_.size()) {
            buf_.clear();
            off_ = 0;
        } else if (off_ > (std::size_t{1} << 20)) {
            buf_.erase(buf_.begin(), buf_.begin() + static_cast<std::ptrdiff_t>(off_));
            off_ = 0;
        }
        return true;
    }

  private:
    std::vector<std::uint8_t> buf_;
    std::size_t off_ = 0;
};

struct Ctrl {
    FrameType type;
    std::uint32_t epoch;
    std::int64_t a, b;
};

}  // namespace

int main(int argc, char** argv) {
    Options o;
    for (int i = 1; i < argc; ++i) {
        const std::string a = argv[i];
        const bool has = i + 1 < argc;
        std::uint64_t v = 0;
        if (a == "--group" && has) {
            o.group = argv[++i];
        } else if (a == "--port" && has) {
            if (!parse_port(argv[++i], o.port)) return usage(argv[0]);
        } else if (a == "--ctrl-host" && has) {
            o.ctrl_host = argv[++i];
        } else if (a == "--ctrl-port" && has) {
            if (!parse_port(argv[++i], o.ctrl_port)) return usage(argv[0]);
        } else if (a == "--rcvbuf" && has) {
            if (!parse_u64(argv[++i], v) || v == 0 || v > 0x7fffffffULL) return usage(argv[0]);
            o.rcvbuf = static_cast<int>(v);
        } else if (a == "--expect" && has) {
            o.expect = argv[++i];
        } else if (a == "--timeout-s" && has) {
            if (!parse_u64(argv[++i], o.timeout_s) || o.timeout_s == 0) return usage(argv[0]);
        } else {
            return usage(argv[0]);
        }
    }
    ::signal(SIGPIPE, SIG_IGN);

    std::vector<EpochDigest> expected;
    try {
        if (!o.expect.empty()) expected = read_digests(o.expect);
    } catch (const std::exception& e) {
        std::fprintf(stderr, "error: %s\n", e.what());
        return 2;
    }

    const auto t_start = clk::now();
    const auto deadline = t_start + std::chrono::seconds(o.timeout_s);
    Outbox outbox;
    RecoveringHandler core(outbox);
    int rcvbuf_actual = 0;
    u64 udp_received = 0, truncated = 0;
    std::optional<clk::time_point> t_first, t_done;
    std::string status = "running", error;

    try {
        // 1. Join before connecting: the publisher sends only after accept.
        netu::Fd udp = netu::mcast_receiver(o.group, o.port, o.rcvbuf, rcvbuf_actual);
        netu::set_nonblocking(udp.get());
        netu::Fd tcp;
        while (!(tcp = netu::tcp_connect_once(o.ctrl_host, o.ctrl_port)).valid()) {
            if (clk::now() >= deadline) {
                status = "timeout";
                throw std::runtime_error("timed out connecting to the publisher");
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
        netu::set_nonblocking(tcp.get());

        FrameParser parser;
        std::vector<std::uint8_t> tcp_in(1 << 16), dgram(kUdpBuf);
        std::vector<Ctrl> deferred;
        clk::time_point deferred_since{};
        bool peer_closed = false;

        auto deliver = [&](const Ctrl& c) {
            if (c.type == FrameType::EpochStart)
                core.on_epoch_start(c.epoch, c.a, c.b);
            else
                core.on_end(c.epoch, c.a);
        };
        auto finished = [&] { return core.done() || core.failed(); };

        while (!finished()) {
            const auto now = clk::now();
            if (now >= deadline) {
                status = "timeout";
                break;
            }
            pollfd fds[2] = {{tcp.get(), static_cast<short>(POLLIN | (outbox.pending() ? POLLOUT : 0)), 0},
                             {udp.get(), POLLIN, 0}};
            const auto left = std::chrono::duration_cast<std::chrono::milliseconds>(deadline - now).count();
            int wait_ms = static_cast<int>(std::min<long long>(deferred.empty() ? 100 : kCtrlDeferMs, left + 1));
            if (::poll(fds, 2, wait_ms) < 0) {
                if (errno == EINTR) continue;
                netu::fail("poll");
            }

            // a. TCP first.
            if (!peer_closed && (fds[0].revents & (POLLIN | POLLHUP | POLLERR)) != 0) {
                for (int chunk = 0; chunk < 64; ++chunk) {
                    const ssize_t n = ::recv(tcp.get(), tcp_in.data(), tcp_in.size(), 0);
                    if (n == 0) {
                        peer_closed = true;
                        break;
                    }
                    if (n < 0) {
                        if (errno == EINTR) continue;
                        if (errno == EAGAIN || errno == EWOULDBLOCK) break;
                        if (errno == ECONNRESET) {
                            peer_closed = true;
                            break;
                        }
                        netu::fail("recv(tcp)");
                    }
                    parser.feed({tcp_in.data(), static_cast<std::size_t>(n)});
                    Frame f{};
                    while (parser.next(f)) {
                        switch (f.type) {
                            case FrameType::Retransmit:
                                core.on_retransmit(f.epoch, f.body);
                                break;
                            case FrameType::Unserved:
                                if (f.body.size() != 16) throw std::runtime_error("bad Unserved frame");
                                core.on_unserved(f.epoch, get_i64(f.body.data()), get_i64(f.body.data() + 8));
                                break;
                            case FrameType::EpochStart:
                                if (f.body.size() != 16) throw std::runtime_error("bad EpochStart frame");
                                if (deferred.empty()) deferred_since = clk::now();
                                deferred.push_back(
                                    {f.type, f.epoch, get_i64(f.body.data()), get_i64(f.body.data() + 8)});
                                break;
                            case FrameType::End:
                                if (f.body.size() != 8) throw std::runtime_error("bad End frame");
                                if (deferred.empty()) deferred_since = clk::now();
                                deferred.push_back({f.type, f.epoch, get_i64(f.body.data()), 0});
                                break;
                            case FrameType::Request:
                                throw std::runtime_error("unexpected Request frame from the publisher");
                        }
                    }
                }
            }

            // b. UDP.
            bool udp_empty = false;
            for (int k = 0; k < kUdpBatch && !core.failed(); ++k) {
                iovec iov{dgram.data(), dgram.size()};
                msghdr mh{};
                mh.msg_iov = &iov;
                mh.msg_iovlen = 1;
                const ssize_t n = ::recvmsg(udp.get(), &mh, 0);
                if (n < 0) {
                    if (errno == EINTR) continue;
                    if (errno == EAGAIN || errno == EWOULDBLOCK) {
                        udp_empty = true;
                        break;
                    }
                    netu::fail("recvmsg(udp)");
                }
                ++udp_received;
                if (!t_first) t_first = clk::now();
                if ((mh.msg_flags & MSG_TRUNC) != 0) {
                    ++truncated;
                    continue;
                }
                core.on_datagram({dgram.data(), static_cast<std::size_t>(n)});
            }

            // Epoch frames once the queued datagrams are in (or after the cap).
            if (!deferred.empty() &&
                (udp_empty || clk::now() - deferred_since >= std::chrono::milliseconds(kCtrlDeferMs))) {
                for (const Ctrl& c : deferred) deliver(c);
                deferred.clear();
            }

            // c. Requests out.
            if (!peer_closed && !outbox.flush(tcp.get())) peer_closed = true;
            if (peer_closed && !finished()) {
                status = "publisher_closed";
                break;
            }
        }
        if (core.done()) t_done = clk::now();
        tcp.reset();  // the publisher's server thread sees EOF and exits
    } catch (const std::exception& e) {
        error = e.what();
        if (status == "running") status = "error";
    }
    if (core.done()) status = "done";
    else if (core.failed()) status = "failed";

    // Digest comparison.
    const auto& got = core.digests();
    std::optional<std::size_t> first_diff;
    if (!o.expect.empty()) {
        const std::size_t n = std::max(got.size(), expected.size());
        for (std::size_t i = 0; i < n && !first_diff; ++i) {
            if (i >= got.size() || i >= expected.size() || got[i].digest != expected[i].digest ||
                got[i].orders != expected[i].orders || got[i].messages != expected[i].messages)
                first_diff = i;
        }
    }
    const bool match = !first_diff.has_value();

    const RecoveryStats& s = core.stats();
    const auto end_t = t_done ? *t_done : clk::now();
    const std::int64_t wall_ns =
        t_first ? std::chrono::duration_cast<std::chrono::nanoseconds>(end_t - *t_first).count() : 0;
    const double mps = wall_ns > 0 ? static_cast<double>(s.messages_applied) * 1e9 / static_cast<double>(wall_ns) : 0;

    std::printf("{\n");
    std::printf("  \"status\": \"%s\",\n", status.c_str());
    std::printf("  \"udp_received\": %llu,\n", udp_received);
    std::printf("  \"truncated\": %llu,\n", truncated);
    std::printf("  \"datagrams\": %llu,\n", static_cast<u64>(s.datagrams));
    std::printf("  \"rejected\": %llu,\n", static_cast<u64>(s.rejected + truncated));
    std::printf("  \"segments_applied\": %llu,\n", static_cast<u64>(s.segments_applied));
    std::printf("  \"messages_applied\": %llu,\n", static_cast<u64>(s.messages_applied));
    std::printf("  \"gaps\": %llu,\n", static_cast<u64>(s.gaps));
    std::printf("  \"requests\": %llu,\n", static_cast<u64>(s.requests));
    std::printf("  \"requested_messages\": %llu,\n", static_cast<u64>(s.requested_messages));
    std::printf("  \"retransmit_segments\": %llu,\n", static_cast<u64>(s.retransmit_segments));
    std::printf("  \"duplicate_messages\": %llu,\n", static_cast<u64>(s.duplicate_messages));
    std::printf("  \"epochs\": %llu,\n", static_cast<u64>(s.epochs));
    std::printf("  \"parked\": %llu,\n", static_cast<u64>(s.parked));
    std::printf("  \"unrecoverable\": %llu,\n", static_cast<u64>(s.unrecoverable));
    std::printf("  \"held_max\": %llu,\n", static_cast<u64>(s.held_max));
    std::printf("  \"rcvbuf_requested\": %d,\n", o.rcvbuf);
    std::printf("  \"rcvbuf_actual\": %d,\n", rcvbuf_actual);
    std::printf("  \"wall_ns\": %lld,\n", static_cast<long long>(wall_ns));
    std::printf("  \"msgs_per_sec\": %.0f,\n", mps);
    std::printf("  \"digests\": [");
    for (std::size_t i = 0; i < got.size(); ++i)
        std::printf("%s{\"epoch\": %zu, \"digest\": \"%016llx\", \"orders\": %llu, \"messages\": %llu}",
                    i == 0 ? "" : ", ", i, static_cast<u64>(got[i].digest), static_cast<u64>(got[i].orders),
                    static_cast<u64>(got[i].messages));
    std::printf("]");
    if (!o.expect.empty()) {
        std::printf(",\n  \"expected_epochs\": %zu,\n", expected.size());
        std::printf("  \"digest_match\": %s", match ? "true" : "false");
        if (first_diff) std::printf(",\n  \"first_mismatch_epoch\": %zu", *first_diff);
    }
    std::printf("\n}\n");
    std::fflush(stdout);

    if (!error.empty()) std::fprintf(stderr, "error: %s\n", error.c_str());
    if (status != "done") {
        std::fprintf(stderr, "handler stopped: %s\n", status.c_str());
        return 2;
    }
    if (!match) {
        std::fprintf(stderr, "digest mismatch at epoch %zu\n", *first_diff);
        return 1;
    }
    return 0;
}
