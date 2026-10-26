#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_string.hpp>

#include <atomic>
#include <cstdlib>
#include <new>
#include <stdexcept>
#include <string>
#include <vector>

#include "md_fixtures.hpp"
#include "pkmn_md/pcapng.hpp"

using namespace pkmn::md;
using namespace md_fixtures;
using Catch::Matchers::ContainsSubstring;

// Global allocation counter: the "decoding must not allocate per message"
// rule is checked directly, not assumed. Only this test binary replaces new.
// Not static: test_deep_plus.cpp reads it too.
std::atomic<std::uint64_t> g_allocs{0};
void* operator new(std::size_t n) {
    ++g_allocs;
    if (void* p = std::malloc(n == 0 ? 1 : n)) return p;
    throw std::bad_alloc();
}
void operator delete(void* p) noexcept { std::free(p); }
void operator delete(void* p, std::size_t) noexcept { std::free(p); }

namespace {

struct Got {
    std::int64_t ts_ns;
    std::string payload;
};

std::vector<Got> read_all(PcapngReader& r) {
    std::vector<Got> out;
    Packet p{};
    while (r.next(p)) out.push_back(Got{p.ts_ns, std::string(p.udp_payload.begin(), p.udp_payload.end())});
    return out;
}

std::vector<Got> read_all(const std::string& path) {
    PcapngReader r(path);
    return read_all(r);
}

// Reads until next() throws; returns the message (empty if it never threw).
std::string drain_error(const std::string& path) {
    try {
        PcapngReader r(path);
        Packet p{};
        while (r.next(p)) {
        }
    } catch (const std::runtime_error& e) {
        return e.what();
    }
    return {};
}

}  // namespace

TEST_CASE("pcapng: reads payloads and microsecond timestamps") {
    TempFile f(".pcapng");
    write_raw(f.path, capture({"a", "bb", "ccc"}, {1, 2, 3}));
    auto got = read_all(f.path);
    REQUIRE(got.size() == 3);
    CHECK(got[0].payload == "a");
    CHECK(got[1].payload == "bb");
    CHECK(got[2].payload == "ccc");
    CHECK(got[0].ts_ns == 1000);
    CHECK(got[1].ts_ns == 2000);
    CHECK(got[2].ts_ns == 3000);
}

