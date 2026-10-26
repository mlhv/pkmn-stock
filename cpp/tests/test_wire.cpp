#include <catch2/catch_test_macros.hpp>
#include <algorithm>
#include <streambuf>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

#include "pkmn_wire/decoder.hpp"
#include "pkmn_wire/encoder.hpp"

using namespace pkmn::wire;

namespace {

struct Ev {
    WireEvent::Kind kind;
    std::uint32_t instrument, market, low, mid, high, price, qty, quote_count;
    std::int32_t day;
    std::int64_t product_id, ts_ns;
    std::string sub_type;
    bool operator==(const Ev&) const = default;
};

Ev copy(const WireEvent& e) {
    return Ev{e.kind,  e.instrument, e.market, e.low,        e.mid,        e.high, e.price,
              e.qty,   e.quote_count, e.day,   e.product_id, e.ts_ns,     std::string(e.sub_type)};
}

std::vector<std::byte> as_bytes(const std::string& s) {
    std::vector<std::byte> v(s.size());
    std::memcpy(v.data(), s.data(), s.size());
    return v;
}

std::vector<Ev> decode_all(Decoder& d, const std::string& s) {
    std::vector<Ev> out;
    auto b = as_bytes(s);
    d.packet(b, [&](const WireEvent& e) { out.push_back(copy(e)); });
    return out;
}

// Splits an encoded stream into packets using only the header.
std::vector<std::string> split_packets(const std::string& s) {
    std::vector<std::string> out;
    std::size_t pos = 0;
    while (pos < s.size()) {
        std::uint16_t pl;
        std::memcpy(&pl, s.data() + pos + 6, 2);
        out.push_back(s.substr(pos, kHeaderBytes + pl));
        pos += kHeaderBytes + pl;
    }
    return out;
}

void le(std::string& s, std::uint64_t v, int n) {
    for (int i = 0; i < n; ++i) s.push_back(static_cast<char>(v >> (8 * i)));
}

std::string packet(std::int64_t seq, std::uint16_t count, const std::string& payload) {
    std::string s("PKW1");
    le(s, count, 2);
    le(s, payload.size(), 2);
    le(s, static_cast<std::uint64_t>(seq), 8);
    return s + payload;
}

std::string quote_msg(std::uint32_t inst, std::uint32_t m, std::uint32_t l, std::uint32_t mi, std::uint32_t h,
                      std::size_t extra = 0) {
    std::string s;
    le(s, kLenQuote + extra, 2);
    s.push_back('Q');
    le(s, inst, 4);
    le(s, m, 4);
    le(s, l, 4);
    le(s, mi, 4);
    le(s, h, 4);
    s.append(extra, '\xAA');
    return s;
}

struct Fixture {
    std::vector<std::int64_t> pid{100, 100, 200};
    std::vector<std::string> sub{"1st Edition Holofoil", "Normal", ""};
    std::vector<std::int32_t> day{19000, 19000, 19000, 19001, 19001, 19001};
    std::vector<std::uint32_t> inst{0, 1, 2, 0, 1, 2};
    std::vector<std::uint32_t> mk{1000, 250, 99999, 1100, 260, 99000};
    std::vector<std::uint32_t> lo{900, kNull, 90000, 1000, 250, 90000};
    std::vector<std::uint32_t> mid{1200, 300, 100000, 1250, 310, 100000};
    std::vector<std::uint32_t> hi{2000, 400, 120000, 2000, 400, 120000};
    EncodeInput in() const { return {pid, sub, day, inst, mk, lo, mid, hi}; }
};

}  // namespace

TEST_CASE("wire: round trip", "[wire]") {
    Fixture f;
    std::ostringstream os;
    const auto n = encode(f.in(), os);
    CHECK(n == 3 + 2 * (1 + 3 + 1));
    Decoder d;
    std::vector<Ev> got;
    for (const auto& p : split_packets(os.str())) {
        auto v = decode_all(d, p);
        got.insert(got.end(), v.begin(), v.end());
    }
    REQUIRE(got.size() == n);
    for (std::uint32_t i = 0; i < 3; ++i) {
        CHECK(got[i].kind == WireEvent::Kind::Instrument);
        CHECK(got[i].instrument == i);
        CHECK(got[i].product_id == f.pid[i]);
        CHECK(got[i].sub_type == f.sub[i]);
    }
    CHECK(got[0].sub_type.size() == 20);
    std::size_t k = 3;
    for (int day = 0; day < 2; ++day) {
        CHECK(got[k].kind == WireEvent::Kind::DayStart);
        CHECK(got[k].day == f.day[day * 3]);
        ++k;
        for (int i = 0; i < 3; ++i, ++k) {
            const int r = day * 3 + i;
            CHECK(got[k].kind == WireEvent::Kind::Quote);
            CHECK(got[k].instrument == f.inst[r]);
            CHECK(got[k].market == f.mk[r]);
            CHECK(got[k].low == f.lo[r]);
            CHECK(got[k].mid == f.mid[r]);
            CHECK(got[k].high == f.hi[r]);
        }
        CHECK(got[k].kind == WireEvent::Kind::DayEnd);
        CHECK(got[k].day == f.day[day * 3]);
        CHECK(got[k].quote_count == 3);
        ++k;
    }
    CHECK(got[4].low == 900);    // events: D,D,D, S, Q0, Q1, Q2, E, ...
    CHECK(got[5].low == kNull);  // the null low (instrument 1, first day)
    const auto& s = d.stats();
    CHECK(s.gaps == 0);
    CHECK(s.duplicates == 0);
    CHECK(s.unknown == 0);
    CHECK(s.short_messages == 0);
    CHECK(s.short_packets == 0);
    CHECK(s.messages == n);
}

