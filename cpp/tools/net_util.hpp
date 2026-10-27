#pragma once

// Small POSIX socket helpers for feed_publisher and feed_handler. Linux and
// macOS. Every failure throws std::runtime_error naming the call and errno.
//
// Multicast is loopback only: the sender pins IP_MULTICAST_IF to 127.0.0.1,
// sets IP_MULTICAST_LOOP=1 so the local receiver gets a copy, and
// IP_MULTICAST_TTL=0 so nothing leaves the host. The receiver joins the group
// on interface 127.0.0.1.

#include <arpa/inet.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <sys/socket.h>
#include <unistd.h>

#include <cerrno>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <stdexcept>
#include <string>

namespace netu {

[[noreturn]] inline void fail(const std::string& what) {
    throw std::runtime_error(what + ": " + std::strerror(errno));
}

// Owns one file descriptor; closes it on destruction.
class Fd {
  public:
    Fd() = default;
    explicit Fd(int fd) : fd_(fd) {}
    ~Fd() { reset(); }
    Fd(const Fd&) = delete;
    Fd& operator=(const Fd&) = delete;
    Fd(Fd&& o) noexcept : fd_(o.fd_) { o.fd_ = -1; }
    Fd& operator=(Fd&& o) noexcept {
        if (this != &o) {
            reset();
            fd_ = o.fd_;
            o.fd_ = -1;
        }
        return *this;
    }
    int get() const { return fd_; }
    bool valid() const { return fd_ >= 0; }
    void reset() {
        if (fd_ >= 0) ::close(fd_);
        fd_ = -1;
    }