TEST_CASE("pcapng: gzip and raw give identical packets") {
    std::vector<std::string> payloads;
    std::vector<std::uint64_t> ts;
    for (int i = 0; i < 500; ++i) {
        payloads.push_back(std::string(static_cast<std::size_t>(1 + i % 97), static_cast<char>('a' + i % 26)));
        ts.push_back(1'734'964'524'000'000ULL + static_cast<std::uint64_t>(i) * 13);
    }
    const Bytes cap = capture(payloads, ts);
    TempFile raw(".pcapng"), gz(".pcapng.gz");
    write_raw(raw.path, cap);
    write_gzip(gz.path, cap);
    auto a = read_all(raw.path);
    auto b = read_all(gz.path);
    REQUIRE(a.size() == 500);
    REQUIRE(b.size() == 500);
    for (std::size_t i = 0; i < a.size(); ++i) {
        CHECK(a[i].ts_ns == b[i].ts_ns);
        CHECK(a[i].payload == b[i].payload);
        CHECK(a[i].payload == payloads[i]);
        CHECK(a[i].ts_ns == static_cast<std::int64_t>(ts[i] * 1000));
    }
}

TEST_CASE("pcapng: format is detected by gzip magic, not extension") {
    const Bytes cap = capture({"x", "yy"}, {5, 6});
    TempFile gz_named_raw(".pcap.gz"), raw_named_gz(".pcapng");
    write_raw(gz_named_raw.path, cap);
    write_gzip(raw_named_gz.path, cap);
    CHECK(read_all(gz_named_raw.path).size() == 2);
    CHECK(read_all(raw_named_gz.path).size() == 2);
}

TEST_CASE("pcapng: skips unknown block types and non-UDP frames") {
    Bytes b = shb();
    append(b, block(0x0BAD, Bytes(17, 0x5A)));  // unknown block type
    append(b, idb());
    append(b, epb(10, udp_frame(bytes_of("one"))));
    append(b, block(5, Bytes(24, 0)));  // Interface Statistics Block
    append(b, epb(11, tcp_frame()));
    append(b, epb(12, arp_frame()));
    append(b, epb(13, udp_frame(bytes_of("two"), 1)));  // one 802.1Q tag
    append(b, epb(14, udp_frame(bytes_of("three"), 2)));  // QinQ
    append(b, block(0x80000001, Bytes(8, 1)));  // custom block
    TempFile f(".pcapng");
    write_raw(f.path, b);
    PcapngReader r(f.path);
    auto got = read_all(r);
    REQUIRE(got.size() == 3);
    CHECK(got[0].payload == "one");
    CHECK(got[1].payload == "two");
    CHECK(got[2].payload == "three");
    CHECK(got[2].ts_ns == 14000);
    CHECK(r.skipped_frames() == 2);
    CHECK(r.bytes_consumed() == b.size());
}

TEST_CASE("pcapng: if_tsresol defaults to microseconds and honours nanoseconds") {
    TempFile none(".pcapng"), nanos(".pcapng");
    write_raw(none.path, capture({"a"}, {7}, -1));
    write_raw(nanos.path, capture({"a"}, {1'734'964'524'123'456'789ULL}, 9));
    auto a = read_all(none.path);
    auto b = read_all(nanos.path);
    REQUIRE(a.size() == 1);
    REQUIRE(b.size() == 1);
    CHECK(a[0].ts_ns == 7000);
    CHECK(b[0].ts_ns == 1'734'964'524'123'456'789LL);
}

TEST_CASE("pcapng: truncated file throws with offset") {
    TempFile f(".pcapng");
    write_raw(f.path, capture({"a", "bb", "ccc"}, {1, 2, 3}));
    chop(f.path, 5);
    const std::string msg = drain_error(f.path);
    CHECK_THAT(msg, ContainsSubstring("truncated"));
    CHECK_THAT(msg, ContainsSubstring("offset"));
}

TEST_CASE("pcapng: raw file cut mid-block names the block's offset") {
    const Bytes cap = capture({"a", "bb", "ccc"}, {1, 2, 3});
    const Bytes last = epb(3, udp_frame(bytes_of("ccc")));
    const std::size_t last_start = cap.size() - last.size();
    TempFile f(".pcapng");
    write_raw(f.path, Bytes(cap.begin(), cap.begin() + static_cast<std::ptrdiff_t>(last_start + 30)));
    PcapngReader r(f.path);
    Packet p{};
    REQUIRE(r.next(p));
    REQUIRE(r.next(p));
    try {
        r.next(p);
        FAIL("expected truncation");
    } catch (const std::runtime_error& e) {
        CHECK_THAT(std::string(e.what()), ContainsSubstring("truncated"));
        CHECK_THAT(std::string(e.what()), ContainsSubstring("offset " + std::to_string(last_start)));
    }
}

TEST_CASE("pcapng: raw file cut inside a block header is truncated, not EOF") {
    const Bytes cap = capture({"a"}, {1});
    TempFile f(".pcapng");
    write_raw(f.path, cap);
    chop(f.path, cap.size() - (shb().size() + idb().size() + 3));  // 3 bytes of the EPB header left
    CHECK_THAT(drain_error(f.path), ContainsSubstring("truncated"));
}

TEST_CASE("pcapng: gzip stream cut short throws truncated") {
    std::vector<std::string> payloads(2000, std::string(200, 'q'));
    std::vector<std::uint64_t> ts(2000, 1);
    const Bytes cap = capture(payloads, ts);
    SECTION("cut in the middle of the deflate data") {
        TempFile gz(".pcap.gz");
        write_gzip(gz.path, cap);
        chop(gz.path, read_file(gz.path).size() / 2);
        const std::string msg = drain_error(gz.path);
        CHECK_THAT(msg, ContainsSubstring("truncated"));
        CHECK_THAT(msg, ContainsSubstring("offset"));
    }
    SECTION("only the gzip trailer missing") {
        TempFile gz(".pcap.gz");
        write_gzip(gz.path, cap);
        chop(gz.path, 5);
        const std::string msg = drain_error(gz.path);
        CHECK_THAT(msg, ContainsSubstring("truncated"));
        CHECK_THAT(msg, ContainsSubstring("offset"));
    }
}

TEST_CASE("pcapng: clean EOF after a complete block returns false") {
    TempFile f(".pcapng"), gz(".pcap.gz");
    const Bytes cap = capture({"a"}, {1});
    write_raw(f.path, cap);
    write_gzip(gz.path, cap);
    for (const auto* path : {&f.path, &gz.path}) {
        PcapngReader r(*path);
        Packet p{};
        CHECK(r.next(p));
        CHECK_FALSE(r.next(p));
        CHECK_FALSE(r.next(p));  // stays at EOF
        CHECK(r.bytes_consumed() == cap.size());
    }
}

TEST_CASE("pcapng: big-endian section is rejected clearly") {
    TempFile f(".pcapng");
    write_raw(f.path, shb_big_endian());
    CHECK_THAT(drain_error(f.path), ContainsSubstring("big-endian"));
}

TEST_CASE("pcapng: non-pcapng and empty files are rejected") {
    TempFile junk(".pcapng"), empty(".pcapng");
    write_raw(junk.path, bytes_of("this is not a capture file at all"));
    write_raw(empty.path, {});
    CHECK_THAT(drain_error(junk.path), ContainsSubstring("not a pcapng"));
    CHECK_THAT(drain_error(empty.path), ContainsSubstring("empty"));
    CHECK_THROWS_AS(PcapngReader("/nonexistent/dir/x.pcapng"), std::runtime_error);
}

TEST_CASE("pcapng: packet on an undeclared interface throws") {
    Bytes b = shb();
    append(b, epb(1, udp_frame(bytes_of("a"))));
    TempFile f(".pcapng");
    write_raw(f.path, b);
    CHECK_THAT(drain_error(f.path), ContainsSubstring("interface"));
}

TEST_CASE("pcapng: large packet crosses buffer boundary") {
    const std::string big(9000, 'Z');
    TempFile f(".pcapng"), gz(".pcap.gz");
    write_raw(f.path, capture({"small", big, "tail"}, {1, 2, 3}));
    write_gzip(gz.path, capture({"small", big, "tail"}, {1, 2, 3}));
    SECTION("default buffer") {
        auto got = read_all(gz.path);
        REQUIRE(got.size() == 3);
        CHECK(got[1].payload == big);
        CHECK(got[2].payload == "tail");
    }
    SECTION("buffer smaller than the block must grow") {
        for (const auto* path : {&f.path, &gz.path}) {
            PcapngReader r(*path, 4096);
            auto got = read_all(r);
            REQUIRE(got.size() == 3);
            CHECK(got[0].payload == "small");
            CHECK(got[1].payload == big);
            CHECK(got[2].payload == "tail");
        }
    }
}

TEST_CASE("pcapng: many packets straddling refills come back intact") {
    // ~6 MiB of packets so blocks straddle refills of the default 4 MiB
    // buffer, and of a tiny one with uneven sizes.
    std::vector<std::string> payloads;
    std::vector<std::uint64_t> ts;
    for (int i = 0; i < 6000; ++i) {
        payloads.push_back(std::string(static_cast<std::size_t>(700 + (i * 37) % 600), static_cast<char>('A' + i % 23)));
        ts.push_back(static_cast<std::uint64_t>(i));
    }
    const Bytes cap = capture(payloads, ts);
    TempFile raw(".pcapng"), gz(".pcap.gz");
    write_raw(raw.path, cap);
    write_gzip(gz.path, cap);
    for (std::size_t buf : {std::size_t{4} << 20, std::size_t{3001}}) {
        for (const auto* path : {&raw.path, &gz.path}) {
            PcapngReader r(*path, buf);
            auto got = read_all(r);
            REQUIRE(got.size() == payloads.size());
            bool all_equal = true;
            for (std::size_t i = 0; i < got.size(); ++i)
                all_equal = all_equal && got[i].payload == payloads[i] && got[i].ts_ns == static_cast<std::int64_t>(i * 1000);
            CHECK(all_equal);
        }
    }
}

TEST_CASE("pcapng: steady-state next() does not allocate") {
    std::vector<std::string> payloads(20000, std::string(300, 'm'));
    std::vector<std::uint64_t> ts(20000, 9);
    TempFile gz(".pcap.gz");
    write_gzip(gz.path, capture(payloads, ts));  // ~7 MiB decompressed: several refills
    PcapngReader r(gz.path);
    Packet p{};
    REQUIRE(r.next(p));  // first fill + SHB/IDB parsing may allocate
    std::uint64_t n = 1, bytes = 0;
    const std::uint64_t before = g_allocs.load();
    while (r.next(p)) {
        ++n;
        bytes += p.udp_payload.size();
    }
    const std::uint64_t after = g_allocs.load();
    CHECK(n == 20000);
    CHECK(bytes == 19999 * 300);
    CHECK(after == before);
}

namespace {

// IDB with an if_tsresol byte (raw, so the binary 0x80 bit can be set) and
// an optional if_tsoffset in seconds.
Bytes idb_opts(std::uint8_t tsresol_byte, bool with_offset, std::int64_t offset_s) {
    Bytes body;
    put_le16(body, 1);  // Ethernet
    put_le16(body, 0);
    put_le32(body, 262144);
    put_le16(body, 9);  // if_tsresol
    put_le16(body, 1);
    body.push_back(tsresol_byte);
    pad4(body);
    if (with_offset) {
        put_le16(body, 14);  // if_tsoffset
        put_le16(body, 8);
        put_le64(body, static_cast<std::uint64_t>(offset_s));
    }
    put_le16(body, 0);
    put_le16(body, 0);
    return block(1, body);
}

Bytes one_packet(const Bytes& idb_block, std::uint64_t ts) {
    Bytes b = shb();
    append(b, idb_block);
    append(b, epb(ts, udp_frame(bytes_of("p"))));
    return b;
}

}  // namespace

TEST_CASE("pcapng: binary if_tsresol (2^-exp seconds)") {
    TempFile f(".pcapng");
    // 2^-10 s per unit: 3 * 2^10 units = 3 s; 1 unit = 976562.5 ns -> 976562.
    Bytes b = shb();
    append(b, idb_opts(0x80 | 10, false, 0));
    append(b, epb(3 * 1024, udp_frame(bytes_of("a"))));
    append(b, epb(1, udp_frame(bytes_of("b"))));
    write_raw(f.path, b);
    auto got = read_all(f.path);
    REQUIRE(got.size() == 2);
    CHECK(got[0].ts_ns == 3'000'000'000LL);
    CHECK(got[1].ts_ns == 976'562LL);
}

TEST_CASE("pcapng: if_tsoffset is added in seconds, positive and negative") {
    TempFile pos(".pcapng"), neg(".pcapng");
    write_raw(pos.path, one_packet(idb_opts(6, true, 1'700'000'000), 5));  // 5 us after the offset
    write_raw(neg.path, one_packet(idb_opts(9, true, -10), 20'000'000'000ULL));
    auto a = read_all(pos.path);
    auto b = read_all(neg.path);
    REQUIRE(a.size() == 1);
    REQUIRE(b.size() == 1);
    CHECK(a[0].ts_ns == 1'700'000'000'000'005'000LL);
    CHECK(b[0].ts_ns == 10'000'000'000LL);
}

TEST_CASE("pcapng: timestamp overflow from the file throws instead of wrapping") {
    SECTION("if_tsoffset seconds * 1e9 overflows int64") {
        TempFile f(".pcapng");
        write_raw(f.path, one_packet(idb_opts(6, true, 10'000'000'000LL), 1));
        CHECK_THAT(drain_error(f.path), ContainsSubstring("if_tsoffset seconds to ns"));
    }
    SECTION("negative if_tsoffset overflows int64") {
        TempFile f(".pcapng");
        write_raw(f.path, one_packet(idb_opts(6, true, -10'000'000'000LL), 1));
        CHECK_THAT(drain_error(f.path), ContainsSubstring("if_tsoffset seconds to ns"));
    }
    SECTION("timestamp + if_tsoffset overflows int64") {
        TempFile f(".pcapng");
        // 9.2e9 s * 1e9 fits; adding a ~9.0e18 ns timestamp does not.
        write_raw(f.path, one_packet(idb_opts(9, true, 9'000'000'000LL), 9'000'000'000'000'000'000ULL));
        CHECK_THAT(drain_error(f.path), ContainsSubstring("timestamp plus if_tsoffset"));
    }
    SECTION("decimal if_tsresol scale overflows") {
        TempFile f(".pcapng");
        // 10^0 s per unit (exp 0): ts * 10^9 past 2^64.
        write_raw(f.path, one_packet(idb_opts(0, false, 0), 1ULL << 62));
        CHECK_THAT(drain_error(f.path), ContainsSubstring("if_tsresol scale"));
    }
    SECTION("decimal if_tsresol result past int64") {
        TempFile f(".pcapng");
        // ns resolution, raw ts above INT64_MAX: fits u64, not int64.
        write_raw(f.path, one_packet(idb_opts(9, false, 0), ~std::uint64_t{0}));
        CHECK_THAT(drain_error(f.path), ContainsSubstring("if_tsresol scale"));
    }
    SECTION("binary if_tsresol result past int64") {
        TempFile f(".pcapng");
        // 2^-1 s per unit: 2^62 units = 2^61 s, far past int64 ns.
        write_raw(f.path, one_packet(idb_opts(0x80 | 1, false, 0), 1ULL << 62));
        CHECK_THAT(drain_error(f.path), ContainsSubstring("binary if_tsresol"));
    }
}

TEST_CASE("pcapng: Simple Packet Blocks are skipped and counted") {
    Bytes b = shb();
    append(b, idb());
    append(b, epb(1, udp_frame(bytes_of("one"))));
    Bytes spb_body;
    put_le32(spb_body, 60);  // original packet length
    append(spb_body, udp_frame(bytes_of("simple")));
    append(b, block(3, spb_body));
    append(b, block(3, spb_body));
    append(b, epb(2, udp_frame(bytes_of("two"))));
    TempFile f(".pcapng");
    write_raw(f.path, b);
    PcapngReader r(f.path);
    auto got = read_all(r);
    REQUIRE(got.size() == 2);
    CHECK(got[0].payload == "one");
    CHECK(got[1].payload == "two");
    CHECK(r.skipped_simple_packets() == 2);
    CHECK(r.skipped_frames() == 0);
    CHECK(r.bytes_consumed() == b.size());
}