TEST_CASE("wire: packets stay under 1400 bytes", "[wire]") {
    const int n = 500;
    std::vector<std::int64_t> pid;
    std::vector<std::string> sub;
    std::vector<std::int32_t> day;
    std::vector<std::uint32_t> inst, v;
    for (int i = 0; i < n; ++i) {
        pid.push_back(i + 1);
        sub.push_back("Normal");
        day.push_back(20000);
        inst.push_back(static_cast<std::uint32_t>(i));
        v.push_back(100 + i);
    }
    std::ostringstream os;
    const auto count = encode({pid, sub, day, inst, v, v, v, v}, os);
    CHECK(count == 500 + 1 + 500 + 1);
    auto pk = split_packets(os.str());
    CHECK(pk.size() > 1);
    std::int64_t expect = 1;
    for (const auto& p : pk) {
        CHECK(p.size() <= kMaxPacketBytes);
        std::uint16_t mc, pl;
        std::int64_t seq;
        std::memcpy(&mc, p.data() + 4, 2);
        std::memcpy(&pl, p.data() + 6, 2);
        std::memcpy(&seq, p.data() + 8, 8);
        CHECK(16 + pl == p.size());
        CHECK(seq == expect);
        expect += mc;
    }
    CHECK(expect - 1 == static_cast<std::int64_t>(count));
    Decoder d;
    std::size_t events = 0;
    for (const auto& p : pk) events += decode_all(d, p).size();
    CHECK(events == count);
    CHECK(d.stats().gaps == 0);
}

TEST_CASE("wire: unknown type skipped, longer message accepted", "[wire]") {
    std::string pl;
    le(pl, 7, 2);
    pl.push_back('Z');
    pl.append(6, '\x01');
    pl += quote_msg(5, 111, 222, 333, 444, 4);
    Decoder d;
    auto got = decode_all(d, packet(1, 2, pl));
    REQUIRE(got.size() == 1);
    CHECK(got[0].kind == WireEvent::Kind::Quote);
    CHECK(got[0].instrument == 5);
    CHECK(got[0].market == 111);
    CHECK(got[0].low == 222);
    CHECK(got[0].mid == 333);
    CHECK(got[0].high == 444);
    CHECK(d.stats().unknown == 1);
    CHECK(d.stats().short_messages == 0);
}

TEST_CASE("wire: truncated packet counted, no overread", "[wire]") {
    std::string pl = quote_msg(1, 10, 20, 30, 40) + quote_msg(2, 11, 21, 31, 41);
    std::string full = packet(1, 2, pl);
    // Allocate exactly the cut size so ASan sees any overread.
    const std::size_t cut = full.size() - 7;
    std::vector<std::byte> exact(cut);
    std::memcpy(exact.data(), full.data(), cut);
    Decoder d;
    std::vector<Ev> got;
    d.packet(exact, [&](const WireEvent& e) { got.push_back(copy(e)); });
    REQUIRE(got.size() == 1);
    CHECK(got[0].instrument == 1);
    CHECK(d.stats().short_messages == 1);
    CHECK(d.stats().short_packets == 1);

    // Header-only and sub-header inputs.
    Decoder d2;
    std::vector<std::byte> tiny(5);
    d2.packet(tiny, [](const WireEvent&) { FAIL("no delivery"); });
    CHECK(d2.stats().short_packets == 1);
    // msg_count promises more messages than the payload holds.
    Decoder d3;
    auto g3 = decode_all(d3, packet(1, 3, quote_msg(1, 1, 1, 1, 1)));
    CHECK(g3.size() == 1);
    CHECK(d3.stats().short_messages == 1);
}

