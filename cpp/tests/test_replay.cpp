#include <catch2/catch_test_macros.hpp>
#include <cmath>
#include <cstring>
#include <cstdint>
#include <limits>
#include <sstream>
#include <string>
#include <vector>

#include "pkmn_wire/decoder.hpp"
#include "pkmn_wire/encoder.hpp"
#include "pkmn_wire/replay.hpp"

using namespace pkmn;
using namespace pkmn::wire;

namespace {
CostModel make_cm() {
    CostModel cm;
    cm.impact_enabled = true;
    return cm;
}
std::uint64_t digest_of(const DayQuote& q) {
    book::OrderBook b;
    build_day_ladder(q, make_cm(), LadderPricing::Cent, b);
    return ladder_digest(b);
}
const double NaN = std::numeric_limits<double>::quiet_NaN();
}  // namespace

TEST_CASE("replay: digest of an empty book is fixed, and sides are tagged", "[replay]") {
    book::OrderBook empty;
    // FNV-1a 64 of the two bytes 'S','B'.
    std::uint64_t h = 0xcbf29ce484222325ULL;
    for (unsigned char c : {'S', 'B'}) {
        h ^= c;
        h *= 0x100000001b3ULL;
    }
    CHECK(ladder_digest(empty) == h);

    // The same single level on the other side hashes differently.
    book::OrderBook ask, bid;
    ask.add(1, book::Side::Sell, 100, 1);
    bid.add(1, book::Side::Buy, 100, 1);
    CHECK(ladder_digest(ask) != ladder_digest(bid));
}

TEST_CASE("replay: digest sees price, qty and order count", "[replay]") {
    book::OrderBook a, b, c, d;
    a.add(1, book::Side::Sell, 100, 2);
    b.add(1, book::Side::Sell, 101, 2);
    c.add(1, book::Side::Sell, 100, 3);
    d.add(1, book::Side::Sell, 100, 1);
    d.add(2, book::Side::Sell, 100, 1);  // same level qty as `a`, two orders
    CHECK(ladder_digest(a) != ladder_digest(b));
    CHECK(ladder_digest(a) != ladder_digest(c));
    CHECK(ladder_digest(a) != ladder_digest(d));
    CHECK(digest_of(DayQuote{12, 13, 11}) != digest_of(DayQuote{12, 13.01, 11}));
    CHECK(digest_of(DayQuote{12, 13, 11}) == digest_of(DayQuote{12, 13, 11}));
}

TEST_CASE("replay: cents map to the backtester's doubles", "[replay]") {
    CHECK(cents_to_dollars(1250) == 12.5);
    CHECK(cents_to_dollars(29) == 0.29);  // the same double the literal 0.29 parses to
    CHECK(std::isnan(cents_to_dollars(kNull)));
}

TEST_CASE("replay: decoded quotes build the same books as direct doubles", "[replay]") {
    std::vector<std::int64_t> pid{7, 7, 9};
    std::vector<std::string> sub{"Holofoil", "Normal", "Normal"};
    std::vector<std::int32_t> day{20000, 20000, 20000, 20001};
    std::vector<std::uint32_t> inst{0, 1, 2, 2};
    std::vector<std::uint32_t> mk{2995, 317, kNull, 25000};
    std::vector<std::uint32_t> lo{2750, kNull, 100, 30000};
    std::vector<std::uint32_t> mid{kNull, 349, 200, 27500};
    std::vector<std::uint32_t> hi{4100, 410, 300, kNull};
    std::ostringstream os;
    encode(EncodeInput{pid, sub, day, inst, mk, lo, mid, hi}, os);
    const std::string s = os.str();

    struct Got {
        std::int32_t day;
        std::uint32_t inst;
        std::uint64_t digest;
    };
    std::vector<Got> got;
    auto on = [&](std::int32_t d, std::uint32_t i, const book::OrderBook& b) {
        got.push_back({d, i, ladder_digest(b)});
    };
    LadderReplay<decltype(on)> rep(make_cm(), LadderPricing::Cent, true, on);
    Decoder dec;
    std::vector<std::byte> bytes(s.size());
    std::memcpy(bytes.data(), s.data(), s.size());
    dec.packet(bytes, rep);

    REQUIRE(rep.instruments().size() == 3);
    CHECK(rep.instruments()[0].product_id == 7);
    CHECK(rep.instruments()[0].sub_type == "Holofoil");
    CHECK(rep.quotes() == 4);
    CHECK(rep.days() == 2);
    CHECK(rep.null_market() == 1);
    CHECK(rep.ladders_built() == 3);
    REQUIRE(got.size() == 3);
    CHECK(got[0].day == 20000);
    CHECK(got[0].inst == 0);
    CHECK(got[0].digest == digest_of(DayQuote{29.95, NaN, 27.50}));
    CHECK(got[1].digest == digest_of(DayQuote{3.17, 3.49, NaN}));
    CHECK(got[2].day == 20001);
    CHECK(got[2].inst == 2);
    CHECK(got[2].digest == digest_of(DayQuote{250.00, 275.00, 300.00}));

    // Ladders off: same counts, nothing built.
    std::size_t calls = 0;
    auto none = [&](std::int32_t, std::uint32_t, const book::OrderBook&) { ++calls; };
    LadderReplay<decltype(none)> off(make_cm(), LadderPricing::Cent, false, none);
    Decoder dec2;
    dec2.packet(bytes, off);
    CHECK(off.quotes() == 4);
    CHECK(off.ladders_built() == 0);
    CHECK(calls == 0);
}