  private:
    int fd_ = -1;
};

inline in_addr parse_ipv4(const std::string& s) {
    in_addr a{};
    if (::inet_pton(AF_INET, s.c_str(), &a) != 1) throw std::runtime_error("not an IPv4 address: " + s);
    return a;
}

inline sockaddr_in make_addr(const std::string& ip, int port) {
    sockaddr_in sa{};
    sa.sin_family = AF_INET;
    sa.sin_port = htons(static_cast<std::uint16_t>(port));
    sa.sin_addr = parse_ipv4(ip);
    return sa;
}

inline void set_nonblocking(int fd) {
    const int fl = ::fcntl(fd, F_GETFL, 0);
    if (fl < 0 || ::fcntl(fd, F_SETFL, fl | O_NONBLOCK) < 0) fail("fcntl O_NONBLOCK");
}

template <class T>
inline void setopt(int fd, int level, int name, const T& v, const char* what) {
    if (::setsockopt(fd, level, name, &v, sizeof v) != 0) fail(what);
}

// Asks for `bytes` of receive buffer and returns what getsockopt reports
// afterwards. The kernel caps the request (Linux: net.core.rmem_max) and Linux
// reports twice the granted value (its bookkeeping overhead), so the return
// is the kernel's own figure, not the request. Warns on stderr when the
// granted size is below the request (the cap bit): on Linux that is got / 2,
// since a request capped at rmem_max still reads back as up to twice the
// request; elsewhere (macOS) the figure is the granted size itself.
inline int set_rcvbuf(int fd, int bytes) {
    setopt(fd, SOL_SOCKET, SO_RCVBUF, bytes, "setsockopt SO_RCVBUF");
    int got = 0;
    socklen_t len = sizeof got;
    if (::getsockopt(fd, SOL_SOCKET, SO_RCVBUF, &got, &len) != 0) fail("getsockopt SO_RCVBUF");
#if defined(__linux__)
    const int granted = got / 2;
#else
    const int granted = got;
#endif
    if (granted < bytes)
        std::fprintf(stderr,
                     "warning: SO_RCVBUF %d requested, kernel reports %d (capped; on Linux raise "
                     "net.core.rmem_max)\n",
                     bytes, got);
    return got;
}

// UDP socket for sending to the group over loopback; `dest` is filled.
inline Fd mcast_sender(const std::string& group, int port, sockaddr_in& dest) {
    Fd s(::socket(AF_INET, SOCK_DGRAM, 0));
    if (!s.valid()) fail("socket(udp)");
    const in_addr lo = parse_ipv4("127.0.0.1");
    setopt(s.get(), IPPROTO_IP, IP_MULTICAST_IF, lo, "setsockopt IP_MULTICAST_IF");
    const unsigned char loop = 1, ttl = 0;
    setopt(s.get(), IPPROTO_IP, IP_MULTICAST_LOOP, loop, "setsockopt IP_MULTICAST_LOOP");
    setopt(s.get(), IPPROTO_IP, IP_MULTICAST_TTL, ttl, "setsockopt IP_MULTICAST_TTL");
    dest = make_addr(group, port);
    return s;
}

// UDP socket bound to group:port and joined to the group on 127.0.0.1.
// `rcvbuf_actual` receives set_rcvbuf's report.
inline Fd mcast_receiver(const std::string& group, int port, int rcvbuf, int& rcvbuf_actual) {
    Fd s(::socket(AF_INET, SOCK_DGRAM, 0));
    if (!s.valid()) fail("socket(udp)");
    const int one = 1;
    setopt(s.get(), SOL_SOCKET, SO_REUSEADDR, one, "setsockopt SO_REUSEADDR");
#ifdef SO_REUSEPORT
    setopt(s.get(), SOL_SOCKET, SO_REUSEPORT, one, "setsockopt SO_REUSEPORT");
#endif
    rcvbuf_actual = set_rcvbuf(s.get(), rcvbuf);
    const sockaddr_in sa = make_addr(group, port);
    if (::bind(s.get(), reinterpret_cast<const sockaddr*>(&sa), sizeof sa) != 0) fail("bind(udp)");
    ip_mreq m{};
    m.imr_multiaddr = parse_ipv4(group);
    m.imr_interface = parse_ipv4("127.0.0.1");
    setopt(s.get(), IPPROTO_IP, IP_ADD_MEMBERSHIP, m, "setsockopt IP_ADD_MEMBERSHIP");
    return s;
}

// TCP listener on 127.0.0.1:port, backlog 1.
inline Fd tcp_listen(int port) {
    Fd s(::socket(AF_INET, SOCK_STREAM, 0));
    if (!s.valid()) fail("socket(tcp)");
    const int one = 1;
    setopt(s.get(), SOL_SOCKET, SO_REUSEADDR, one, "setsockopt SO_REUSEADDR");
    const sockaddr_in sa = make_addr("127.0.0.1", port);
    if (::bind(s.get(), reinterpret_cast<const sockaddr*>(&sa), sizeof sa) != 0) fail("bind(tcp)");
    if (::listen(s.get(), 1) != 0) fail("listen");
    return s;
}

inline void set_nodelay(int fd) {
    const int one = 1;
    setopt(fd, IPPROTO_TCP, TCP_NODELAY, one, "setsockopt TCP_NODELAY");
}

// Blocking accept of one connection, with TCP_NODELAY set.
inline Fd tcp_accept(int listen_fd) {
    int c;
    do {
        c = ::accept(listen_fd, nullptr, nullptr);
    } while (c < 0 && errno == EINTR);
    if (c < 0) fail("accept");
    Fd f(c);
    set_nodelay(f.get());
    return f;
}

// One blocking connect attempt; an invalid Fd (errno set) if it is refused,
// so the caller can retry while the listener is not up yet.
inline Fd tcp_connect_once(const std::string& host, int port) {
    Fd s(::socket(AF_INET, SOCK_STREAM, 0));
    if (!s.valid()) fail("socket(tcp)");
    const sockaddr_in sa = make_addr(host, port);
    if (::connect(s.get(), reinterpret_cast<const sockaddr*>(&sa), sizeof sa) != 0) return Fd();
    set_nodelay(s.get());
    return s;
}

// Writes every byte to a blocking fd. False if the peer is gone (EPIPE,
// ECONNRESET); throws on any other error. Callers ignore SIGPIPE.
inline bool write_all(int fd, const std::uint8_t* p, std::size_t n) {
    while (n > 0) {
        const ssize_t w = ::send(fd, p, n, 0);
        if (w < 0) {
            if (errno == EINTR) continue;
            if (errno == EPIPE || errno == ECONNRESET) return false;
            fail("send(tcp)");
        }
        p += w;
        n -= static_cast<std::size_t>(w);
    }
    return true;
}

}  // namespace netu