TEST_CASE("wire: bad magic and bad first_seq deliver nothing", "[wire]") {
    std::string p = packet(1, 1, quote_msg(1, 1, 1, 1, 1));
    p[0] = 'X';
    Decoder d;
    CHECK(decode_all(d, p).empty());
    CHECK(d.stats().bad_magic == 1);
    CHECK(d.stats().packets == 0);
    CHECK(decode_all(d, packet(0, 1, quote_msg(1, 1, 1, 1, 1))).empty());
    CHECK(d.stats().short_packets == 1);
}

TEST_CASE("wire: gap detected", "[wire]") {
    std::string stream;
    for (int i = 0; i < 4; ++i) stream += packet(1 + 2 * i, 2, quote_msg(i, 1, 1, 1, 1) + quote_msg(i, 2, 2, 2, 2));
    auto pk = split_packets(stream);
    REQUIRE(pk.size() == 4);
    Decoder d;
    std::size_t events = 0;
    for (int i : {0, 2, 3}) events += decode_all(d, pk[i]).size();
    CHECK(events == 6);
    CHECK(d.stats().gaps == 1);
    CHECK(d.stats().gap_messages == 2);
}

TEST_CASE("wire: duplicate packet not redelivered", "[wire]") {
    std::string p = packet(1, 1, quote_msg(1, 1, 1, 1, 1));
    Decoder d;
    CHECK(decode_all(d, p).size() == 1);
    CHECK(decode_all(d, p).empty());
    CHECK(d.stats().duplicates == 1);
}

TEST_CASE("wire: trade message decodes", "[wire]") {
    std::string m;
    le(m, kLenTrade, 2);
    m.push_back('T');
    le(m, 9, 4);
    le(m, 1234, 4);
    le(m, 7, 4);
    le(m, static_cast<std::uint64_t>(-5), 8);
    Decoder d;
    auto got = decode_all(d, packet(1, 1, m));
    REQUIRE(got.size() == 1);
    CHECK(got[0].kind == WireEvent::Kind::Trade);
    CHECK(got[0].instrument == 9);
    CHECK(got[0].price == 1234);
    CHECK(got[0].qty == 7);
    CHECK(got[0].ts_ns == -5);
}

TEST_CASE("wire: encoder rejects unsorted rows", "[wire]") {
    Fixture f;
    std::swap(f.day[0], f.day[3]);
    std::ostringstream os;
    CHECK_THROWS_AS(encode(f.in(), os), std::invalid_argument);
    CHECK(os.str().empty());

    Fixture g;
    std::swap(g.inst[0], g.inst[1]);
    CHECK_THROWS_AS(encode(g.in(), os), std::invalid_argument);

    Fixture h;
    h.sub[0] = std::string(25, 'x');
    CHECK_THROWS_AS(encode(h.in(), os), std::invalid_argument);
}

TEST_CASE("wire: decode_file streams a file, truncated tail counted", "[wire]") {
    Fixture f;
    std::ostringstream os;
    const auto n = encode(f.in(), os);
    const std::string path = "wire_test_tmp.bin";
    {
        std::ofstream o(path, std::ios::binary);
        o << os.str();
    }
    std::size_t events = 0;
    auto st = decode_file(path, [&](const WireEvent&) { ++events; });
    CHECK(events == n);
    CHECK(st.short_packets == 0);
    {
        std::ofstream o(path, std::ios::binary);
        o << os.str().substr(0, os.str().size() - 3);
    }
    events = 0;
    st = decode_file(path, [&](const WireEvent&) { ++events; });
    CHECK(st.short_packets == 1);
    CHECK(events < n);
    std::remove(path.c_str());
    CHECK_THROWS_AS(decode_file("no/such/file.bin", [](const WireEvent&) {}), std::runtime_error);
}

namespace {
// A streambuf that accepts `limit` bytes and then refuses (disk full).
class FullBuf : public std::streambuf {
  public:
    explicit FullBuf(std::size_t limit) : limit_(limit) {}

  protected:
    int_type overflow(int_type c) override {
        if (n_ >= limit_ || traits_type::eq_int_type(c, traits_type::eof())) return traits_type::eof();
        ++n_;
        return c;
    }
    std::streamsize xsputn(const char*, std::streamsize n) override {
        const auto room = static_cast<std::streamsize>(limit_ - n_);
        const auto took = std::min(room, n);
        n_ += static_cast<std::size_t>(took);
        return took;
    }

  private:
    std::size_t limit_, n_ = 0;
};
}  // namespace

TEST_CASE("wire: encoder throws on a failed output stream", "[wire]") {
    Fixture f;
    FullBuf full(20);  // less than one packet
    std::ostream os(&full);
    CHECK_THROWS_AS(encode(f.in(), os), std::runtime_error);

    std::ostringstream bad;
    bad.setstate(std::ios::badbit);
    CHECK_THROWS_AS(encode(f.in(), bad), std::runtime_error);
}
